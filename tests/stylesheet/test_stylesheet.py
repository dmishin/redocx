from collections.abc import Callable, Mapping
from pathlib import Path

import pytest

from redocx import ir
from redocx.lower.builtins import BUILTIN_STYLES
from redocx.ooxml.writer import to_docx_bytes
from redocx.props import cell_props, para_props, run_props
from redocx.stylesheet import CompiledStyles, compile_stylesheets, parse_stylesheet
from redocx.stylesheet.collect import document_styles
from redocx.stylesheet.syntax import Block, Declaration, StyleRule
from redocx.syntax import Source, parse
from redocx.syntax.diagnostics import Code
from redocx.values import Border, Color, Length, LineSpacing

EXAMPLES = Path(__file__).resolve().parents[2] / "examples"


def compile_text(
    text: str, builtins: Mapping[str, Callable[[], ir.Style]] | None = None
) -> CompiledStyles:
    return compile_stylesheets([parse_stylesheet(Source("t.rdxs", text))], builtins)


def compiled_ok(text: str) -> CompiledStyles:
    result = compile_text(text)
    assert result.ok, result.render_diagnostics()
    return result


def style(result: CompiledStyles, name: str) -> ir.Style:
    return next(s for s in result.styles if s.name == name)


# --- syntax -------------------------------------------------------------------------


def test_parse_rule_structure() -> None:
    sheet = parse_stylesheet(
        Source(
            "t",
            'modify paragraph "Body Text" : Normal {\n  size: 11pt; % note\n  bold;\n  level 1 { x: "a;b"; }\n}',
        )
    )
    assert sheet.diagnostics == ()
    (rule,) = sheet.rules
    assert isinstance(rule, StyleRule)
    assert (rule.action, rule.kind, rule.name.text) == ("modify", "paragraph", "Body Text")
    assert rule.based_on is not None
    assert rule.based_on.text == "Normal"
    size, bold, level = rule.items
    assert isinstance(size, Declaration)
    assert (size.key, size.value) == ("size", "11pt")
    assert isinstance(bold, Declaration)
    assert bold.value is None
    assert isinstance(level, Block)
    assert (level.name, level.args) == ("level", ("1",))
    (inner,) = level.items
    assert isinstance(inner, Declaration)
    assert inner.value == '"a;b"'


def test_values_keep_parentheses_and_percent() -> None:
    sheet = parse_stylesheet(
        Source(
            "t", "paragraph X { color: theme.accent1.darker(25%); indent: (left=1cm, right=2cm) }"
        )
    )
    assert sheet.diagnostics == ()
    rule = sheet.rules[0]
    assert isinstance(rule, StyleRule)
    values = [item.value for item in rule.items if isinstance(item, Declaration)]
    assert values == ["theme.accent1.darker(25%)", "(left=1cm, right=2cm)"]


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("paragraph {", "expected a paragraph style name"),
        ("paragraph X { size 11pt; }", "expected `:` after `size`"),
        ("paragraph X { size: ; }", "`size` has no value"),
        ("paragraph X { size: 1pt;", "unclosed `{`"),
        ("bogus X { }", "unknown rule `bogus`"),
        ('paragraph "X { }', "unclosed string"),
        ("modify numbering X { }", "expected a style kind after `modify`"),
    ],
)
def test_syntax_errors(text: str, message: str) -> None:
    sheet = parse_stylesheet(Source("t", text))
    assert any(d.message == message for d in sheet.diagnostics), [
        d.message for d in sheet.diagnostics
    ]
    assert all(d.code is Code.STYLESHEET_SYNTAX for d in sheet.diagnostics)


def test_recovery_continues_after_errors() -> None:
    result = compile_text("paragraph A { size 11pt; bold; }\ncharacter B { italic; }")
    assert [d.code for _, d in result.diagnostics] == [Code.STYLESHEET_SYNTAX]
    assert style(result, "A").run == run_props(bold=True)
    assert style(result, "B").run == run_props(italic=True)


# --- compilation ----------------------------------------------------------------------


def test_properties_are_split_by_target() -> None:
    result = compiled_ok(
        'paragraph "Body Text" { size: 10.5pt; justify; space-after: 6pt; keep-with-next; }'
    )
    body = style(result, "Body Text")
    assert body.kind is ir.StyleKind.PARAGRAPH
    assert body.based_on == "Normal"
    assert body.run == run_props(size="10.5pt")
    assert body.paragraph == para_props(align="justify", space_after="6pt", keep_with_next=True)


