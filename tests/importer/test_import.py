"""The importer's printer (M6 step 2): IR -> source with commands only.

The check for every document: the printed source compiles without errors and has the
original's text. Documents come from the Word-built corpus, from the examples (built by
wordgenx, then imported), and from small IR documents for the details of printing.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest

from wordgenx import ir
from wordgenx.cli import main
from wordgenx.compiler import build, compile_file
from wordgenx.importer import import_docx, visible_text
from wordgenx.importer.printer import display_name, print_document
from wordgenx.importer.values import length, line_spacing
from wordgenx.lower.builtins import BUILTIN_STYLES
from wordgenx.ooxml.reader import read_docx
from wordgenx.ooxml.style_names import stored_name
from wordgenx.ooxml.writer import to_docx_bytes
from wordgenx.props import PropertySet, Target, para_props, run_props
from wordgenx.syntax import Source
from wordgenx.values import Border, Color, Length, LineSpacing, TabStop

ROOT = Path(__file__).resolve().parents[2]
CORPUS_DIR = ROOT / "tests" / "corpus"
CORPUS = sorted(CORPUS_DIR.glob("*.docx"))
EXAMPLES = [
    "examples/tables.wgx",
    "examples/index.wgx",
    "examples/citations/citations.wgx",
    "examples/forms/onboarding.wgx",
    "examples/magazine/magazine.wgx",
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
    assert (
        (tmp_path / f"{docx.stem}.import.txt")
        .read_text("utf-8")
        .endswith("Its text is the original's.\n")
    )


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
    assert display_name("annotation text") == "annotation text"  # wordgenx's own spelling
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
        "drawings.wgx",
        "drawings.wgxs",
        "media",
    ]
    assert "The printed source compiles." in capsys.readouterr().out


def test_what_cannot_be_printed_is_counted() -> None:
    first = ir.Section(headers=(("first", (ir.paragraph("unused"),)),))
    printed = print_document(ir.Document(body=(ir.paragraph("x  y"),), section=first), "s.wgxs")
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
    sheet = (tmp_path / "out" / "doc.wgxs").read_text("utf-8")
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
    sheet = (tmp_path / "out" / "doc.wgxs").read_text("utf-8")
    assert 'modify linked "Heading 1" {\n  color: #C00000;\n}' in sheet
    back = next(s for s in compiled.styles if s.name == "Heading 1")
    assert back.run == changed.run
    assert back.link == "Heading 1 Char"


def test_word_legacy_defaults_are_written_out() -> None:
    """Without a font or size in the defaults Word uses Times New Roman 10pt, while wordgenx
    would fill in its own: the style sheet says what Word does."""
    sheet = print_document(ir.Document(body=(ir.paragraph("x"),)), "s.wgxs").stylesheet
    assert 'defaults {\n  font: "Times New Roman";\n  size: 10pt;\n}' in sheet


@pytest.mark.parametrize("docx", CORPUS, ids=lambda p: p.stem)
def test_keep_styles_compiles_into_the_original(docx: Path, tmp_path: Path) -> None:
    result = import_docx(docx, tmp_path, keep_styles=True)
    assert (result.errors, result.difference) == (0, None)
    source = result.source.read_text("utf-8")
    assert source.startswith(f'\\document[template="{docx.stem}.template.docx"')
    assert not (tmp_path / f"{docx.stem}.wgxs").exists()  # the template has the styles and lists


def _shapes(document: ir.Document) -> list[tuple[object, ...]]:
    """Each paragraph's style, formatting and list level, and each table's spans and header
    rows, in order (drop caps and generated blocks such as the TOC left out)."""
    out: list[tuple[object, ...]] = []

    def blocks(items: tuple[ir.Block, ...]) -> None:
        for block in items:
            only = (
                block.content[0]
                if isinstance(block, ir.Paragraph) and len(block.content) == 1
                else None
            )
            if isinstance(block, ir.FieldBlock) or (
                isinstance(only, ir.Field) and only.code.split()[:1] == ["INDEX"]
            ):
                out.append(("field block",))  # an INDEX block comes back as its field
            elif isinstance(block, ir.Paragraph) and block.drop_cap is None:
                style = stored_name(block.style or "Normal").lower()
                level = block.list_item.level if block.list_item else None
                out.append(("p", style, _in_twips(block.props), level))
                for item in block.content:
                    if isinstance(item, ir.Note | ir.TextBox):
                        blocks(item.content)
            elif isinstance(block, ir.Table):
                spans = [[(c.colspan, c.rowspan) for c in row.cells] for row in block.rows]
                out.append(("table", spans, [row.header for row in block.rows]))
                for row in block.rows:
                    for cell in row.cells:
                        blocks(cell.content)

    blocks(document.body)
    for section in document.sections():
        for kind, parts in (*section.headers, *section.footers):
            shown = kind == "default" or (
                section.title_page if kind == "first" else document.settings.even_and_odd_headers
            )
            if shown:  # the parts Word does not show are not imported (and reported)
                blocks(parts)
    return out


def _in_twips(props: PropertySet) -> dict[str, object]:
    """Lengths as Word stores them (twips), so 1.02cm and 578 twips are the same."""

    def value(v: object) -> object:
        if isinstance(v, Length):
            return v.in_twips
        if isinstance(v, tuple):  # tab stops
            stops = cast(tuple[TabStop, ...], v)
            return tuple((t.position.in_twips, t.align, t.leader) for t in stops)
        return v

    return {name: value(v) for name, v in props.items()}


def _looks(document: ir.Document) -> list[list[tuple[str, dict[str, object]]]]:
    """For each body paragraph: its text in pieces of one effective run formatting (the
    defaults, the paragraph style's chain, the character style's, then direct formatting),
    whitespace left out."""
    styles = {stored_name(s.name).lower(): s for s in document.styles}

    def chain(name: str | None) -> PropertySet:
        found: list[ir.Style] = []
        while name is not None and (style := styles.get(stored_name(name).lower())) is not None:
            if style in found:
                break
            found.append(style)
            name = style.based_on
        props = PropertySet(Target.RUN)
        for style in reversed(found):
            props = props.merged(style.run)
        return props

    legacy = PropertySet(Target.RUN, {"font": "Times New Roman", "size": "10pt"})
    base = legacy.merged(document.default_run)
    out: list[list[tuple[str, dict[str, object]]]] = []
    for block in document.body:
        if not isinstance(block, ir.Paragraph) or block.drop_cap is not None:
            continue
        paragraph = base.merged(chain(block.style or "Normal"))
        pieces: list[tuple[str, dict[str, object]]] = []
        for run in block.runs():
            look = paragraph.merged(chain(run.style)).merged(run.props)
            text, key = "".join(run.text.split()), _in_twips(look)
            if not text:
                continue
            if pieces and pieces[-1][1] == key:
                pieces[-1] = (pieces[-1][0] + text, key)
            else:
                pieces.append((text, key))
        if pieces:
            out.append(pieces)
    return out


def _same_shapes(docx: Path, folder: Path, **options: bool) -> None:
    result = import_docx(docx, folder, **options)
    assert (result.errors, result.difference) == (0, None)
    compiled = compile_file(result.source).document
    assert compiled is not None
    document = read_docx(docx).document
    original, rebuilt = _shapes(document), _shapes(compiled)
    for index, (a, b) in enumerate(zip(original, rebuilt, strict=False)):
        assert a == b, f"block {index}"
    assert len(original) == len(rebuilt)
    for index, (a, b) in enumerate(zip(_looks(document), _looks(compiled), strict=True)):
        assert a == b, f"paragraph {index}"


@pytest.mark.parametrize("docx", CORPUS, ids=lambda p: p.stem)
def test_sugar_keeps_the_structure(docx: Path, tmp_path: Path) -> None:
    _same_shapes(docx, tmp_path)


@pytest.mark.parametrize("example", EXAMPLES)
def test_sugar_keeps_the_structure_of_examples(example: str, tmp_path: Path) -> None:
    data, _ = build(Source.from_path(ROOT / example))
    assert data is not None
    docx = tmp_path / "original.docx"
    docx.write_bytes(data)
    _same_shapes(docx, tmp_path / "import")


def test_a_link_without_the_hyperlink_look_stays_plain(tmp_path: Path) -> None:
    """wordgenx's own citations link to their entries without the Hyperlink style."""
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
