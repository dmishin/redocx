"""Footnotes and endnotes (design §3.11, F-14 settings)."""

import io
import zipfile

from lxml import etree

from redocx import ir
from redocx.compiler import build, compile_source
from tests.support.xsd import validate_package

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def compiled(text: str) -> ir.Document:
    result = compile_source(text)
    assert result.document is not None, result.render_diagnostics()
    assert not result.diagnostics, result.render_diagnostics()
    return result.document


def errors(text: str) -> list[str]:
    result = compile_source(text)
    return [f"{d.code.value} {d.message}" for _, d in result.diagnostics if d.is_error]


def built(text: str) -> zipfile.ZipFile:
    data, result = build(text)
    assert data is not None, result.render_diagnostics()
    assert validate_package(data) == {}
    return zipfile.ZipFile(io.BytesIO(data))


def xml(package: zipfile.ZipFile, name: str) -> etree._Element:  # pyright: ignore[reportPrivateUsage]
    return etree.fromstring(package.read(name))


def test_footnote_in_text() -> None:
    doc = compiled("A claim.\\footnote{With a *note*.} More.")
    (paragraph,) = [p for p in doc.body if isinstance(p, ir.Paragraph)]
    note = paragraph.content[1]
    assert isinstance(note, ir.Note)
    assert (note.kind, note.style) == ("footnote", "Footnote Reference")
    assert paragraph.text() == "A claim. More."
    (text,) = note.content
    assert isinstance(text, ir.Paragraph)
    assert text.style == "Footnote Text"
    assert text.content[0] == ir.NoteMark("footnote", "Footnote Reference")
    assert text.text() == " With a note."
    styles = {s.name for s in doc.styles}
    assert {"Footnote Text", "Footnote Reference"} <= styles
    assert doc.notes("footnote") == [note]


def test_note_with_several_blocks() -> None:
    doc = compiled("Text.\\endnote{First.\n\n  - a\n  - b\n}")
    (note,) = doc.notes("endnote")
    first, a, b = note.content
    assert isinstance(first, ir.Paragraph) and first.style == "Endnote Text"  # noqa: PT018
    assert isinstance(a, ir.Paragraph) and a.list_item is not None  # noqa: PT018
    assert a.style == "List Paragraph"
    assert isinstance(b, ir.Paragraph)


def test_package_parts_and_references() -> None:
    package = built("One.\\footnote{F1} Two.\\footnote{F2} Three.\\endnote{E1}")
    document = xml(package, "word/document.xml")
    refs = [
        (etree.QName(r).localname, r.get(f"{W}id"))
        for r in document.iter(f"{W}footnoteReference", f"{W}endnoteReference")
    ]
    assert refs == [
        ("footnoteReference", "1"),
        ("footnoteReference", "2"),
        ("endnoteReference", "1"),
    ]
    footnotes = xml(package, "word/footnotes.xml")
    ids = [(n.get(f"{W}type"), n.get(f"{W}id")) for n in footnotes]
    assert ids == [("separator", "-1"), ("continuationSeparator", "0"), (None, "1"), (None, "2")]
    assert footnotes[2].find(f".//{W}footnoteRef") is not None
    assert "word/endnotes.xml" in package.namelist()
    settings = xml(package, "word/settings.xml")
    separators = settings.findall(f"{W}footnotePr/{W}footnote")
    assert [s.get(f"{W}id") for s in separators] == ["-1", "0"]


def test_no_note_parts_without_notes() -> None:
    package = built("Plain.")
    assert "word/footnotes.xml" not in package.namelist()
    assert xml(package, "word/settings.xml").find(f"{W}footnotePr") is None


def test_hyperlink_in_note_belongs_to_the_notes_part() -> None:
    package = built("Text.\\footnote{See [x](https://example.org).}")
    rels = package.read("word/_rels/footnotes.xml.rels").decode()
    assert "https://example.org" in rels


def test_note_settings() -> None:
    doc = compiled(
        "\\settings[footnotes=(format=symbols, restart=each-page, position=beneath-text, start=2), "
        "endnotes=(format=decimal, position=document-end)]\n\nText."
    )
    assert doc.settings.footnotes == ir.NoteSettings("chicago", 2, "eachPage", "beneathText")
    assert doc.settings.endnotes == ir.NoteSettings("decimal", None, None, "docEnd")
    package = built("\\settings[footnotes=lower-roman]\n\nText.\\footnote{x}")
    section = xml(package, "word/document.xml").find(f".//{W}sectPr/{W}footnotePr/{W}numFmt")
    assert section is not None
    assert section.get(f"{W}val") == "lowerRoman"


def test_invalid_note_settings() -> None:
    assert errors("\\settings[endnotes=(restart=each-page)]") == [
        "RDX0302 `endnotes`: unknown value `each-page`"
    ]


def test_note_references_print_the_number() -> None:
    doc = compiled(
        "\\settings[footnotes=(format=lower-letter, restart=each-section), endnotes=(start=3)]\n\n"
        "A.\\footnote{x} B.\\footnote[id=fn:b]{y} C.\\endnote[id=en:c]{z}\n\n"
        "@fn:b @en:c\n\n\\section\n\nD.\\footnote[id=fn:d]{w} @fn:d"
    )
    refs = [
        (item.code.strip(), item.result)
        for p in doc.all_paragraphs()
        for item in p.content
        if isinstance(item, ir.Field)
    ]
    assert refs == [
        ("NOTEREF _Ref_fn_b \\h", "b"),
        ("NOTEREF _Ref_en_c \\h", "iii"),
        ("NOTEREF _Ref_fn_d \\h", "a"),
    ]
    assert not doc.settings.update_fields_on_open


def test_notes_not_allowed_in_headers_or_notes() -> None:
    assert errors("\\header{Text\\footnote{x}}\n\nBody") == [
        "RDX0303 a footnote cannot be inside a header"
    ]
    assert errors("Text\\footnote{a\\endnote{b}}") == [
        "RDX0303 an endnote cannot be inside another note"
    ]


def test_notes_in_table_cells() -> None:
    package = built("| Cell\\footnote{In a table.} |")
    assert b"In a table." in package.read("word/footnotes.xml")
