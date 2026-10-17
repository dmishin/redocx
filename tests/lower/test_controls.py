"""Content controls: \\control (M5 step 3)."""

from __future__ import annotations

import io
import zipfile
from datetime import datetime
from pathlib import Path

import pytest
from lxml import etree
from PIL import Image as PILImage

from tests.support.xsd import validate_package
from wordgenx import ir
from wordgenx.compiler import build, compile_source
from wordgenx.values import Length

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
W14 = "{http://schemas.microsoft.com/office/word/2010/wordml}"
MC = "{http://schemas.openxmlformats.org/markup-compatibility/2006}"


def control(text: str) -> ir.ContentControl:
    result = compile_source(text)
    assert result.document is not None, result.render_diagnostics()
    assert not result.diagnostics, result.render_diagnostics()
    (found,) = [
        i
        for p in result.document.all_paragraphs()
        for i in p.content
        if isinstance(i, ir.ContentControl)
    ]
    return found


def errors(text: str) -> list[str]:
    return [d.message for _, d in compile_source(text).diagnostics if d.is_error]


def sdt(text: str) -> etree._Element:  # pyright: ignore[reportPrivateUsage]
    data, result = build(text)
    assert data is not None, result.render_diagnostics()
    assert validate_package(data) == {}
    document = etree.fromstring(zipfile.ZipFile(io.BytesIO(data)).read("word/document.xml"))
    return next(document.iter(f"{W}sdt"))


def texts(content: tuple[ir.Inline, ...]) -> list[tuple[str, str | None]]:
    return [(r.text, r.style) for r in content if isinstance(r, ir.Run)]


def test_text_with_a_placeholder() -> None:
    made = control('Name: \\control[tag=name, title="Full name", placeholder="Your name"]')
    assert (made.kind, made.tag, made.title, made.showing_placeholder) == (
        "text",
        "name",
        "Full name",
        True,
    )
    assert texts(made.content) == [("Your name", "Placeholder Text")]
    element = sdt("\\control[tag=name, title=Name]")
    properties = element.find(f"{W}sdtPr")
    assert properties is not None
    names = [etree.QName(c).localname for c in properties]
    assert names == ["alias", "tag", "id", "showingPlcHdr", "text"]
    run = element.find(f"{W}sdtContent/{W}r")
    assert run is not None
    assert run.findtext(f"{W}t") == "Click or tap here to enter text."  # Word's own


def test_value_rich_text_and_lock() -> None:
    made = control("\\control[rich-text, lock=both]{With \\b{bold}.}")
    assert not made.showing_placeholder
    assert [r.text for r in made.content if isinstance(r, ir.Run)] == ["With ", "bold", "."]
    properties = sdt("\\control[rich-text, lock=content]{x}").find(f"{W}sdtPr")
    assert properties is not None
    assert properties.find(f"{W}lock").get(f"{W}val") == "contentLocked"  # pyright: ignore[reportOptionalMemberAccess]
    assert properties.find(f"{W}text") is None  # rich text has no type element


@pytest.mark.parametrize(("kind", "element"), [("dropdown", "dropDownList"), ("combo", "comboBox")])
def test_lists(kind: str, element: str) -> None:
    properties = sdt(f'\\control[{kind}, items=("A", "B c")]').find(f"{W}sdtPr")
    assert properties is not None
    entries = properties.findall(f"{W}{element}/{W}listItem")
    assert [(e.get(f"{W}displayText"), e.get(f"{W}value")) for e in entries] == [
        ("A", "A"),
        ("B c", "B c"),
    ]


def test_date() -> None:
    made = control('\\control[date, format="d MMM yyyy", date=2026-11-02]')
    assert made.date == datetime(2026, 11, 2)
    assert texts(made.content) == [("2 Nov 2026", None)]
    date = sdt("\\control[date, date=2026-11-02]").find(f"{W}sdtPr/{W}date")
    assert date is not None
    assert date.get(f"{W}fullDate") == "2026-11-02T00:00:00Z"
    assert date.find(f"{W}dateFormat").get(f"{W}val") == "d MMMM yyyy"  # pyright: ignore[reportOptionalMemberAccess]


def test_checkbox_is_word_2010s_and_ignorable() -> None:
    made = control("\\control[checkbox, checked] Laptop issued.")
    assert texts(made.content)[0][0] == "☒"
    data, result = build("\\control[checkbox] I agree.")
    assert data is not None, result.render_diagnostics()
    assert validate_package(data) == {}
    document = etree.fromstring(zipfile.ZipFile(io.BytesIO(data)).read("word/document.xml"))
    assert document.get(f"{MC}Ignorable") == "w14"
    box = next(document.iter(f"{W14}checkbox"))
    assert box.find(f"{W14}checked").get(f"{W14}val") == "0"  # pyright: ignore[reportOptionalMemberAccess]
    plain, _ = build("No checkbox.")
    assert plain is not None
    assert b"mc:Ignorable" not in zipfile.ZipFile(io.BytesIO(plain)).read("word/document.xml")


def test_picture_control(tmp_path: Path) -> None:
    empty = control("\\control[picture, tag=photo, width=3cm, height=4cm]")
    assert empty.showing_placeholder
    (frame,) = empty.content
    assert isinstance(frame, ir.Image)
    assert (frame.width, frame.height) == (Length.cm(3), Length.cm(4))
    assert sdt("\\control[picture]").find(f"{W}sdtPr/{W}picture") is not None
    PILImage.new("RGB", (40, 20), "red").save(tmp_path / "sig.png")
    result = compile_source("\\control[picture]{\\image[width=4cm]{sig.png}}", tmp_path)
    assert result.document is not None, result.render_diagnostics()
    (filled,) = [
        i
        for p in result.document.all_paragraphs()
        for i in p.content
        if isinstance(i, ir.ContentControl)
    ]
    assert not filled.showing_placeholder
    assert errors("\\control[picture]{text}") == ["a picture control holds one \\image"]


def test_control_errors() -> None:
    assert errors("\\control[dropdown]") == ["a dropdown needs its choices: items=(...)"]
    assert errors("\\control[checkbox]{label}") == [
        "a checkbox has no value text; write its label beside it"
    ]
    assert errors("\\control[date, date=2/11/2026]") == [
        "`date`: expected YYYY-MM-DD, got `2/11/2026`"
    ]
    assert errors("\\control[slider]") != []
