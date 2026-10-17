"""The importer's printer (M6 step 2): IR -> source with commands only.

The check for every document: the printed source compiles without errors and has the
original's text. Documents come from the Word-built corpus, from the examples (built by
wordgenx, then imported), and from small IR documents for the details of printing.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from wordgenx import ir
from wordgenx.cli import main
from wordgenx.compiler import build, compile_file
from wordgenx.importer import import_docx, visible_text
from wordgenx.importer.printer import display_name, print_document
from wordgenx.importer.values import length, line_spacing
from wordgenx.ooxml.writer import to_docx_bytes
from wordgenx.props import para_props, run_props
from wordgenx.syntax import Source
from wordgenx.values import Border, Color, Length, LineSpacing

ROOT = Path(__file__).resolve().parents[2]
CORPUS = sorted((ROOT / "tests" / "corpus").glob("*.docx"))
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
        ir.Paragraph((ir.Hyperlink("", (ir.Run("back"),), anchor="target"),)),
    )
    source, compiled = imported(ir.Document(body=body), tmp_path)
    assert '\\link[to="#target"]{back}' in source
    links = [i for p in compiled.body if isinstance(p, ir.Paragraph) for i in p.content]
    assert ir.Hyperlink("", (ir.Run("back", style="Hyperlink"),), anchor="target") in links
    assert ir.BookmarkStart("target") not in links  # raw XML keeps it, as raw XML
    assert visible_text(compiled) == "Hereback"


def test_values_are_written_in_round_units() -> None:
    assert length(Length.twips(1440), "cm") == "2.54cm"
    assert length(Length.twips(567), "cm") == "1cm"
    assert length(Length.twips(708), "cm") == "1.25cm"
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
