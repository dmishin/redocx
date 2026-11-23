r"""SVG images with a fallback picture, `\embed-docx` and `\embed-rtf` as a w:altChunk, and
`\include-rtf` (design §3.16)."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest
from lxml import etree
from PIL import Image as PILImage

from redocx import ir
from redocx.compiler import build, compile_source
from redocx.lower.media import PLACEHOLDER_PNG, svg_size
from redocx.ooxml.reader import read_docx
from redocx.values import Length
from tests.support.xsd import validate_package

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
ASVG = "{http://schemas.microsoft.com/office/drawing/2016/SVG/main}"
R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
SVG = '<svg xmlns="http://www.w3.org/2000/svg" {}><rect width="10" height="10"/></svg>'
RTF = rb"{\rtf1\ansi\deff0{\fonttbl{\f0 Arial;}}\pard\fs20 From an {\b RTF} file.\par}"


@pytest.fixture
def files(tmp_path: Path) -> Path:
    (tmp_path / "chart.svg").write_text(SVG.format('width="8cm" viewBox="0 0 400 200"'), "utf-8")
    (tmp_path / "plain.svg").write_text(SVG.format(""), "utf-8")
    (tmp_path / "html.svg").write_text("<html/>", "utf-8")
    PILImage.new("RGB", (40, 20), "red").save(tmp_path / "chart.png")
    part, result = build("= Embedded\n\nFrom another file.\n")
    assert part is not None, result.render_diagnostics()
    (tmp_path / "part.docx").write_bytes(part)
    (tmp_path / "notes.txt").write_text("not a docx", "utf-8")
    (tmp_path / "part.rtf").write_bytes(RTF)
    return tmp_path


def compiled(text: str, base_dir: Path) -> ir.Document:
    result = compile_source(text, base_dir)
    assert result.document is not None, result.render_diagnostics()
    assert not result.diagnostics, result.render_diagnostics()
    return result.document


def errors(text: str, base_dir: Path) -> list[str]:
    return [d.message for _, d in compile_source(text, base_dir).diagnostics if d.is_error]


def the_image(doc: ir.Document) -> ir.Image:
    (image,) = [i for p in doc.all_paragraphs() for i in p.content if isinstance(i, ir.Image)]
    return image


@pytest.mark.parametrize(
    ("attributes", "size"),
    [
        ('width="8cm" height="2in"', (Length.cm(8), Length.inch(2))),
        ('width="96" height="48px"', (Length.inch(1), Length.inch("0.5"))),
        ('width="72pt" viewBox="0 0 400 200"', (Length.inch(1), Length.inch("0.5"))),
        ('height="1in" viewBox="0,0,400,200"', (Length.inch(2), Length.inch(1))),
        ('viewBox="0 0 192 96"', (Length.inch(2), Length.inch(1))),
        ('width="100%" height="100%" viewBox="0 0 96 96"', (Length.inch(1), Length.inch(1))),
        ("", (Length.inch("3.125"), Length.inch("1.5625"))),  # a browser's 300 x 150
    ],
)
def test_svg_size(attributes: str, size: tuple[Length, Length]) -> None:
    assert svg_size(SVG.format(attributes).encode()) == size


def test_svg_size_rejects_other_xml() -> None:
    assert svg_size(b"<html/>") is None
    assert svg_size(b"not xml") is None


def test_svg_gets_a_placeholder_fallback(files: Path) -> None:
    image = the_image(compiled('\\image[alt="Chart"]{chart.svg}', files))
    assert image.svg == (files / "chart.svg").read_bytes()
    assert image.format == "png"
    assert (image.width, image.height) == (Length.cm(8), Length.cm(4))
    assert image.data == PLACEHOLDER_PNG
    with PILImage.open(io.BytesIO(image.data)) as fallback:
        assert (fallback.size, fallback.getpixel((0, 0))) == ((1, 1), (217, 217, 217))


def test_svg_with_fallback_and_width(files: Path) -> None:
    image = the_image(compiled("\\image[width=4cm, fallback=chart.png]{chart.svg}", files))
    assert image.data == (files / "chart.png").read_bytes()
    assert (image.width, image.height) == (Length.cm(4), Length.cm(2))


def test_svg_is_written_as_word_writes_it(files: Path) -> None:
    data, result = build("\\image{chart.svg}", files)
    assert data is not None, result.render_diagnostics()
    package = zipfile.ZipFile(io.BytesIO(data))
    document = etree.fromstring(package.read("word/document.xml"))
    (blip,) = document.iter(f"{A}blip")
    (svg_blip,) = blip.iter(f"{ASVG}svgBlip")
    rels = etree.fromstring(package.read("word/_rels/document.xml.rels"))
    targets = {rel.get("Id"): rel.get("Target") for rel in rels}
    assert targets[blip.get(f"{R}embed")] == "media/image1.png"
    assert targets[svg_blip.get(f"{R}embed")] == "media/image2.svg"
    assert b'Extension="svg" ContentType="image/svg+xml"' in package.read("[Content_Types].xml")
    assert validate_package(data) == {}


def test_reader_keeps_the_svg(files: Path) -> None:
    data, _ = build("\\image{chart.svg}", files)
    assert data is not None
    (files / "out.docx").write_bytes(data)
    images = [
        i
        for p in read_docx(files / "out.docx").document.all_paragraphs()
        for i in p.content
        if isinstance(i, ir.Image)
    ]
    assert [i.svg for i in images] == [(files / "chart.svg").read_bytes()]


def test_svg_errors(files: Path) -> None:
    assert errors("\\image{html.svg}", files) == ["`html.svg` is not an SVG image"]
    assert errors("\\image[fallback=missing.png]{chart.svg}", files)[0].startswith(
        "cannot read fallback image `missing.png`"
    )
    assert errors("\\image[fallback=plain.svg]{chart.svg}", files) == [
        "`plain.svg` is not an image Word can show"
    ]
    assert errors("\\image[fallback=chart.svg]{chart.png}", files) == [
        "`fallback` is only for SVG images"
    ]


def test_embed_docx_is_an_alt_chunk(files: Path) -> None:
    doc = compiled("Before.\n\n\\embed-docx{part.docx}\n\nAfter.", files)
    (chunk,) = [b for b in doc.body if isinstance(b, ir.AltChunk)]
    assert chunk.data == (files / "part.docx").read_bytes()
    data, result = build("Before.\n\n\\embed-docx{part.docx}\n\nAfter.", files)
    assert data is not None, result.render_diagnostics()
    package = zipfile.ZipFile(io.BytesIO(data))
    body = etree.fromstring(package.read("word/document.xml")).find(f"{W}body")
    assert body is not None
    assert [etree.QName(child).localname for child in body] == ["p", "altChunk", "p", "sectPr"]
    rels = etree.fromstring(package.read("word/_rels/document.xml.rels"))
    (rel,) = [r for r in rels if r.get("Type", "").endswith("/aFChunk")]
    assert rel.get("Target") == "afchunk1.docx"
    assert package.read("word/afchunk1.docx") == chunk.data
    assert validate_package(data) == {}


def test_embed_docx_errors(files: Path) -> None:
    assert errors("\\embed-docx{notes.txt}", files) == [
        "`notes.txt` is not a Word document (.docx)"
    ]
    assert errors("\\embed-docx{missing.docx}", files)[0].startswith(
        "cannot read document `missing.docx`"
    )
    assert errors("| \\embed-docx{part.docx} |\n|---|", files) != []


def test_embed_rtf_is_an_alt_chunk(files: Path) -> None:
    data, result = build("Before.\n\n\\embed-rtf{part.rtf}\n\nAfter.", files)
    assert data is not None, result.render_diagnostics()
    assert not result.diagnostics, result.render_diagnostics()
    package = zipfile.ZipFile(io.BytesIO(data))
    body = etree.fromstring(package.read("word/document.xml")).find(f"{W}body")
    assert body is not None
    assert [etree.QName(child).localname for child in body] == ["p", "altChunk", "p", "sectPr"]
    rels = etree.fromstring(package.read("word/_rels/document.xml.rels"))
    (rel,) = [r for r in rels if r.get("Type", "").endswith("/aFChunk")]
    assert rel.get("Target") == "afchunk1.rtf"
    assert package.read("word/afchunk1.rtf") == RTF
    assert b'Extension="rtf" ContentType="application/rtf"' in package.read("[Content_Types].xml")
    assert validate_package(data) == {}


def test_embed_rtf_errors(files: Path) -> None:
    assert errors("\\embed-rtf{notes.txt}", files) == ["`notes.txt` is not an RTF document"]
    assert errors("\\embed-rtf{part.docx}", files) == ["`part.docx` is not an RTF document"]
    assert errors("| \\embed-rtf{part.rtf} |\n|---|", files) != []


INSET = (
    rb"{\rtf1\ansi\deff0{\fonttbl{\f0 Arial;}}{\colortbl;\red255\green0\blue0;}"
    rb"\pard\qc\fs20 Red {\cf1\b text}.\par"
    rb"\trowd\trgaph100\cellx2000\cellx4000\intbl A\cell B\cell\row"
    rb"\pard{\pict\pngblip\picwgoal1440\pichgoal720 PNG}\par}"
)


@pytest.fixture
def inset(files: Path) -> Path:
    png = io.BytesIO()
    PILImage.new("RGB", (4, 2), "blue").save(png, "PNG")
    (files / "inset.rtf").write_bytes(INSET.replace(b"PNG", png.getvalue().hex().encode()))
    note = rb"{\rtf1\ansi Text{\footnote\pard A note.}.\par{\header\pard Head\par}}"
    (files / "note.rtf").write_bytes(note)
    return files


def test_include_rtf_is_the_documents_own_content(inset: Path) -> None:
    doc = compiled("Before.\n\n\\include-rtf{inset.rtf}\n\nAfter.", inset)
    texts = [p.text() for p in doc.body if isinstance(p, ir.Paragraph)]
    assert texts[0] == "Before."
    assert texts[1] == "Red text."
    assert texts[-1] == "After."
    first = doc.body[1]
    assert isinstance(first, ir.Paragraph)
    assert first.props.get("align") == "center"
    assert first.props.get("space-after") == Length(0)  # RTF's default, not Normal's
    (bold,) = [r for r in first.runs() if r.props.get("bold")]
    assert bold.text == "text"
    assert bold.props.get("font") == "Arial"
    assert bold.props.get("size") == Length.pt(10)
    (table,) = [b for b in doc.body if isinstance(b, ir.Table)]
    assert [w.in_twips for w in table.widths] == [2000, 2000]
    image = the_image(doc)
    assert (image.width.in_twips, image.height.in_twips) == (1440, 720)
    data, result = build("\\include-rtf{inset.rtf}", inset)
    assert data is not None, result.render_diagnostics()
    assert validate_package(data) == {}


def test_include_rtf_warns_about_what_it_leaves_out(inset: Path) -> None:
    result = compile_source("\\include-rtf{note.rtf}", inset)
    assert result.document is not None
    (warning,) = [d.message for _, d in result.diagnostics]
    assert warning.startswith("`note.rtf`: left out ")
    assert "footnote" in warning
    assert "header" in warning


def test_include_rtf_errors(files: Path) -> None:
    assert errors("\\include-rtf{notes.txt}", files) == ["`notes.txt` is not an RTF document"]
    assert errors("\\include-rtf{missing.rtf}", files)[0].startswith("cannot read document")