def test_defaults_meta_and_composites() -> None:
    result = compiled_ok(
        "defaults { font: theme.minor; size: 11pt; space-after: 8pt; }\n"
        "paragraph Code : Normal { line-spacing: (12pt, exact); indent: 1cm hanging; next: Normal; ui-priority: 5; quick-format: false; }"
    )
    assert result.default_run == run_props(font="theme.minor", size="11pt")
    assert result.default_paragraph == para_props(space_after="8pt")
    code = style(result, "Code")
    assert code.paragraph["line-spacing"] == LineSpacing.exact(Length.pt(12))
    assert code.paragraph["indent-left"] == Length.cm(1)
    assert code.paragraph["hanging-indent"] == Length.cm(1)
    assert (code.next, code.ui_priority, code.quick_format) == ("Normal", 5, False)


def test_modify_merges_and_replace_resets() -> None:
    result = compiled_ok(
        "paragraph A { bold; size: 12pt; first-line-indent: 1cm; }\n"
        "modify paragraph A { size: 14pt; hanging-indent: 0.5cm; }\n"
        "paragraph B { bold; }\n"
        "replace paragraph B { italic; }"
    )
    a = style(result, "A")
    assert a.run == run_props(bold=True, size="14pt")
    assert a.paragraph == para_props(hanging_indent="0.5cm")
    assert style(result, "B").run == run_props(italic=True)


def test_linked_style_creates_char_style() -> None:
    result = compiled_ok("linked Quote : Normal { italic; indent: (left=1cm, right=1cm); }")
    quote, char = style(result, "Quote"), style(result, "Quote Char")
    assert (quote.kind, quote.link) == (ir.StyleKind.PARAGRAPH, "Quote Char")
    assert (char.kind, char.link) == (ir.StyleKind.CHARACTER, "Quote")
    assert char.run == run_props(italic=True)
    assert quote.paragraph == para_props(indent_left="1cm", indent_right="1cm")


def test_numbering_levels() -> None:
    result = compiled_ok(
        'paragraph "Heading 1" { numbering: Headings level 1; }\n'
        "numbering Headings {\n"
        '  level 1 { format: decimal; text: "%1."; indent: 0.75cm hanging; bold; }\n'
        '  level 2 { format: lower-letter; text: "(%2)"; start: 3; legal; suffix: space; restart-after: 0; }\n'
        "}\n"
        'numbering Bullets { level 1 { bullet: "☐"; font: "Segoe UI Symbol"; } }'
    )
    headings = next(n for n in result.numbering if n.name == "Headings")
    first, second = headings.levels
    assert (first.format, first.text, first.run) == ("decimal", "%1.", run_props(bold=True))
    assert first.paragraph == para_props(indent_left="0.75cm", hanging_indent="0.75cm")
    assert (second.format, second.start, second.legal, second.suffix, second.restart_after) == (
        "lowerLetter", 3, True, "space", 0,
    )  # fmt: skip
    bullets = next(n for n in result.numbering if n.name == "Bullets")
    assert (bullets.levels[0].format, bullets.levels[0].text) == ("bullet", "☐")
    assert style(result, "Heading 1").numbering == ir.NumberingRef("Headings", 1)


@pytest.mark.parametrize(
    ("text", "code", "marked", "message"),
    [
        ("paragraph X { siez: 11pt; }", Code.UNKNOWN_PROPERTY, "siez", "unknown property `siez`"),
        ("paragraph X { size: big; }", Code.INVALID_VALUE, "big", "size: not a length"),
        ("character X { space-before: 6pt; }", Code.UNKNOWN_PROPERTY, "space-before",
         "`space-before` is a paragraph property and is not allowed here"),
        ("paragraph X { }\nparagraph X { }", Code.STYLE_DEFINITION, "X", "style `X` is already defined"),
        ("paragraph X : Missing { }", Code.STYLE_DEFINITION, "Missing", "is not defined"),
        ('paragraph X { numbering: Nope level 2; }', Code.STYLE_DEFINITION, "Nope level 2", "is not defined"),
        ('numbering N { level 2 { text: "%3"; } }', Code.INVALID_VALUE, "level 2", "refers to a deeper level"),
        ("numbering N { level 1 { format: fancy; } }", Code.INVALID_VALUE, "fancy", "unknown number format"),
        ("numbering N { size: 1pt; }", Code.STYLESHEET_SYNTAX, "size: 1pt", "level N { ... }"),
        ('numbering N { level 1 { style: "Nope"; } }', Code.STYLE_DEFINITION, '"Nope"', "is not defined"),
        ("character Normal { }", Code.STYLE_DEFINITION, "Normal", "Normal must be a paragraph style"),
    ],
)  # fmt: skip
def test_compile_errors(text: str, code: Code, marked: str, message: str) -> None:
    result = compile_text(text)
    matching = [(s, d) for s, d in result.diagnostics if d.code is code]
    assert matching, result.render_diagnostics()
    source, diagnostic = matching[0]
    assert source.slice(diagnostic.span) == marked
    assert message in diagnostic.message


