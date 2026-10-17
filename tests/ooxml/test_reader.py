from collections.abc import Callable
from dataclasses import replace
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

import pytest

from tests.golden.cases import example_tables, metadata, minimal, styled
from wordgenx import ir
from wordgenx.cli import main
from wordgenx.ooxml import Package
from wordgenx.ooxml.reader import ImportReport, NotADocxError, read_docx
from wordgenx.ooxml.writer import to_docx_bytes
from wordgenx.props import para_props
from wordgenx.values import Border, Color, Length


def _read(document: ir.Document) -> tuple[ir.Document, ImportReport]:
    result = read_docx(to_docx_bytes(document))
    return result.document, result.report


# --- plain IR round-trips -----------------------------------------------------


def test_reads_a_minimal_document() -> None:
    document, report = _read(minimal())
    assert [paragraph.text() for paragraph in document.all_paragraphs()] == ["Hello, wordgenx."]
    assert report.paragraphs == 1
    assert report.unsupported == ()


def test_recovers_direct_run_and_paragraph_formatting() -> None:
    document, _ = _read(styled())
    paragraphs = list(document.all_paragraphs())
    assert paragraphs[0].text() == "Styled document"
    assert paragraphs[0].style == "heading 1"

    body = paragraphs[1]
    assert body.style == "Body Text"
    body_text = next(style for style in document.styles if style.name == "Body Text")
    assert body_text.paragraph["align"] == "both"
    first_line = body_text.paragraph["first-line-indent"]
    assert isinstance(first_line, Length)
    assert first_line.in_twips == Length.cm("0.5").in_twips

    direct = next(run for run in body.runs() if run.text == "direct formatting")
    assert direct.props["underline"] == "double"
    assert direct.props["highlight"] == "lightGray"

    chemistry = paragraphs[2]
    subscript = next(run for run in chemistry.runs() if run.text == "2")
    assert subscript.props["vertical-align"] == "subscript"
    assert any(run.props.get("small-caps") is True for run in chemistry.runs())


def test_recovers_section_page_setup() -> None:
    document, _ = _read(metadata())
    assert document.section.page.width == Length.inch(11)
    assert document.section.page.margin_left == Length.inch("0.75")
    assert document.settings.update_fields_on_open
    assert document.settings.even_and_odd_headers
    assert document.meta.title == "Q3 Report & Outlook"


def test_recovers_tables() -> None:
    original = example_tables()
    document, report = _read(original)
    source = next(ir.iter_tables(original.body))
    recovered = next(ir.iter_tables(document.body))
    assert [w.in_twips for w in recovered.widths] == [w.in_twips for w in source.widths]
    assert len(recovered.rows) == len(source.rows)
    assert report.tables >= 1


def test_recovers_row_spans() -> None:
    def cell(text: str, rowspan: int = 1) -> ir.Cell:
        return ir.Cell((ir.paragraph(text),), rowspan=rowspan)

    rows = (
        ir.Row((cell("A", rowspan=3), cell("B"))),
        ir.Row((cell("C"),)),
        ir.Row((cell("D"),)),
    )
    table = ir.Table(rows, (Length.twips(1134), Length.twips(1134)), style=None)
    document, _ = _read(ir.Document(body=(table,)))
    assert next(ir.iter_tables(document.body)) == table


def test_recovers_jpeg_images() -> None:
    image = ir.Image(b"\xff\xd8\xff\xe0", "jpeg", Length.cm(1), Length.cm(1))
    document, report = _read(ir.Document(body=(ir.Paragraph((image,)),)))
    assert next(document.all_paragraphs()).content == (replace(image, name="Picture 1"),)
    assert report.images == 1


def test_keeps_fields_inside_hyperlinks() -> None:
    page = ir.Field(("PAGE",), "7")
    link = ir.Hyperlink("https://example.com", (ir.run("p. "), page))
    document, _ = _read(ir.Document(body=(ir.Paragraph((link,)),)))
    recovered = next(document.all_paragraphs()).content[0]
    assert isinstance(recovered, ir.Hyperlink)
    assert recovered.content[1] == page


