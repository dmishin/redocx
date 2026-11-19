"""The importer's printer (M6 step 2): IR -> source with commands only.

The check for every document: the printed source compiles without errors and has the
original's text. Documents come from the Word-built corpus, from the examples (built by
redocx, then imported), and from small IR documents for the details of printing.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from redocx import ir
from redocx.cli import main
from redocx.compiler import build, compile_file
from redocx.importer import import_docx, visible_text
from redocx.importer.check import Comparison, compare
from redocx.importer.printer import display_name, print_document
from redocx.importer.values import length, line_spacing
from redocx.lower.builtins import BUILTIN_STYLES
from redocx.math.nodes import Align, Frac, Raw, Run, canonical
from redocx.math.parser import parse_math
from redocx.ooxml.writer import to_docx_bytes
from redocx.props import para_props, run_props
from redocx.syntax import Source
from redocx.values import Border, Color, Length, LineSpacing

ROOT = Path(__file__).resolve().parents[2]
CORPUS_DIR = ROOT / "tests" / "corpus"
CORPUS = sorted(CORPUS_DIR.glob("*.docx"))
EXAMPLES = [
    "examples/tables.rdx",
    "examples/index.rdx",
    "examples/citations/citations.rdx",
    "examples/forms/onboarding.rdx",
    "examples/magazine/magazine.rdx",
]


def imported(document: ir.Document, tmp_path: Path) -> tuple[str, ir.Document]:
    """The printed source of `document`, and the document it compiles to."""
    docx = tmp_path / "doc.docx"
    docx.write_bytes(to_docx_bytes(document))
    result = import_docx(docx, tmp_path / "out")
    assert result.errors == 0, (tmp_path / "out" / "doc.diagnostics.txt").read_text("utf-8")
    compiled = compile_file(result.source).document
    assert compiled is not None
    return result.source.read_text("utf-8"), compiled


@pytest.mark.parametrize("docx", CORPUS, ids=lambda p: p.stem)
def test_corpus_imports(docx: Path, tmp_path: Path) -> None:
    result = import_docx(docx, tmp_path)
    assert result.errors == 0
    assert result.difference is None
    report = (tmp_path / f"{docx.stem}.import.txt").read_text("utf-8")
    assert "The printed source compiles.\nIts text is the original's.\n" in report


@pytest.mark.parametrize("example", EXAMPLES)
def test_examples_survive_build_and_import(example: str, tmp_path: Path) -> None:
    data, _ = build(Source.from_path(ROOT / example))
    assert data is not None
    docx = tmp_path / "original.docx"
    docx.write_bytes(data)
    result = import_docx(docx, tmp_path / "import")
    assert (result.errors, result.difference) == (0, None)


def test_text_that_looks_like_markup_stays_text(tmp_path: Path) -> None:
    tricky = [
        "- not a list",
        "1. not a list either",
        "= not a heading",
        "| not a table |",
        "*not strong* and _not emphasis_ and snake_case",
        "100% sure % not a comment, {braces} [brackets] \\backslash `tick` ~tilde",
        "mail me@example.com or @not:a-ref, <https://not.a.link> and $$ 5",
        'smart "quotes" -- and --- dashes stay as typed',
    ]
    document = ir.Document(body=tuple(ir.paragraph(text) for text in tricky))
    source, compiled = imported(document, tmp_path)
    assert [p.text() for p in compiled.body if isinstance(p, ir.Paragraph)] == tricky
    assert "\\font{-} not a list" in source


def test_formatting_and_paragraph_options_round_trip(tmp_path: Path) -> None:
    paragraph = ir.Paragraph(
        (
            ir.run("plain "),
            ir.run("bold red", bold=True, color="#C00000"),
            ir.Run(" tab\tand\nbreak"),
        ),
        props=para_props(
            align="justify",
            space_before=Length.pt(6),
            indent_left=Length.cm(1),
            line_spacing=LineSpacing.exact(Length.pt(14)),
            border_top=Border("single", Length.pt(1), Color(theme="accent1")),
        ),
    )
    source, compiled = imported(ir.Document(body=(paragraph,)), tmp_path)
    (back,) = [b for b in compiled.body if isinstance(b, ir.Paragraph)]
    assert back.props == paragraph.props
    assert [(r.text, r.props) for r in back.runs()] == [
        ("plain ", run_props()),
        ("bold red", run_props(bold=True, color="#C00000")),
        (" tab\tand\nbreak", run_props()),
    ]
    assert "line-spacing=(14pt, exact)" in source
    assert "\\tab{}and\\br{}break" in source


def test_lists_print_as_named_lists(tmp_path: Path) -> None:
    numbering = ir.Numbering(
        "Steps",
        (
            ir.NumberingLevel(1, "decimal", "%1."),
            ir.NumberingLevel(2, "lowerLetter", "%2)"),
        ),
    )
    items = [("One", 1), ("One a", 2), ("Two", 1)]
    body = tuple(
        ir.Paragraph((ir.Run(text),), "List Paragraph", list_item=ir.ListItemRef("Steps", 1, level))
        for text, level in items
    )
    document = ir.Document(
        body=body, numbering=(numbering,), styles=(ir.paragraph_style("List Paragraph"),)
    )
    source, compiled = imported(document, tmp_path)
    # The writer gives each list instance its start, so the import restarts it there.
    assert "\\list[Steps, restart=1]{\n  1. One\n     1. One a\n  1. Two\n}" in source
    levels = [
        (p.text(), p.list_item.level)
        for p in compiled.body
        if isinstance(p, ir.Paragraph) and p.list_item
    ]
    assert levels == items


def test_internal_links_and_bookmarks(tmp_path: Path) -> None:
    body = (
        ir.Paragraph((ir.BookmarkStart("target"), ir.Run("Here"), ir.BookmarkEnd("target"))),
        ir.Paragraph((ir.Hyperlink("", (ir.Run("back", style="Hyperlink"),), anchor="target"),)),
    )
    hyperlink = ir.character_style("Hyperlink", underline="single")
    source, compiled = imported(ir.Document(body=body, styles=(hyperlink,)), tmp_path)
    assert '\\link[to="#target"]{back}' in source
    links = [i for p in compiled.body if isinstance(p, ir.Paragraph) for i in p.content]
    assert ir.Hyperlink("", (ir.Run("back", style="Hyperlink"),), anchor="target") in links
    assert ir.BookmarkStart("target") not in links  # raw XML keeps it, as raw XML
    assert visible_text(compiled) == "Hereback"


def test_values_are_written_in_round_units() -> None:
    assert length(Length.twips(1440), "cm") == "2.54cm"
    assert length(Length.twips(567), "cm") == "1cm"
    assert length(Length.twips(708), "cm") == "1.249cm"  # what Word stores for 1.25cm
    assert length(Length.pt(11)) == "11pt"
    assert length(Length(3008 * 635), "size") == "5.31cm"
    assert line_spacing(LineSpacing.lines("1.1583333333")) == "1.16"


def test_word_names_of_built_in_styles_are_shown_capitalised() -> None:
    assert display_name("heading 1") == "Heading 1"
    assert display_name("toc 2") == "TOC 2"
    assert display_name("annotation text") == "annotation text"  # redocx's own spelling
    assert display_name("My Style") == "My Style"


def test_the_import_command_writes_the_folder(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    folder = tmp_path / "drawings"
    assert (
        main(["import", str(ROOT / "tests" / "corpus" / "drawings.docx"), "-o", str(folder)]) == 0
    )
    assert sorted(p.name for p in folder.iterdir()) == [
        "drawings.import.txt",
        "drawings.rdx",
        "drawings.rdxs",
        "media",
    ]
    assert "The printed source compiles." in capsys.readouterr().out


def test_what_cannot_be_printed_is_counted() -> None:
    first = ir.Section(headers=(("first", (ir.paragraph("unused"),)),))
    printed = print_document(ir.Document(body=(ir.paragraph("x  y"),), section=first), "s.rdxs")
    assert printed.lost == {
        "first-page header of a section without a different first page": 1,
        "repeated spaces (collapsed to one)": 1,
    }


def test_nested_fields_print_in_field_braces(tmp_path: Path) -> None:
    outer = ir.Field(("=", ir.Field(("NUMPAGES",), "17"), "-1"), "16")
    source, compiled = imported(ir.Document(body=(ir.Paragraph((outer,)),)), tmp_path)
    assert "\\field[result=16]{={ NUMPAGES }-1}" in source
    (paragraph,) = compiled.body
    assert isinstance(paragraph, ir.Paragraph)
    (field,) = paragraph.content
    assert isinstance(field, ir.Field)
    nested = [part for part in field.instruction if isinstance(part, ir.Field)]
    assert [part.code.strip() for part in nested] == ["NUMPAGES"]
    assert " ".join(field.code.split()) == "={ NUMPAGES }-1"  # spacing aside, the same code


def test_custom_properties_round_trip(tmp_path: Path) -> None:
    custom = (
        ("project", "WX-001"),
        ("N° ISO", "A12"),
        ("count", 3),
        ("code", "7"),
        ("final", True),
    )
    source, compiled = imported(ir.Document(meta=ir.Meta(custom=custom)), tmp_path)
    assert 'custom."N° ISO"="A12"' in source
    assert compiled.meta.custom == custom


def test_the_theme_is_printed_where_it_differs(tmp_path: Path) -> None:
    theme = ir.ThemeChoice("Coastal", (("accent1", "0B7A75"),), "Georgia", None)
    _, compiled = imported(ir.Document(body=(ir.paragraph("x"),), theme=theme), tmp_path)
    sheet = (tmp_path / "out" / "doc.rdxs").read_text("utf-8")
    assert 'theme "Coastal" {\n  accent1: #0B7A75;\n  major-font: "Georgia";\n}' in sheet
    assert compiled.theme is not None
    assert ("accent1", "0B7A75") in compiled.theme.colors
    assert compiled.theme.major_font == "Georgia"


def test_built_in_styles_print_only_their_differences(tmp_path: Path) -> None:
    heading = BUILTIN_STYLES["Heading 1"]()
    changed = replace(
        heading, run=heading.run.merged(run_props(color="#C00000")), link="Heading 1 Char"
    )
    char = ir.Style(ir.StyleKind.CHARACTER, "Heading 1 Char", run=changed.run, link="Heading 1")
    body = (ir.Paragraph((ir.Run("Title"),), "Heading 1"),)
    _, compiled = imported(ir.Document(body=body, styles=(changed, char)), tmp_path)
    sheet = (tmp_path / "out" / "doc.rdxs").read_text("utf-8")
    assert 'modify linked "Heading 1" {\n  color: #C00000;\n}' in sheet
    back = next(s for s in compiled.styles if s.name == "Heading 1")
    assert back.run == changed.run
    assert back.link == "Heading 1 Char"


def test_word_legacy_defaults_are_written_out() -> None:
    """Without a font or size in the defaults Word uses Times New Roman 10pt, while redocx
    would fill in its own: the style sheet says what Word does."""
    sheet = print_document(ir.Document(body=(ir.paragraph("x"),)), "s.rdxs").stylesheet
    assert 'defaults {\n  font: "Times New Roman";\n  size: 10pt;\n}' in sheet


@pytest.mark.parametrize("docx", CORPUS, ids=lambda p: p.stem)
def test_keep_styles_compiles_into_the_original(docx: Path, tmp_path: Path) -> None:
    result = import_docx(docx, tmp_path, keep_styles=True)
    assert (result.errors, result.difference) == (0, None)
    source = result.source.read_text("utf-8")
    assert source.startswith(f'\\document[template="{docx.stem}.template.docx"')
    assert not (tmp_path / f"{docx.stem}.rdxs").exists()  # the template has the styles and lists


ALL_EXAMPLES = sorted(
    str(p.relative_to(ROOT)).replace("\\", "/")
    for p in (ROOT / "examples").rglob("*.rdx")
    if "spike" not in p.parts and p.parent.name != "chapters"
)
MODES = pytest.mark.parametrize("keep_styles", [False, True], ids=["style-sheet", "keep-styles"])


def _comparison(docx: Path, folder: Path, **options: bool) -> Comparison:
    result = import_docx(docx, folder, **options)
    assert result.errors == 0, (folder / f"{docx.stem}.diagnostics.txt").read_text("utf-8")
    assert result.comparison is not None
    return result.comparison


@MODES
@pytest.mark.parametrize("docx", CORPUS, ids=lambda p: p.stem)
def test_the_corpus_compiles_to_what_it_was(docx: Path, keep_styles: bool, tmp_path: Path) -> None:
    comparison = _comparison(docx, tmp_path, keep_styles=keep_styles)
    assert comparison.same, comparison.render()


@MODES
@pytest.mark.parametrize("example", ALL_EXAMPLES)
def test_examples_compile_to_what_they_were(
    example: str, keep_styles: bool, tmp_path: Path
) -> None:
    """.rdx -> .docx -> import -> .docx: the oracle on documents redocx wrote."""
    data, _ = build(Source.from_path(ROOT / example))
    assert data is not None
    docx = tmp_path / "original.docx"
    docx.write_bytes(data)
    comparison = _comparison(docx, tmp_path / "import", keep_styles=keep_styles)
    assert comparison.same, comparison.render()


def test_the_comparison_names_what_differs() -> None:
    heading = ir.Paragraph((ir.Run("Title"),), "Heading 1")
    body = ir.Paragraph((ir.run("Text", bold=True),))
    original = ir.Document(body=(heading, body))
    changed = ir.Document(
        body=(replace(heading, style="Heading 2"), ir.Paragraph((ir.Run("Text"),)))
    )
    comparison = compare(original, changed)
    assert not comparison.same
    assert comparison.text is None
    assert comparison.blocks == (1, 2)
    assert comparison.paragraphs == (1, 2)  # the heading looks the same either way
    assert (
        comparison.differences[0]
        == "block 1: paragraph (heading 1): style heading 1 became heading 2"
    )
    assert "paragraph 2: text formatted differently: bold" in comparison.differences
    assert "Blocks that match the original: 1 of 2." in comparison.render()


def test_the_import_command_checks_the_result(tmp_path: Path) -> None:
    docx = CORPUS_DIR / "tables.docx"
    assert main(["import", str(docx), "-o", str(tmp_path / "a"), "--check"]) == 0
    report = (tmp_path / "a" / "tables.import.txt").read_text("utf-8")
    assert "Blocks that match the original: 20 of 20." in report
    assert "Paragraphs whose text looks the same: 2 of 2." in report


def test_a_link_without_the_hyperlink_look_stays_plain(tmp_path: Path) -> None:
    """redocx's own citations link to their entries without the Hyperlink style."""
    body = (
        ir.Paragraph((ir.BookmarkStart("entry"), ir.Run("Entry"), ir.BookmarkEnd("entry"))),
        ir.Paragraph((ir.Hyperlink("", (ir.Run("(Smith, 2020)"),), anchor="entry"),)),
    )
    source, compiled = imported(ir.Document(body=body), tmp_path)
    assert '\\link[to="#entry", style=none]{(Smith, 2020)}' in source
    links = [i for p in compiled.body if isinstance(p, ir.Paragraph) for i in p.content]
    assert ir.Hyperlink("", (ir.Run("(Smith, 2020)"),), anchor="entry") in links


