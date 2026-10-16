"""Text boxes: wps shapes with blocks inside, inline or floating (M4 step 4)."""

from __future__ import annotations

import io
import zipfile

from lxml import etree

from tests.support.xsd import validate_package
from wordgenx import ir
from wordgenx.compiler import build, compile_source
from wordgenx.values import Border, Color, Length

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
WP = "{http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing}"
WPS = "{http://schemas.microsoft.com/office/word/2010/wordprocessingShape}"
A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
MC = "{http://schemas.openxmlformats.org/markup-compatibility/2006}"
A4_TEXT_WIDTH = Length.mm(210).emu - 2 * Length.cm("2.54").emu


def box(text: str) -> ir.TextBox:
    result = compile_source(text)
    assert result.document is not None, result.render_diagnostics()
    assert not result.diagnostics, result.render_diagnostics()
    found = [
        i for p in result.document.all_paragraphs() for i in p.content if isinstance(i, ir.TextBox)
    ]
    return found[0]


def errors(text: str) -> list[str]:
    return [d.message for _, d in compile_source(text).diagnostics if d.is_error]


def document_xml(text: str) -> etree._Element:  # pyright: ignore[reportPrivateUsage]
    data, result = build(text)
    assert data is not None, result.render_diagnostics()
    assert validate_package(data) == {}
    return etree.fromstring(zipfile.ZipFile(io.BytesIO(data)).read("word/document.xml"))


def test_defaults() -> None:
    made = box("\\textbox{Hello.}")
    assert made.width.emu == A4_TEXT_WIDTH
    assert made.height is None
    assert (made.fill, made.border, made.anchor, made.valign) == (None, None, None, "top")
    (paragraph,) = made.content
    assert isinstance(paragraph, ir.Paragraph)
    assert paragraph.text() == "Hello."


def test_options() -> None:
    made = box(
        "\\textbox[width=6cm, height=3cm, fill=#FFF2CC, border=(single, 1pt), padding=(all=0.2cm,"
        " left=0.5cm), valign=bottom, rotate=5, wrap=square, x=right]{A\n\nB}"
    )
    assert (made.width, made.height) == (Length.cm(6), Length.cm(3))
    assert made.fill == Color(rgb="FFF2CC")
    assert made.border == Border("single", Length.pt(1))
    assert made.padding == (Length.cm("0.2"), Length.cm("0.2"), Length.cm("0.5"), Length.cm("0.2"))
    assert (made.valign, made.rotation) == ("bottom", 5)
    assert made.anchor == ir.Anchor(x="right")
    assert len(made.content) == 2


def test_written_as_word_writes_it() -> None:
    document = document_xml(
        "Before \\textbox[width=5cm, fill=theme.accent1.lighter(40%)]{Inside.} after."
    )
    (alternate,) = document.iter(f"{MC}AlternateContent")
    (choice,) = alternate
    assert choice.get("Requires") == "wps"
    shape = choice.find(f"{W}drawing/{WP}inline/{A}graphic/{A}graphicData/{WPS}wsp")
    assert shape is not None
    texts = [t.text for t in shape.iter(f"{W}t")]
    assert texts == ["Inside."]
    fill = shape.find(f"{WPS}spPr/{A}solidFill/{A}schemeClr")
    assert fill is not None
    assert fill.get("val") == "accent1"
    assert [(etree.QName(m).localname, m.get("val")) for m in fill] == [
        ("lumMod", "60000"),
        ("lumOff", "40000"),
    ]
    assert shape.find(f"{WPS}spPr/{A}ln/{A}noFill") is not None  # no border
    assert shape.find(f"{WPS}bodyPr/{A}spAutoFit") is not None  # grows to fit


def test_floating_text_box() -> None:
    document = document_xml(
        "\\textbox[width=5cm, height=2cm, wrap=tight, y=(top, from=margin)]{X}\nText."
    )
    anchor = next(document.iter(f"{WP}anchor"))
    assert anchor.find(f"{WP}wrapTight") is not None
    assert anchor.findtext(f"{WP}positionV/{WP}align") == "top"
    assert next(document.iter(f"{WPS}bodyPr")).find(f"{A}noAutofit") is not None


def test_references_and_styles_inside_work() -> None:
    result = compile_source(
        "\\textbox{\\p[id=p:in]{Boxed.}}\n\nSee @p:in.\n\n= Heading {#sec:h}\n\n\\textbox{@sec:h}"
    )
    assert result.document is not None, result.render_diagnostics()
    assert not result.diagnostics, result.render_diagnostics()


def test_text_box_errors() -> None:
    assert errors("\\textbox{A\\footnote{n}}") != []
    assert errors("\\textbox[valign=middle]{A}") != []
    assert errors("\\figure{\\textbox{A}\n\\caption{C}}") != []