# --- reading documents the writer did not produce -----------------------------


def test_reads_a_hand_written_package(hello_package: Callable[[], Package]) -> None:
    document = read_docx(hello_package().to_bytes()).document
    paragraphs = list(document.all_paragraphs())
    assert [paragraph.text() for paragraph in paragraphs] == [
        "Hello, wordgenx",
        "Written by the OPC package writer. See example.com",
    ]
    assert paragraphs[0].style == "heading 1"

    hyperlink = next(
        item
        for paragraph in document.all_paragraphs()
        for item in paragraph.content
        if isinstance(item, ir.Hyperlink)
    )
    assert hyperlink.url == "https://example.com/?a=1&b=2"


def _patched(document: ir.Document, old: bytes, new: bytes) -> bytes:
    """The document's package with one edit to word/document.xml."""
    with ZipFile(BytesIO(to_docx_bytes(document))) as archive:
        parts = {name: archive.read(name) for name in archive.namelist()}
    assert old in parts["word/document.xml"]
    parts["word/document.xml"] = parts["word/document.xml"].replace(old, new, 1)
    buffer = BytesIO()
    with ZipFile(buffer, "w") as archive:
        for name, data in parts.items():
            archive.writestr(name, data)
    return buffer.getvalue()


def test_reports_constructs_it_cannot_represent() -> None:
    # Inject an emphasis mark (East Asian typography): the registry has no such property.
    bold = ir.Document(body=(ir.paragraph(ir.run("plain", bold=True)),))
    report = read_docx(_patched(bold, b"<w:b/>", b'<w:b/><w:em w:val="dot"/>')).report
    assert ("w:em", 1) in report.unsupported


def test_one_bad_value_keeps_the_other_properties() -> None:
    bold = ir.Document(body=(ir.paragraph(ir.run("plain", bold=True)),))
    result = read_docx(_patched(bold, b"<w:b/>", b'<w:b/><w:highlight w:val="bogus"/>'))
    run = next(result.document.all_paragraphs()).runs()[0]
    assert run.props["bold"] is True
    assert result.report.unhandled == 1


def test_reads_symbols_as_hex_code_points() -> None:
    plain = ir.Document(body=(ir.paragraph("x"),))
    data = _patched(plain, b"<w:t>x</w:t>", b'<w:sym w:font="Symbol" w:char="0041"/>')
    assert next(read_docx(data).document.all_paragraphs()).text() == "A"


def test_a_field_spanning_paragraphs_keeps_the_text_after_it() -> None:
    plain = ir.Document(body=(ir.paragraph("x"), ir.paragraph("y")))
    data = _patched(
        plain,
        b"<w:r><w:t>y</w:t></w:r>",
        b'<w:r><w:t>two</w:t></w:r><w:r><w:fldChar w:fldCharType="end"/></w:r>'
        b"<w:r><w:t> after</w:t></w:r>",
    )
    paragraphs = list(read_docx(data).document.all_paragraphs())
    assert paragraphs[1].text() == "two after"


def test_rejects_something_that_is_not_a_document() -> None:
    with pytest.raises(NotADocxError):
        read_docx(b"not a zip at all")
    buffer = BytesIO()
    with ZipFile(buffer, "w") as archive:
        archive.writestr("hello.txt", "hi")
    with pytest.raises(NotADocxError):
        read_docx(buffer.getvalue())


# --- the CLI command ----------------------------------------------------------


