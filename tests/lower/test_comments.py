"""Comments: \\comment[text=...]{range} (M5 step 2)."""

from __future__ import annotations

import io
import zipfile
from datetime import datetime

from lxml import etree

from tests.support.xsd import validate_package
from wordgenx import ir
from wordgenx.compiler import build, compile_source

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def compiled(text: str) -> ir.Document:
    result = compile_source(text)
    assert result.document is not None, result.render_diagnostics()
    assert not result.diagnostics, result.render_diagnostics()
    return result.document


def errors(text: str) -> list[str]:
    return [d.message for _, d in compile_source(text).diagnostics if d.is_error]


def comments(doc: ir.Document) -> list[ir.Comment]:
    return [
        item.comment
        for paragraph in doc.all_paragraphs()
        for item in paragraph.content
        if isinstance(item, ir.CommentStart)
    ]


def package(text: str) -> zipfile.ZipFile:
    data, result = build(text)
    assert data is not None, result.render_diagnostics()
    assert validate_package(data) == {}
    return zipfile.ZipFile(io.BytesIO(data))


def test_a_comment_on_a_range() -> None:
    doc = compiled(
        '\\meta[author="Jane Doe"]\n\nThe term is \\comment[text="Confirm?", date=2026-10-13]{30 days} long.'
    )
    (paragraph,) = [p for p in doc.body if isinstance(p, ir.Paragraph)]
    assert paragraph.text() == "The term is 30 days long."
    kinds = [type(i).__name__ for i in paragraph.content]
    assert kinds == ["Run", "CommentStart", "Run", "CommentEnd", "Run"]
    (comment,) = comments(doc)
    assert (comment.author, comment.initials, comment.date) == (
        "Jane Doe",
        "JD",
        datetime(2026, 10, 13),
    )
    (note,) = comment.content
    assert isinstance(note, ir.Paragraph)
    assert (note.style, note.text()) == ("annotation text", "Confirm?")


def test_markup_point_comments_and_defaults() -> None:
    doc = compiled("A\\comment[text={With *markup*:\n\n- one\n- two}] point.")
    (comment,) = comments(doc)
    assert comment.author == "Author"  # no \meta author
    assert len(comment.content) == 3
    assert next(p.text() for p in doc.all_paragraphs()) == "A point."


def test_written_as_word_writes_it() -> None:
    files = package('B \\comment[author="R. Patel", text="Why?"]{text} C.')
    document = etree.fromstring(files.read("word/document.xml"))
    names = [etree.QName(e).localname for e in next(document.iter(f"{W}p"))]
    assert names == ["r", "commentRangeStart", "r", "commentRangeEnd", "r", "r"]
    reference = next(document.iter(f"{W}commentReference"))
    assert reference.get(f"{W}id") == "0"
    part = etree.fromstring(files.read("word/comments.xml"))
    (comment,) = part
    assert (comment.get(f"{W}author"), comment.get(f"{W}initials")) == ("R. Patel", "RP")
    first_run = comment.find(f"{W}p/{W}r")
    assert first_run is not None
    assert first_run.find(f"{W}annotationRef") is not None
    styles = files.read("word/styles.xml")
    assert b'w:styleId="CommentText"' in styles
    assert b'w:styleId="CommentReference"' in styles
    assert b"comments+xml" in files.read("[Content_Types].xml")


def test_comment_errors() -> None:
    assert errors("\\comment{no text}") != []
    assert errors("\\comment[text=x, date=13/10/2026]{y}") == [
        "`date`: expected YYYY-MM-DD or YYYY-MM-DDThh:mm, got `13/10/2026`"
    ]
    assert errors("A\\footnote{B \\comment[text=x]{y}}") == ["a comment cannot be in a footnote"]