# --- sugar (M6 step 4) ---------------------------------------------------------------------


def _source(name: str, tmp_path: Path, **options: bool) -> str:
    return import_docx(CORPUS_DIR / f"{name}.docx", tmp_path, **options).source.read_text("utf-8")


def test_headings_captions_and_references_use_their_sugar(tmp_path: Path) -> None:
    source = _source("references", tmp_path)
    assert "= Introduction {#sec:introduction}" in source
    assert "\\caption[Figure, id=fig:the-logo]{The logo}" in source
    assert (
        "See @fig:the-logo on page \\pageref{fig:the-logo}, and the section @sec:introduction."
        in source
    )
    assert "_Ref" not in source  # the bookmarks became labels


def test_simple_formatting_and_pipe_tables_use_their_sugar(tmp_path: Path) -> None:
    text = _source("text", tmp_path / "text")
    assert "Plain, \\b{bold}, \\i{italic}, \\u{underlined}, \\s{struck}," in text
    tables = _source("tables", tmp_path / "tables")
    assert "header-rows=1]{\n  | Item | Q1 | Q2 |\n  | Revenue | 10.5 | 12.25 |\n  \\row{" in tables


def test_commands_only_prints_no_sugar(tmp_path: Path) -> None:
    source = _source("references", tmp_path, sugar=False)
    assert '\\p[style="Heading 1"]{\\raw[ooxml]{<w:bookmarkStart' in source  # no labels
    assert "= Introduction" not in source
    assert "@" not in source.replace("\\@", "")


