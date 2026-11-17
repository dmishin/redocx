# ruff: noqa: RUF001 - Arabic and Hebrew test text
"""Right-to-left and complex scripts (M4 step 6)."""

from __future__ import annotations

import io
import zipfile

from lxml import etree

from redocx.compiler import build, compile_source
from redocx.ooxml.parts.theme import Theme
from redocx.props import para_props, run_props
from tests.support.xsd import validate_package

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def part(text: str, name: str = "word/document.xml") -> etree._Element:  # pyright: ignore[reportPrivateUsage]
    data, result = build(text)
    assert data is not None, result.render_diagnostics()
    assert not result.diagnostics, result.render_diagnostics()
    assert validate_package(data) == {}
    return etree.fromstring(zipfile.ZipFile(io.BytesIO(data)).read(name))


def errors(text: str) -> list[str]:
    return [d.message for _, d in compile_source(text).diagnostics if d.is_error]


def test_run_properties() -> None:
    assert run_props(rtl=True).emit(Theme()) == {"rtl": {}}
    assert run_props(**{"complex-script": True}).emit(Theme()) == {"cs": {}}
    assert run_props(bold=True, italic=True).emit(Theme()) == {
        "b": {},
        "bCs": {},  # as Word's Bold button writes it, so Arabic and Hebrew are bold too
        "i": {},
        "iCs": {},
    }


def test_languages_go_where_word_keeps_them() -> None:
    assert run_props(lang="ar-SA").emit(Theme()) == {"lang": {"bidi": "ar-SA"}}
    assert run_props(lang="he-IL").emit(Theme()) == {"lang": {"bidi": "he-IL"}}
    assert run_props(lang="th-TH").emit(Theme()) == {"lang": {"bidi": "th-TH"}}
    assert run_props(lang="ja-JP").emit(Theme()) == {"lang": {"eastAsia": "ja-JP"}}
    assert run_props(lang="de-DE").emit(Theme()) == {"lang": {"val": "de-DE"}}


def test_script_fonts_beat_theme_fonts() -> None:
    fonts = run_props(font="theme.minor", **{"font-cs": "Arial"}).emit(Theme())["rFonts"]
    assert fonts["cs"] == "Arial"
    assert "cstheme" not in fonts
    assert fonts["asciiTheme"] == "minorHAnsi"
    east = run_props(**{"font-east-asian": "MS Mincho"}).emit(Theme())["rFonts"]
    assert east == {"eastAsia": "MS Mincho"}


def test_paragraph_and_table_direction() -> None:
    assert para_props(rtl=True).emit(Theme()) == {"bidi": {}}
    document = part("\\p[rtl]{مرحبا}\n\n\\table[rtl]{\n| أ | ب |\n}")
    assert next(document.iter(f"{W}pPr")).find(f"{W}bidi") is not None
    assert next(document.iter(f"{W}tblPr")).find(f"{W}bidiVisual") is not None


def test_rtl_in_styles() -> None:
    styles = part(
        "\\styles{ paragraph Arabic : Normal { rtl; lang: ar-SA; font-cs: Arial; } }\n\n"
        "\\p[style=Arabic]{مرحبا}",
        "word/styles.xml",
    )
    (arabic,) = [s for s in styles.iter(f"{W}style") if s.get(f"{W}styleId") == "Arabic"]
    assert arabic.find(f"{W}pPr/{W}bidi") is not None  # the paragraph's direction
    assert arabic.find(f"{W}rPr/{W}rtl") is None
    assert arabic.find(f"{W}rPr/{W}lang").get(f"{W}bidi") == "ar-SA"  # pyright: ignore[reportOptionalMemberAccess]
    assert errors("\\styles{ character Hebrew { rtl; } }") == [
        "Word keeps no right-to-left setting in character styles"
    ]


def test_sections() -> None:
    document = part("\\section[rtl, gutter=1cm, text-direction=vertical]\n\nText.")
    section = next(document.iter(f"{W}sectPr"))
    assert section.find(f"{W}bidi") is not None
    assert section.find(f"{W}rtlGutter") is not None
    assert section.find(f"{W}textDirection").get(f"{W}val") == "tbRl"  # pyright: ignore[reportOptionalMemberAccess]


def test_complex_document_language() -> None:
    settings = part("\\document[lang=ar-SA]\n\nمرحبا", "word/settings.xml")
    language = settings.find(f"{W}themeFontLang")
    assert language is not None
    assert (language.get(f"{W}val"), language.get(f"{W}bidi")) == ("en-US", "ar-SA")
