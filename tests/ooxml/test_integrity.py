"""The consistency check of written packages (wordgenx.ooxml.integrity), build --validate."""

import io
import zipfile
from pathlib import Path

import pytest

from wordgenx import ir
from wordgenx.cli import main
from wordgenx.compiler import build
from wordgenx.ooxml import validate
from wordgenx.ooxml.integrity import package_problems
from wordgenx.ooxml.writer import build_package
from wordgenx.syntax.diagnostics import Code


def _problems(raw: str) -> list[str]:
    document = ir.Document(body=(ir.RawBlock(raw), ir.paragraph("x")))
    return package_problems(build_package(document))


def test_a_consistent_document_has_no_problems() -> None:
    assert package_problems(build_package(ir.Document(body=(ir.paragraph("Hello"),)))) == []


def test_bookmarks_open_once_and_close() -> None:
    problems = _problems(
        '<w:p><w:bookmarkStart w:id="7" w:name="a"/><w:bookmarkStart w:id="7" w:name="A"/>'
        '<w:bookmarkEnd w:id="8"/></w:p>'
    )
    assert problems == [
        "word/document.xml: bookmark id 7 is opened 2 times",
        "word/document.xml: bookmark id 7 is never closed",
        "word/document.xml: bookmark id 8 is closed but never opened",
        "bookmark `a` is defined 2 times",
    ]


def test_references_name_what_exists() -> None:
    problems = _problems(
        '<w:p><w:pPr><w:pStyle w:val="Nowhere"/><w:numPr><w:numId w:val="5"/></w:numPr></w:pPr>'
        '<w:hyperlink r:id="rId99"><w:r><w:t>l</w:t></w:r></w:hyperlink>'
        '<w:r><w:footnoteReference w:id="3"/></w:r><w:commentRangeStart w:id="4"/></w:p>'
    )
    assert problems == [
        "word/document.xml refers to relationship rId99, which it does not have",
        "word/document.xml refers to footnote 3, which does not exist",
        "word/document.xml refers to comment 4, which does not exist",
        "paragraph style `Nowhere` is used but not defined",
        "list (numbering) 5 is used but not defined",
    ]


def test_links_go_to_bookmarks_that_exist() -> None:
    problems = _problems(
        '<w:p><w:bookmarkStart w:id="1" w:name="Here"/><w:bookmarkEnd w:id="1"/>'
        '<w:hyperlink w:anchor="here"/><w:hyperlink w:anchor="_top"/>'
        '<w:hyperlink w:anchor="gone"/></w:p>'
    )
    assert problems == ["a link goes to bookmark `gone`, which does not exist"]


def test_links_to_labels_go_to_their_bookmarks() -> None:
    data, result = build("= A {#sec:a}\n\n[back](#sec:a), [also](#_Ref_sec_a)")
    assert data is not None
    assert result.diagnostics == ()
    with zipfile.ZipFile(io.BytesIO(data)) as package:
        assert package.read("word/document.xml").count(b'w:anchor="_Ref_sec_a"') == 2


def test_drawing_ids_are_unique() -> None:
    drawing = (
        '<w:p><w:r><w:drawing><wp:inline><wp:extent cx="1" cy="1"/>'
        '<wp:docPr id="{}" name="d"/><a:graphic><a:graphicData uri="x"/></a:graphic>'
        "</wp:inline></w:drawing></w:r></w:p>"
    )
    assert _problems(drawing.format(1) + drawing.format(1)) == ["drawing id 1 is used 2 times"]


def test_build_reports_problems_as_one_warning() -> None:
    data, result = build(
        r'\raw[ooxml]{<w:p><w:bookmarkStart w:id="7" w:name="b"/></w:p>}' "\n\nText"
    )
    assert data is not None
    ((_, warning),) = result.diagnostics
    assert warning.code is Code.INCONSISTENT_OUTPUT
    assert warning.notes == ("word/document.xml: bookmark id 7 is never closed",)
    rendered = result.render_diagnostics()
    assert "warning[WGX0401]: the written document is inconsistent (1 problem)" in rendered
    assert "  = note: word/document.xml: bookmark id 7 is never closed" in rendered
    assert " | " not in rendered  # no source line: the problem has no place in the source


def test_build_validate_fails_on_schema_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    source = tmp_path / "a.wgx"
    source.write_text("Hello", encoding="utf-8")
    out = tmp_path / "a.docx"
    assert main(["build", str(source), "-o", str(out), "--validate"]) == 0
    out.unlink()

    def invalid(data: bytes) -> dict[str, list[str]]:
        return {"word/document.xml": ["2:0: `w:bogus` is not allowed here"]}

    monkeypatch.setattr(validate, "validate_package", invalid)
    assert main(["build", str(source), "-o", str(out), "--validate"]) == 1
    assert not out.exists()
    err = capsys.readouterr().err
    assert "error: word/document.xml:2:0: `w:bogus` is not allowed here" in err
    assert "does not match the schemas (1 error; nothing was written)" in err
