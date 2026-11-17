"""Custom themes: `theme { ... }` in style sheets (M4 step 2)."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

from lxml import etree

from redocx import ir
from redocx.compiler import build, compile_source
from redocx.values import Border, Color, apply_tint_shade
from tests.support.xsd import validate_package

ROOT = Path(__file__).resolve().parents[2]
A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
HARBOUR = (
    "\\styles{ theme Harbour { dark2: #1B2A41; accent1: #0b7a75; text2: #223344;"
    ' followed-hyperlink: #445566; major-font: "Georgia"; minor-font: Segoe UI; } }\n\n'
)


def compiled(text: str) -> ir.Document:
    result = compile_source(text)
    assert result.document is not None, result.render_diagnostics()
    assert not result.diagnostics, result.render_diagnostics()
    return result.document


def errors(text: str) -> list[str]:
    return [d.message for _, d in compile_source(text).diagnostics if d.is_error]


def theme_part(data: bytes) -> etree._Element:  # pyright: ignore[reportPrivateUsage]
    package = zipfile.ZipFile(io.BytesIO(data))
    (name,) = [n for n in package.namelist() if n.startswith("word/theme/")]
    return etree.fromstring(package.read(name))


def scheme(root: etree._Element) -> dict[str, str]:  # pyright: ignore[reportPrivateUsage]
    colors = root.find(f"{A}themeElements/{A}clrScheme")
    assert colors is not None
    return {etree.QName(slot).localname: slot[0].get("val") or "" for slot in colors}


def test_theme_block() -> None:
    theme = compiled(HARBOUR + "Text.").theme
    assert theme == ir.ThemeChoice(
        "Harbour",
        (("dk2", "223344"), ("accent1", "0B7A75"), ("folHlink", "445566")),  # text2 is dark2
        "Georgia",
        "Segoe UI",
    )


def test_later_sheets_add_to_the_theme() -> None:
    theme = compiled(
        "\\styles{ theme { accent1: #111111; minor-font: Arial; } }\n"
        "\\styles{ theme Night { accent1: #222222; accent2: #333333; } }\n\nText."
    ).theme
    assert theme == ir.ThemeChoice(
        "Night", (("accent1", "222222"), ("accent2", "333333")), None, "Arial"
    )


def test_the_theme_part_and_theme_colours_follow() -> None:
    text = HARBOUR + "A \\font[color=theme.accent1]{word}, \\font[font=theme.major]{heading}."
    data, result = build(text)
    assert data is not None, result.render_diagnostics()
    assert validate_package(data) == {}
    root = theme_part(data)
    colors = scheme(root)
    assert (colors["accent1"], colors["dk2"], colors["accent2"]) == ("0B7A75", "223344", "E07A1F")
    major = root.find(f"{A}themeElements/{A}fontScheme/{A}majorFont/{A}latin")
    assert major is not None
    assert major.get("typeface") == "Georgia"
    document = zipfile.ZipFile(io.BytesIO(data)).read("word/document.xml")
    assert b'w:val="0B7A75" w:themeColor="accent1"' in document  # the cached colour
    fonts = zipfile.ZipFile(io.BytesIO(data)).read("word/fontTable.xml")
    assert b'w:name="Georgia"' in fonts


def test_a_templates_theme_is_patched() -> None:
    template = ROOT / "examples" / "templates" / "corporate.dotx"
    data, result = build(HARBOUR + "Text.", template=template)
    assert data is not None, result.render_diagnostics()
    root = theme_part(data)
    assert scheme(root)["accent1"] == "0B7A75"
    assert root.find(f"{A}themeElements/{A}clrScheme").get("name") == "Harbour"  # pyright: ignore[reportOptionalMemberAccess]
    assert validate_package(data) == {}


def test_theme_errors() -> None:
    assert errors("\\styles{ theme { accent7: #000000; } }") == ["unknown theme entry `accent7`"]
    assert errors("\\styles{ theme { accent1: theme.accent2; } }") == [
        "`accent1` takes a colour such as #0B7A75"
    ]
    assert errors("\\styles{ theme { accent1: #12345; } }") != []
    assert errors("\\styles{ theme { major-font: theme.minor; } }") == [
        "`major-font` takes a font name"
    ]


def test_tints_and_shades_are_computed_as_word_does() -> None:
    # Word scales HSL luminance; these match its rendering of accent1 lighter 40%.
    assert apply_tint_shade("0B7A75", 0x99, None) == "30ECE4"
    assert apply_tint_shade("D1495B", None, 0xBF) == "A92B3B"
    assert apply_tint_shade("808080", None, None) == "808080"


def test_border_with_a_tinted_colour_last() -> None:
    border = Border.parse("single 1pt theme.accent1.lighter(40%)")
    assert border.color == Color(theme="accent1", tint=0x99)
