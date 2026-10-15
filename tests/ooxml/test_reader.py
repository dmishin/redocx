from collections.abc import Callable
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
from wordgenx.values import Length


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


def test_reports_constructs_it_cannot_represent() -> None:
    # Inject a run-level border: the property registry has no such property.
    bold = ir.Document(body=(ir.paragraph(ir.run("plain", bold=True)),))
    with ZipFile(BytesIO(to_docx_bytes(bold))) as archive:
        parts = {name: archive.read(name) for name in archive.namelist()}
    parts["word/document.xml"] = parts["word/document.xml"].replace(
        b"<w:b/>", b'<w:b/><w:bdr w:val="single"/>'
    )
    buffer = BytesIO()
    with ZipFile(buffer, "w") as archive:
        for name, data in parts.items():
            archive.writestr(name, data)

    report = read_docx(buffer.getvalue()).report
    assert ("w:bdr", 1) in report.unsupported


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