def test_strong_and_emphasis_where_the_words_allow(tmp_path: Path) -> None:
    runs = (
        ir.Run("Very "),
        ir.Run("bold", style="Strong"),
        ir.Run(" and "),
        ir.Run("it", style="Emphasis"),
        ir.Run("alic."),
    )
    styles = (ir.character_style("Strong", bold=True), ir.character_style("Emphasis", italic=True))
    source, compiled = imported(ir.Document(body=(ir.Paragraph(runs),), styles=styles), tmp_path)
    assert "Very *bold* and \\emph{it}alic." in source  # "_it_alic" would not close
    (paragraph,) = compiled.body
    assert isinstance(paragraph, ir.Paragraph)
    assert [(r.text, r.style) for r in paragraph.runs()] == [
        ("Very ", None),
        ("bold", "Strong"),
        (" and ", None),
        ("it", "Emphasis"),
        ("alic.", None),
    ]


def test_formatting_the_style_gives_is_left_out(tmp_path: Path) -> None:
    normal = ir.paragraph_style("Normal", run=run_props(font="Arial", size="10pt"))
    runs = (ir.run("same", font="Arial"), ir.run(" bigger", font="Arial", size="12pt"))
    source, _ = imported(ir.Document(body=(ir.Paragraph(runs),), styles=(normal,)), tmp_path)
    assert "same\\font[size=12pt]{ bigger}" in source