def test_inspect_command_writes_a_report(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "hello.docx"
    path.write_bytes(to_docx_bytes(minimal()))
    assert main(["inspect", str(path)]) == 0
    out = capsys.readouterr().out
    assert "paragraphs  1" in out
    assert "coverage:" in out


def test_inspect_command_reports_bad_input(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "not.docx"
    path.write_bytes(b"nope")
    assert main(["inspect", str(path)]) == 1
    assert "error:" in capsys.readouterr().err


def _repackaged(data: bytes, part: str, old: bytes, new: bytes) -> bytes:
    with ZipFile(BytesIO(data)) as archive:
        parts = {name: archive.read(name) for name in archive.namelist()}
    assert old in parts[part]
    parts[part] = parts[part].replace(old, new)
    buffer = BytesIO()
    with ZipFile(buffer, "w") as archive:
        for name, content in parts.items():
            archive.writestr(name, content)
    return buffer.getvalue()


def _png() -> bytes:
    from PIL import Image as PILImage

    buffer = BytesIO()
    PILImage.new("RGB", (4, 4), "red").save(buffer, "PNG")
    return buffer.getvalue()


def test_a_picture_in_a_field_result_is_kept_as_the_picture() -> None:
    """A linked object (LINK field) or INCLUDEPICTURE shows its picture as the result."""
    picture = ir.Image(_png(), "png", Length.cm(1), Length.cm(1))
    data = _repackaged(
        to_docx_bytes(ir.Document(body=(ir.Paragraph((picture,)),))),
        "word/document.xml",
        b"<w:r><w:drawing>",
        b'<w:r><w:fldChar w:fldCharType="begin"/></w:r>'
        b'<w:r><w:instrText> LINK Excel.Sheet.12 "book.xlsx" "" \\a </w:instrText></w:r>'
        b'<w:r><w:fldChar w:fldCharType="separate"/></w:r><w:r><w:drawing>',
    )
    data = _repackaged(
        data,
        "word/document.xml",
        b"</w:drawing></w:r>",
        b'</w:drawing></w:r><w:r><w:fldChar w:fldCharType="end"/></w:r>',
    )
    result = read_docx(data)
    (paragraph,) = result.document.body
    assert isinstance(paragraph, ir.Paragraph)
    assert [type(item) for item in paragraph.content] == [ir.Image]
    assert ("LINK field (kept as its picture)", 1) in result.report.unsupported


def test_two_list_definitions_of_one_name_get_their_own_names() -> None:
    lists = (ir.Numbering("A", (ir.NumberingLevel(1),)), ir.Numbering("B", (ir.NumberingLevel(1),)))
    data = _repackaged(
        to_docx_bytes(ir.Document(numbering=lists)),
        "word/numbering.xml",
        b'<w:name w:val="B"/>',
        b'<w:name w:val="A"/>',
    )
    assert [n.name for n in read_docx(data).document.numbering] == ["A", "A 2"]


def test_borders_keep_their_theme_colours() -> None:
    border = Border("single", Length.pt(1), Color(theme="accent1", shade=0xBF))
    document = ir.Document(
        body=(ir.Paragraph((ir.Run("x"),), props=para_props(border_top=border)),)
    )
    (paragraph,) = read_docx(to_docx_bytes(document)).document.body
    assert isinstance(paragraph, ir.Paragraph)
    assert paragraph.props["border-top"] == border


def test_nested_fields_keep_their_structure() -> None:
    """{ = { NUMPAGES } -1 }: Word keeps the nested result as w:instrText in the code."""
    outer = ir.Field(("=", ir.Field(("NUMPAGES",), "17"), "-1"), "16")
    data = _repackaged(
        to_docx_bytes(ir.Document(body=(ir.Paragraph((outer,)),))),
        "word/document.xml",
        b"<w:t>17</w:t>",
        b"<w:instrText>17</w:instrText>",
    )
    (paragraph,) = read_docx(data).document.body
    assert isinstance(paragraph, ir.Paragraph)
    (field,) = paragraph.content
    assert field == outer
    assert isinstance(field, ir.Field)
    assert field.code == "={ NUMPAGES }-1"


def test_custom_document_properties_are_read() -> None:
    custom = (("project-code", "WX-001"), ("N° ISO", "A12"), ("count", 3), ("final", True))
    meta = read_docx(to_docx_bytes(ir.Document(meta=ir.Meta(custom=custom)))).document.meta
    assert meta.custom == custom
