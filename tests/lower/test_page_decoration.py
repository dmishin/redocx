"""Page borders, the page colour, borders and shading on runs (M4 step 1)."""

from __future__ import annotations

import io
import zipfile

from lxml import etree

from tests.support.xsd import validate_package
from wordgenx import ir
from wordgenx.compiler import build, compile_source
from wordgenx.values import Border, Color, Length

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def compiled(text: str) -> ir.Document:
    result = compile_source(text)
    assert result.document is not None, result.render_diagnostics()
    assert not result.diagnostics, result.render_diagnostics()
    return result.document


def errors(text: str) -> list[str]:
    return [d.message for _, d in compile_source(text).diagnostics if d.is_error]


def part(text: str, name: str) -> etree._Element:  # pyright: ignore[reportPrivateUsage]
    data, result = build(text)
    assert data is not None, result.render_diagnostics()
    assert validate_package(data) == {}
    return etree.fromstring(zipfile.ZipFile(io.BytesIO(data)).read(name))


def test_one_border_for_all_sides() -> None:
    doc = compiled("\\section[borders=(single, 1.5pt, theme.accent1)]\n\nText.")
    borders = doc.section.borders
    assert borders is not None
    line = Border("single", Length.pt("1.5"), Color(theme="accent1"))
    assert [side for _, side in borders.sides()] == [line] * 4
    assert (borders.offset_from, borders.pages) == ("page", "all")


def test_sides_offset_and_pages() -> None:
    doc = compiled(
        "\\section[borders=(all=(single, 0.5pt), left=none, top=(double, 1pt, space=10pt),"
        " from=text, pages=not-first)]\n\nText."
    )
    borders = doc.section.borders
    assert borders is not None
    assert [name for name, _ in borders.sides()] == ["top", "bottom", "right"]
    assert borders.top == Border("double", Length.pt(1), space=Length.pt(10))
    assert (borders.offset_from, borders.pages) == ("text", "not-first")


def test_a_new_section_inherits_or_replaces_the_borders() -> None:
    doc = compiled(
        "\\section[borders=(single, 1pt)]\n\nOne.\n\n\\section\n\nTwo.\n\n"
        "\\section[borders=(top=(single, 2pt))]\n\nThree.\n\n\\section[borders=none]\n\nFour."
    )
    first, second, third, fourth = doc.sections()
    assert first.borders is not None
    assert len(first.borders.sides()) == 4
    assert second.borders == first.borders
    assert third.borders is not None
    assert [n for n, _ in third.borders.sides()] == ["top"]
    assert fourth.borders is None


def test_page_borders_are_written_as_word_writes_them() -> None:
    document = part(
        "\\section[borders=(all=(single, 1.5pt, #1F4E79), from=text, pages=first)]\n\nX",
        "word/document.xml",
    )
    (borders,) = document.iter(f"{W}pgBorders")
    assert borders.get(f"{W}offsetFrom") == "text"
    assert borders.get(f"{W}display") == "firstPage"
    sides = [(etree.QName(s).localname, s.get(f"{W}sz"), s.get(f"{W}space")) for s in borders]
    assert sides == [(side, "12", "4") for side in ("top", "left", "bottom", "right")]
    # the default distance from the page edge is Word's 24pt
    document = part("\\section[borders=(single, 1pt)]\n\nX", "word/document.xml")
    (top,) = document.iter(f"{W}top")
    assert top.get(f"{W}space") == "24"


def test_page_border_errors() -> None:
    assert errors("\\section[borders=(all=(single, 1pt), middle=none)]") == [
        "`borders`: unknown page border part `middle`"
    ]
    assert errors("\\section[borders=(single, 1pt, space=40pt)]") == [
        "a page border is at most 31pt from the page edge or the text"
    ]
    assert errors("\\section[borders=(all=(single, 1pt), from=edge)]") != []


def test_page_color() -> None:
    doc = compiled("\\document[page-color=#FDF6E3]\n\nText.")
    assert doc.settings.page_color == Color(rgb="FDF6E3")
    document = part(
        "\\document[page-color=theme.accent1.lighter(80%)]\n\nText.", "word/document.xml"
    )
    background = document.find(f"{W}background")
    assert background is not None
    assert background.get(f"{W}themeColor") == "accent1"
    assert document.index(background) == 0  # before w:body
    settings = part("\\document[page-color=#FDF6E3]\n\nText.", "word/settings.xml")
    assert settings.find(f"{W}displayBackgroundShape") is not None


def test_run_border_and_shading() -> None:
    document = part(
        "A \\font[border=(single, 0.5pt, #C00000, space=1pt), shading=#FFF2CC]{box} b.",
        "word/document.xml",
    )
    (border,) = document.iter(f"{W}bdr")
    assert (border.get(f"{W}val"), border.get(f"{W}sz"), border.get(f"{W}space")) == (
        "single",
        "4",
        "1",
    )
    assert border.get(f"{W}color") == "C00000"
    (shading,) = document.iter(f"{W}shd")
    assert shading.get(f"{W}fill") == "FFF2CC"
    # in a character style too
    doc = compiled("\\styles{ character Key { border: (single, 0.5pt); shading: #EEEEEE; } }\n\nx")
    (style,) = [s for s in doc.styles if s.name == "Key"]
    assert style.run["border"] == Border("single", Length.pt("0.5"))