def _equations(document: ir.Document) -> list[ir.Math]:
    return [i for p in document.all_paragraphs() for i in p.content if isinstance(i, ir.Math)]


def test_equations_print_as_latex(tmp_path: Path) -> None:
    formulas = [
        r"e^{i\pi}+1=0",
        r"\frac{-b\pm\sqrt{b^2-4ac}}{2a}",
        r"\sum_{i=1}^n{i^2}+\begin{pmatrix}a & b\end{pmatrix}",
    ]
    inline = ir.Math(parse_math(formulas[0]))
    displays = [ir.Math(parse_math(f), display=True) for f in formulas[1:]]
    pre = Raw("<m:sPre><m:sub/><m:sup/><m:e><m:r><m:t>C</m:t></m:r></m:e></m:sPre>")
    body = (
        ir.Paragraph((ir.Run("Euler: "), inline, ir.Run("."))),
        *(ir.Paragraph((d,)) for d in displays),
        ir.Paragraph((ir.Math((Run("a"), pre, Frac((Run("1"),), (Run("2"),), "lin"))),)),
    )
    source, compiled = imported(ir.Document(body=body), tmp_path)
    assert f"Euler: \\m{{{formulas[0]}}}." in source
    assert f"$$ {formulas[1]} $$" in source
    assert f"$$ {formulas[2]} $$" in source
    assert "\\m{a\\ooxml{<m:sPre>" in source
    assert '\\ooxml{<m:f><m:fPr><m:type m:val="lin"/></m:fPr>' in source
    before = _equations(ir.Document(body=body))
    after = _equations(compiled)
    assert [canonical(m.expr) for m in after[:3]] == [canonical(m.expr) for m in before[:3]]
    assert [m.display for m in after] == [False, True, True, False]