def test_suggestion_for_unknown_property() -> None:
    result = compile_text("paragraph X { space-befor: 6pt; }")
    ((_, diagnostic),) = result.diagnostics
    assert diagnostic.label == "did you mean `space-before`?"


def test_table_styles() -> None:
    result = compile_text(
        'table "Data Grid" : "Table Grid" {\n'
        "  borders: (all=(single, 0.5pt, #BFBFBF)); cell-margins: (left=0.1cm, right=0.1cm);\n"
        "  space-after: 0pt; shading: #FAFAFA;\n"
        "  header-row { bold; shading: theme.accent1; border-bottom: (single, 1.5pt); repeat; }\n"
        "  banded-rows { odd { shading: #F2F2F2; } even { shading: #FFFFFF; } }\n"
        "  banded-columns { italic; }\n"
        "}\n"
        'modify table "Data Grid" { last-row { bold; } }',
        builtins=BUILTIN_STYLES,
    )
    assert result.ok, result.render_diagnostics()
    style, grid = result.styles
    assert grid.name == "Table Grid"  # the built-in base comes along
    assert (style.kind, style.based_on, style.repeat_header) == (
        ir.StyleKind.TABLE,
        "Table Grid",
        True,
    )
    border = Border("single", Length.pt("0.5"), Color.parse("#BFBFBF"))
    assert style.table["border-inside-h"] == border
    assert style.table["cell-margin-right"] == Length.cm("0.1")
    assert style.paragraph == para_props(space_after="0pt")
    assert style.cell == cell_props(shading="#FAFAFA")
    parts = {c.kind: c for c in style.conditions}
    assert set(parts) == {"firstRow", "band1Horz", "band2Horz", "band1Vert", "lastRow"}
    header = parts["firstRow"]
    assert header.run == run_props(bold=True)
    assert header.cell == cell_props(shading="theme.accent1", border_bottom="(single, 1.5pt)")
    assert parts["band1Vert"].run == run_props(italic=True)


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("table T { zebra { bold; } }", "unknown table part `zebra`"),
        ("table T { borders: (top=squiggle); }", "not a border part"),
        ("table T { last-row { repeat; } }", "`repeat` applies to the header row"),
        ("table T : Missing { }", "based-on style `Missing` is not defined"),
        ("paragraph P { } table T : P { }", "is not a table style"),
        ("character C { borders: none; }", "is a table property and is not allowed here"),
    ],
)
def test_table_style_errors(text: str, message: str) -> None:
    result = compile_text(text)
    assert any(message in d.message for _, d in result.diagnostics), result.render_diagnostics()


# --- documents ---------------------------------------------------------------------------


def test_styles_body_in_a_document(tmp_path: Path) -> None:
    (tmp_path / "extra.rdxs").write_text("character Term { italic; }", encoding="utf-8")
    text = (
        "\\use{extra.rdxs}\n"
        '\\styles{\n  paragraph "Body Text" { justify; }  % inline sheet\n  modify character Term { bold; }\n}\n\n'
        "Some text."
    )
    result = parse(text, name=str(tmp_path / "doc.rdx"))
    assert result.ok, result.render_diagnostics()
    compiled = document_styles(result)
    assert compiled.ok, compiled.render_diagnostics()
    assert style(compiled, "Term").run == run_props(italic=True, bold=True)
    assert style(compiled, "Body Text").paragraph == para_props(align="justify")


def test_style_sheet_errors_point_into_the_document(tmp_path: Path) -> None:
    text = "Intro.\n\n\\styles{ paragraph X { siez: 1pt; } }\n\\use{missing.rdxs}"
    result = parse(text, name=str(tmp_path / "doc.rdx"))
    compiled = document_styles(result)
    codes = {d.code: (s, d) for s, d in compiled.diagnostics}
    source, unknown = codes[Code.UNKNOWN_PROPERTY]
    assert source.slice(unknown.span) == "siez"
    assert source.position(unknown.span.start).line == 3
    source, missing = codes[Code.STYLESHEET_NOT_FOUND]
    assert source.slice(missing.span) == "\\use{missing.rdxs}"


def test_house_style_sheet_builds_a_valid_document() -> None:
    from tests.support.xsd import validate_package

    compiled = compile_stylesheets(
        [parse_stylesheet(Source.from_path(EXAMPLES / "styles" / "house.rdxs"))]
    )
    assert compiled.ok, compiled.render_diagnostics()
    document = compiled.apply(ir.Document(body=(ir.paragraph("Title", style="Heading 1"),)))
    assert validate_package(to_docx_bytes(document)) == {}
