"""The reader on documents Word itself wrote (tests/corpus, built by tools/corpus/make-corpus.ps1).

These check what the importer can rely on: what each document holds is read, the losses are
the expected ones, and the IR read back writes a valid package again.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from redocx import ir
from redocx.ooxml.reader import ImportResult, read_docx
from redocx.ooxml.writer import to_docx_bytes
from tests.support.xsd import validate_package

CORPUS = Path(__file__).resolve().parents[1] / "corpus"
DOCUMENTS = sorted(p.name for p in CORPUS.glob("*.docx"))

EXPECTED_LOSSES = {
    "drawings.docx": {"shape (no text)"},
    "review.docx": {"oMath", "w:del (tracked deletion)"},
}
"""What the reader is known to leave out of each document (everything else is read)."""


def read(name: str) -> ImportResult:
    return read_docx(CORPUS / name)


def everything(doc: ir.Document) -> list[ir.Inline]:
    return [item for p in doc.all_paragraphs() for item in p.content]


def test_the_corpus_is_there() -> None:
    assert DOCUMENTS == [
        "drawings.docx",
        "references.docx",
        "review.docx",
        "sections.docx",
        "tables.docx",
        "text.docx",
    ]


@pytest.mark.parametrize("name", DOCUMENTS)
def test_only_the_expected_losses(name: str) -> None:
    lost = {construct for construct, _ in read(name).report.unsupported}
    assert lost == EXPECTED_LOSSES.get(name, set())


@pytest.mark.parametrize("name", DOCUMENTS)
def test_what_is_read_writes_a_valid_document(name: str) -> None:
    document = read(name).document
    assert validate_package(to_docx_bytes(document)) == {}


def test_text_notes_and_links() -> None:
    doc = read("text.docx").document
    items = everything(doc)
    kinds = {n.kind for n in items if isinstance(n, ir.Note)}
    assert kinds == {"footnote", "endnote"}
    (note,) = [n for n in items if isinstance(n, ir.Note) and n.kind == "footnote"]
    (first,) = note.content
    assert isinstance(first, ir.Paragraph)
    assert "The footnote text." in first.text()
    assert any(isinstance(i, ir.Hyperlink) and i.url == "https://example.com" for i in items)
    assert any(isinstance(i, ir.Break) and i.kind == "page" for i in items)
    bold = [r for r in items if isinstance(r, ir.Run) and r.text == "bold"]
    assert bold
    assert bold[0].props.get("bold") is True


def test_tables_styles_and_merges() -> None:
    doc = read("tables.docx").document
    first, second = list(doc.all_tables())
    assert first.style is not None
    style = next(s for s in doc.styles if s.name == first.style)
    assert style.kind is ir.StyleKind.TABLE
    assert {c.kind for c in style.conditions} >= {"firstRow"}
    assert first.rows[3].cells[0].colspan == 3
    assert second.rows[0].cells[0].rowspan == 3
    assert second.align == "center"


def test_contents_and_cross_references() -> None:
    doc = read("references.docx").document
    contents = [b for b in doc.body if isinstance(b, ir.FieldBlock)]
    assert len(contents) == 1
    assert contents[0].field.code.strip().startswith("TOC")
    codes = [i.code.strip() for i in everything(doc) if isinstance(i, ir.Field)]
    assert any(c.startswith("REF ") for c in codes)
    assert any(c.startswith("PAGEREF ") for c in codes)
    assert any(c.startswith("SEQ ") for c in codes)


def test_sections() -> None:
    doc = read("sections.docx").document
    first, second, third = doc.sections()
    assert first.title_page
    assert second.page.landscape
    assert second.page_number_format == "lower-roman"
    assert (third.columns, third.column_separator) == (2, True)
    assert third.borders is not None
    assert dict(second.headers)["default"]


def test_drawings() -> None:
    doc = read("drawings.docx").document
    items = everything(doc)
    images = [i for i in items if isinstance(i, ir.Image)]
    inline = next(i for i in images if i.anchor is None)
    assert inline.crop is not None
    assert inline.border is not None
    floating = next(i for i in images if i.anchor is not None)
    assert floating.anchor is not None
    assert floating.anchor.wrap == "square"
    (box,) = [i for i in items if isinstance(i, ir.TextBox)]
    (text,) = box.content
    assert isinstance(text, ir.Paragraph)
    assert text.text() == "A text box with some text."
    assert box.fill is not None
    drops = [p for p in doc.all_paragraphs() if p.drop_cap is not None]
    assert len(drops) == 1
    assert drops[0].drop_cap == ir.DropCap(3)


def test_review() -> None:
    doc = read("review.docx").document
    (start,) = [i for i in everything(doc) if isinstance(i, ir.CommentStart)]
    (comment_text,) = start.comment.content
    assert isinstance(comment_text, ir.Paragraph)
    assert comment_text.text() == "Please confirm the commercial position."
    assert [i for i in everything(doc) if isinstance(i, ir.CommentEnd)]
    texts = [p.text() for p in doc.all_paragraphs()]
    assert any("sixty" in t and "thirty" not in t for t in texts)  # the tracked edit, accepted