def test_numbered_and_labelled_equations(tmp_path: Path) -> None:
    source = (
        '\\settings[captions=(chapter-numbers, separator="-")]\n\n'
        "= One\n\n"
        "$$ a^2+b^2=c^2 $$ {#eq:pythagoras}\n\n"
        "\\math[numbered]{E=mc^2}\n\n"
        "$$\\begin{aligned}x&=1 \\\\ y&=2\\end{aligned}$$ {#eq:two_rows}\n\n"
        "See @eq:pythagoras and \\ref[form=number]{eq:two_rows}; (@eq:pythagoras).\n"
    )
    (tmp_path / "doc.rdx").write_text(source, "utf-8")
    data, _ = build(Source.from_path(tmp_path / "doc.rdx"))
    assert data is not None
    (tmp_path / "doc.docx").write_bytes(data)
    result = import_docx(tmp_path / "doc.docx", tmp_path / "out")
    assert (result.errors, result.difference) == (0, None)
    assert result.comparison is not None
    assert result.comparison.same, result.comparison.render()
    printed = result.source.read_text("utf-8")
    assert "\\settings[captions=(chapter-numbers, separator=-)]" in printed
    assert "$$ a^2+b^2=c^2 $$ {#eq:pythagoras}" in printed
    assert "\\math[numbered]{E=mc^2}" in printed
    assert "$$ \\begin{aligned}x&=1 \\\\ y&=2\\end{aligned} $$ {#eq:two-rows}" in printed
    assert "See @eq:pythagoras and \\ref[form=number]{eq:two-rows}; (@eq:pythagoras)." in printed


def test_an_equation_with_no_latex_form_is_raw(tmp_path: Path) -> None:
    # An alignment mark outside an equation array has no LaTeX form (read from Word it is
    # an `&` in the text, so this comes only from a tree built by hand).
    body = (ir.Paragraph((ir.Math((Run("a"), Align(), Run("b")), display=True),)),)
    printed = print_document(ir.Document(body=body), "s.rdxs")
    assert "\\raw[ooxml]{<m:oMathPara><m:oMath><m:r><m:t>a</m:t></m:r>" in printed.source
    assert printed.lost["equation (kept as OMML)"] == 1
    (tmp_path / "s.rdxs").write_text(printed.stylesheet, "utf-8")
    (tmp_path / "doc.rdx").write_text(printed.source, "utf-8")
    compiled = compile_file(tmp_path / "doc.rdx").document
    assert compiled is not None
    assert any(isinstance(i, ir.RawXml) for p in compiled.all_paragraphs() for i in p.content)
