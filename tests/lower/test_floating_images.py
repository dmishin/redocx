"""Floating pictures, cropping, rotation and borders on pictures (M4 step 3)."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest
from lxml import etree
from PIL import Image as PILImage

from redocx import ir
from redocx.compiler import build, compile_source
from redocx.values import Border, Color, Length
from tests.support.xsd import validate_package

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
WP = "{http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing}"
A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"


@pytest.fixture
def folder(tmp_path: Path) -> Path:
    PILImage.new("RGB", (200, 100), "red").save(tmp_path / "pic.png", dpi=(96, 96))
    return tmp_path


def image(text: str, folder: Path) -> ir.Image:
    result = compile_source(text, folder)
    assert result.document is not None, result.render_diagnostics()
    assert not result.diagnostics, result.render_diagnostics()
    (found,) = [
        i for p in result.document.all_paragraphs() for i in p.content if isinstance(i, ir.Image)
    ]
    return found


def errors(text: str, folder: Path) -> list[str]:
    return [d.message for _, d in compile_source(text, folder).diagnostics if d.is_error]


def drawing(text: str, folder: Path) -> etree._Element:  # pyright: ignore[reportPrivateUsage]
    data, result = build(text, folder)
    assert data is not None, result.render_diagnostics()
    assert validate_package(data) == {}
    document = etree.fromstring(zipfile.ZipFile(io.BytesIO(data)).read("word/document.xml"))
    (found,) = document.iter(f"{W}drawing")
    return found[0]


def test_inline_by_default(folder: Path) -> None:
    assert image("\\image{pic.png}", folder).anchor is None
    assert drawing("\\image{pic.png}", folder).tag == f"{WP}inline"


def test_float_and_its_defaults(folder: Path) -> None:
    anchor = image("\\image[float]{pic.png}", folder).anchor
    assert anchor is not None
    assert anchor == ir.Anchor()
    assert (anchor.wrap, anchor.x, anchor.x_from, anchor.y_from) == (
        "square",
        Length(0),
        "column",
        "paragraph",
    )
    # wrap, x or y alone make the picture float
    assert image("\\image[x=right]{pic.png}", folder).anchor == ir.Anchor(x="right")
    assert image("\\image[wrap=behind]{pic.png}", folder).anchor == ir.Anchor(wrap="behind")


def test_positions_and_distance(folder: Path) -> None:
    anchor = image(
        "\\image[wrap=tight, wrap-side=left, x=(2cm, from=page), y=(bottom, from=margin),"
        " distance=(all=0.2cm, left=0.5cm)]{pic.png}",
        folder,
    ).anchor
    assert anchor == ir.Anchor(
        "tight",
        "left",
        Length.cm(2),
        "page",
        "bottom",
        "margin",
        (Length.cm("0.2"), Length.cm("0.2"), Length.cm("0.5"), Length.cm("0.2")),
    )


def test_anchor_xml(folder: Path) -> None:
    anchor = drawing(
        "\\image[wrap=square, wrap-side=right, x=(right, from=margin), y=1cm]{pic.png}", folder
    )
    assert anchor.tag == f"{WP}anchor"
    names = [etree.QName(c).localname for c in anchor]
    assert names == [
        "simplePos",
        "positionH",
        "positionV",
        "extent",
        "effectExtent",
        "wrapSquare",
        "docPr",
        "cNvGraphicFramePr",
        "graphic",
    ]
    horizontal = anchor.find(f"{WP}positionH")
    assert horizontal is not None
    assert horizontal.get("relativeFrom") == "margin"
    assert horizontal.findtext(f"{WP}align") == "right"
    assert anchor.findtext(f"{WP}positionV/{WP}posOffset") == str(Length.cm(1).emu)
    assert anchor.find(f"{WP}wrapSquare").get("wrapText") == "right"  # pyright: ignore[reportOptionalMemberAccess]
    assert anchor.get("behindDoc") == "0"


@pytest.mark.parametrize(
    ("wrap", "element", "behind"),
    [
        ("tight", "wrapTight", "0"),
        ("top-bottom", "wrapTopAndBottom", "0"),
        ("behind", "wrapNone", "1"),
        ("front", "wrapNone", "0"),
    ],
)
def test_wrap_modes(folder: Path, wrap: str, element: str, behind: str) -> None:
    anchor = drawing(f"\\image[wrap={wrap}]{{pic.png}}", folder)
    assert anchor.find(f"{WP}{element}") is not None
    assert anchor.get("behindDoc") == behind
    if wrap == "tight":
        polygon = anchor.find(f"{WP}wrapTight/{WP}wrapPolygon")
        assert polygon is not None
        assert len(polygon) == 5


def test_crop_changes_the_size(folder: Path) -> None:
    # 200 x 100 px at 96 dpi; cropping half the width leaves a square
    picture = image("\\image[crop=(left=25%, right=25%)]{pic.png}", folder)
    assert picture.crop == ir.Crop(left=25_000, right=25_000)
    assert picture.width == picture.height == Length(952_500)  # 100 px at 96 dpi
    rect = drawing("\\image[crop=(top=12.5%)]{pic.png}", folder).find(f".//{A}srcRect")
    assert rect is not None
    assert dict(rect.attrib) == {"t": "12500"}


def test_rotation_and_border(folder: Path) -> None:
    picture = image("\\image[rotate=-12, border=(dashed, 1.5pt, theme.accent1)]{pic.png}", folder)
    assert picture.rotation == -12
    assert picture.border == Border("dashed", Length.pt("1.5"), Color(theme="accent1"))
    inline = drawing("\\image[rotate=90, border=(double, 2pt, #C00000)]{pic.png}", folder)
    assert inline.find(f".//{A}xfrm").get("rot") == "5400000"  # pyright: ignore[reportOptionalMemberAccess]
    line = inline.find(f".//{A}ln")
    assert line is not None
    assert (line.get("w"), line.get("cmpd")) == (str(Length.pt(2).emu), "dbl")
    assert line.find(f"{A}solidFill/{A}srgbClr").get("val") == "C00000"  # pyright: ignore[reportOptionalMemberAccess]


def test_a_floating_picture_takes_no_room_in_the_line(folder: Path) -> None:
    result = compile_source(
        "\\image[float]{pic.png}\nText and \\image[float]{pic.png} more.", folder
    )
    assert result.document is not None
    (paragraph,) = result.document.all_paragraphs()
    assert paragraph.text() == "Text and more."


def test_floating_picture_errors(folder: Path) -> None:
    assert errors("\\image[crop=(left=60%, right=50%)]{pic.png}", folder) == [
        "cropping would leave nothing of the picture"
    ]
    assert errors("\\image[x=middle]{pic.png}", folder) == [
        "`x`: expected a length or one of left, center, right, inside, outside, got `middle`"
    ]
    assert errors("\\figure{\\image[float]{pic.png}\n\\caption{A}}", folder) == [
        "a picture in a \\figure cannot float: its caption would not move with it"
    ]
