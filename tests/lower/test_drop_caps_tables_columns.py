"""Drop caps, floating tables and columns of different widths (M4 step 5)."""

from __future__ import annotations

import io
import zipfile

import pytest
from lxml import etree

from tests.support.xsd import validate_package
from wordgenx import ir
from wordgenx.compiler import build, compile_source
from wordgenx.values import Length

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def compiled(text: str) -> ir.Document:
    result = compile_source(text)
    assert result.document is not None, result.render_diagnostics()
    assert not result.diagnostics, result.render_diagnostics()
    return result.document


def errors(text: str) -> list[str]:
    return [d.message for _, d in compile_source(text).diagnostics if d.is_error]


def document_xml(text: str) -> etree._Element:  # pyright: ignore[reportPrivateUsage]
    data, result = build(text)
    assert data is not None, result.render_diagnostics()
    assert validate_package(data) == {}
    return etree.fromstring(zipfile.ZipFile(io.BytesIO(data)).read("word/document.xml"))


# --- drop caps ---------------------------------------------------------------------------


def test_drop_cap_splits_the_first_letter() -> None:
    frame, rest = compiled("\\p[drop-cap, id=p:x]{Lorem ipsum.}").body
    assert isinstance(frame, ir.Paragraph)
    assert isinstance(rest, ir.Paragraph)
    assert frame.drop_cap == ir.DropCap(3, in_margin=False)
    assert frame.text() == "L"
    assert rest.text() == "orem ipsum."
    assert frame.props["keep-with-next"] is True


@pytest.mark.parametrize(
    ("lines", "line", "size", "position"),
    [(2, "622", "74", "-2"), (3, "933", "123", "-8"), (4, "1244", "172", "-14")],
)
def test_drop_cap_sizes_match_word(lines: int, line: str, size: str, position: str) -> None:
    # The values Word writes for an 11pt single-spaced body (Format > Drop Cap).
    document = document_xml(f"\\p[drop-cap={lines}]{{Lorem ipsum dolor.}}")
    first = next(document.iter(f"{W}p"))
    frame = first.find(f"{W}pPr/{W}framePr")
    assert frame is not None
    assert dict(frame.attrib) == {
        f"{W}dropCap": "drop",
        f"{W}lines": str(lines),
        f"{W}wrap": "around",
        f"{W}vAnchor": "text",
        f"{W}hAnchor": "text",
    }
    assert first.find(f"{W}pPr/{W}spacing").get(f"{W}line") == line  # pyright: ignore[reportOptionalMemberAccess]
    assert first.find(f"{W}pPr/{W}textAlignment") is not None
    run = first.find(f"{W}r/{W}rPr")
    assert run is not None
    assert run.find(f"{W}sz").get(f"{W}val") == size  # pyright: ignore[reportOptionalMemberAccess]
    assert run.find(f"{W}position").get(f"{W}val") == position  # pyright: ignore[reportOptionalMemberAccess]


def test_floating_objects_before_the_first_letter_stay_in_the_paragraph() -> None:
    frame, rest = compiled(
        "\\p[drop-cap]{\\textbox[width=3cm, wrap=square, x=right]{Box}\n\\font[bold]{W}ord.}"
    ).body
    assert isinstance(frame, ir.Paragraph)
    assert isinstance(rest, ir.Paragraph)
    assert frame.text() == "W"
    assert frame.runs()[0].props["bold"] is True  # the letter keeps its formatting
    assert isinstance(rest.content[0], ir.TextBox)
    assert rest.text() == "ord."


def test_percentages_are_of_the_column() -> None:
    doc = compiled("\\section[columns=(count=2, gap=1cm)]\n\n\\textbox[width=100%]{A}")
    (box,) = [i for p in doc.all_paragraphs() for i in p.content if isinstance(i, ir.TextBox)]
    text_width = doc.section.page.text_width
    assert box.width == Length((text_width.emu - Length.cm(1).emu) // 2)
    doc = compiled("\\section[columns=(widths=(10cm, 4cm))]\n\n\\textbox[width=50%]{A}")
    (box,) = [i for p in doc.all_paragraphs() for i in p.content if isinstance(i, ir.TextBox)]
    assert box.width == Length.cm(2)  # of the narrowest column


def test_drop_cap_in_the_margin_and_errors() -> None:
    document = document_xml("\\p[drop-cap=(lines=2, in-margin)]{Word.}")
    frame = next(document.iter(f"{W}framePr"))
    assert (frame.get(f"{W}dropCap"), frame.get(f"{W}hAnchor")) == ("margin", "page")
    assert errors("\\p[drop-cap]{\\m{x} starts with math}") == [
        "a drop cap needs the paragraph to start with a letter"
    ]
    assert errors("\\p[drop-cap=1]{A}") != []


# --- floating tables ------------------------------------------------------------------------


def test_floating_table() -> None:
    doc = compiled("\\table[x=(right, from=margin), y=1cm, distance=(left=0.5cm)]{\n| a |\n}")
    (table,) = doc.body
    assert isinstance(table, ir.Table)
    assert table.anchor is not None
    assert (table.anchor.x, table.anchor.x_from, table.anchor.y) == (
        "right",
        "margin",
        Length.cm(1),
    )
    document = document_xml("\\table[x=(2cm, from=page), y=(top, from=margin)]{\n| a |\n}")
    position = next(document.iter(f"{W}tblpPr"))
    assert position.get(f"{W}tblpX") == str(Length.cm(2).in_twips)
    assert position.get(f"{W}tblpYSpec") == "top"
    assert (position.get(f"{W}horzAnchor"), position.get(f"{W}vertAnchor")) == ("page", "margin")
    assert position.get(f"{W}leftFromText") == "180"  # Word's default 0.32cm


def test_floating_table_errors() -> None:
    assert errors("\\table[wrap=behind]{\n| a |\n}") == [
        "a floating table always has text around it"
    ]
    assert errors("\\table[x=(left, from=character)]{\n| a |\n}") == [
        "a floating table is placed against the text, the margin or the page"
    ]


# --- columns --------------------------------------------------------------------------------


def test_columns_of_different_widths() -> None:
    doc = compiled("\\section[columns=(widths=(10cm, 4cm), gap=1cm)]\n\nText.")
    assert doc.section.columns == 2
    assert doc.section.column_widths == (Length.cm(10), Length.cm(4))
    document = document_xml("\\section[columns=(widths=(10cm, 4cm), gap=1cm)]\n\nText.")
    columns = next(document.iter(f"{W}cols"))
    assert columns.get(f"{W}equalWidth") == "0"
    assert [(c.get(f"{W}w"), c.get(f"{W}space")) for c in columns] == [
        (str(Length.cm(10).in_twips), str(Length.cm(1).in_twips)),
        (str(Length.cm(4).in_twips), None),
    ]


def test_column_width_problems() -> None:
    assert errors("\\section[columns=(count=3, widths=(5cm, 5cm))]") == ["3 columns but 2 widths"]
    result = compile_source("\\section[columns=(widths=(12cm, 12cm))]\n\nText.")
    assert [d.message for _, d in result.diagnostics] == [
        "the columns and gaps are wider than the text"
    ]
