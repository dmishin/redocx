"""Watermarks: VML shapes in the headers, as Word's Design > Watermark writes them."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest
from lxml import etree
from PIL import Image as PILImage

from tests.support.xsd import validate_package
from wordgenx import ir
from wordgenx.compiler import build, compile_source
from wordgenx.values import Color, Length

V = "{urn:schemas-microsoft-com:vml}"


def compiled(text: str, folder: Path | None = None) -> ir.Document:
    result = compile_source(text, folder)
    assert result.document is not None, result.render_diagnostics()
    assert not result.diagnostics, result.render_diagnostics()
    return result.document


def errors(text: str) -> list[str]:
    return [d.message for _, d in compile_source(text).diagnostics if d.is_error]


def marks(blocks: tuple[ir.Block, ...]) -> list[ir.Watermark]:
    return [i for p in ir.iter_paragraphs(blocks) for i in p.content if isinstance(i, ir.Watermark)]


def test_text_watermark_in_every_header_kind() -> None:
    doc = compiled(
        "\\settings[even-odd-headers]\n\\section[title-page]\n\\watermark[DRAFT]\n\nText."
    )
    headers = dict(doc.section.headers)
    assert set(headers) == {"default", "first", "even"}
    for content in headers.values():
        (mark,) = marks(content)
        assert (mark.text, mark.diagonal, mark.semitransparent) == ("DRAFT", True, True)
        assert mark.color == Color(rgb="C0C0C0")
    (mark,) = marks(headers["default"])
    text_width = doc.section.page.text_width
    assert mark.width == text_width
    assert mark.height.emu < text_width.emu // 2


def test_watermark_joins_the_header_and_carries_on() -> None:
    doc = compiled(
        "\\header{Top}\n\\watermark[text=SECRET, color=#C00000, layout=horizontal]\n\nOne.\n\n"
        "\\section\n\nTwo.\n\n\\section\n\\watermark[none]\n\nThree."
    )
    first, second, third = doc.sections()
    ((_, content),) = first.headers
    (paragraph,) = content
    assert isinstance(paragraph, ir.Paragraph)
    assert isinstance(paragraph.content[0], ir.Watermark)  # at the start of the header text
    assert paragraph.text() == "Top"
    assert not paragraph.content[0].diagonal
    assert marks(dict(second.headers)["default"])  # inherited, with the watermark
    ((_, plain),) = third.headers  # without: a plain copy of the inherited header
    assert not marks(plain)
    assert ir.Paragraph(paragraph.content[1:], paragraph.style, paragraph.props) == plain[0]


def test_picture_watermark(tmp_path: Path) -> None:
    PILImage.new("RGB", (300, 150), "teal").save(tmp_path / "logo.png", dpi=(96, 96))
    doc = compiled("\\watermark[image=logo.png, width=6cm]\n\nText.", tmp_path)
    (mark,) = marks(dict(doc.section.headers)["default"])
    assert mark.image is not None
    assert (mark.width, mark.height, mark.washout) == (Length.cm(6), Length.cm(3), True)
    data, result = build("\\watermark[image=logo.png, washout=off]\n\nText.", tmp_path)
    assert data is not None, result.render_diagnostics()
    assert validate_package(data) == {}
    package = zipfile.ZipFile(io.BytesIO(data))
    header = etree.fromstring(package.read("word/header1.xml"))
    (shape,) = header.iter(f"{V}shape")
    assert shape.get("id", "").startswith("WordPictureWatermark")
    (imagedata,) = shape.iter(f"{V}imagedata")
    assert imagedata.get("gain") is None  # washout off
    assert b"media/image1.png" in package.read("word/_rels/header1.xml.rels")


def test_written_as_word_writes_it() -> None:
    data, result = build("\\watermark[DRAFT]\n\nText.")
    assert data is not None, result.render_diagnostics()
    assert validate_package(data) == {}
    header = etree.fromstring(zipfile.ZipFile(io.BytesIO(data)).read("word/header1.xml"))
    (shape,) = header.iter(f"{V}shape")
    assert shape.get("id", "").startswith("PowerPlusWaterMarkObject")  # Word's own id
    assert shape.get("type") == "#_x0000_t136"
    assert "rotation:315" in shape.get("style", "")
    (path,) = shape.iter(f"{V}textpath")
    assert path.get("string") == "DRAFT"
    assert shape.find(f"{V}fill").get("opacity") == ".5"  # pyright: ignore[reportOptionalMemberAccess]


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("\\watermark", "\\watermark takes text or an image"),
        ("\\watermark[text=A, image=b.png]", "\\watermark takes text or an image"),
        (
            "\\table{\\row{\\cell{\\watermark[A]}}}",
            "\\watermark belongs to a section, not to a table, header, footer or note",
        ),
    ],
)
def test_watermark_errors(text: str, message: str) -> None:
    assert message in errors(text)
