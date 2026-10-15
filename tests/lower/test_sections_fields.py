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
from wordgenx.lower.fields import format_date, parse_instruction
from wordgenx.props import para_props
from wordgenx.values import Length, TabStop

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def compiled(text: str, base_dir: Path | None = None) -> ir.Document:
    result = compile_source(text, base_dir)
    assert result.document is not None, result.render_diagnostics()
    return result.document


def part(data: bytes, name: str) -> etree._Element:  # pyright: ignore[reportPrivateUsage]
    return etree.fromstring(zipfile.ZipFile(io.BytesIO(data)).read(name))


def built(text: str, base_dir: Path | None = None) -> bytes:
    data, result = build(text, base_dir)
    assert data is not None, result.render_diagnostics()
    assert validate_package(data) == {}
    return data


# --- sections --------------------------------------------------------------------------------


def test_preamble_section_sets_up_the_first_section() -> None:
    doc = compiled("\\section[size=A5, page-numbers=lower-roman, title-page]\n\nText")
    assert not [b for b in doc.body if isinstance(b, ir.SectionBreak)]
    section = doc.section
    assert (section.page.width, section.page_number_format, section.title_page) == (
        Length.mm(148),
        "lower-roman",
        True,
    )


def test_section_after_content_breaks() -> None:
    doc = compiled(
        "\\section[title-page]\n\nOne\n\n"
        "\\section[orientation=landscape, break=continuous, page-numbers=(format=decimal, start=1), "
        "columns=(count=2, gap=1cm, separator)]\n\nTwo"
    )
    first, brk, second = doc.body
    assert isinstance(brk, ir.SectionBreak)
    assert brk.section.title_page
    assert not brk.section.page.landscape
    new = doc.section
    assert new.page.landscape
    assert (new.start, new.page_number_format, new.page_number_start) == (
        "continuous",
        "decimal",
        1,
    )
    assert (new.columns, new.column_gap, new.column_separator) == (2, Length.cm(1), True)
    assert not new.title_page  # a different first page is per section
    assert isinstance(first, ir.Paragraph)
    assert isinstance(second, ir.Paragraph)


def test_headers_and_footers() -> None:
    doc = compiled(
        "\\header{Running head \\tab \\pagenum}\n\\header[first]{}\n\\footer[even]{Even footer}\n\nBody\n\n"
        "\\section[link-headers=false]\n\nMore"
    )
    brk = doc.body[1]
    assert isinstance(brk, ir.SectionBreak)
    first_section = brk.section
    assert [kind for kind, _ in first_section.headers] == ["default", "first"]
    assert first_section.title_page  # implied by the first-page header
    (default_header,) = (blocks for kind, blocks in first_section.headers if kind == "default")
    (paragraph,) = default_header
    assert isinstance(paragraph, ir.Paragraph)
    assert paragraph.style == "Header"
    assert isinstance(paragraph.content[-1], ir.Field)
    assert doc.settings.even_and_odd_headers  # implied by the even footer
    # link-headers=false: the new section gets explicit empty parts of every kind used so far
    assert doc.section.headers == (("default", ()), ("first", ()))
    assert doc.section.footers == (("even", ()),)
    header_style = next(s for s in doc.styles if s.name == "Header")
    width = doc.page.text_width
    assert header_style.paragraph["tabs"] == (
        TabStop(Length(width.emu // 2), "center"),
        TabStop(width, "right"),
    )


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("\\header[odd]{x}", "unknown header kind `odd`"),
        ("\\table{\\row{\\cell{\\section[size=A4]}}}", "\\section cannot appear inside"),
        ("\\header{\\section[size=A4]}", "\\section cannot appear inside"),
        ("\\section[break=sometimes]", "unknown section break"),
        ("\\section[page-numbers=klingon]", "invalid page-numbers"),
        ("\\section[columns=(count=99)]", "invalid columns"),
    ],
)
def test_section_errors(text: str, message: str) -> None:
    result = compile_source(text)
    assert any(message in d.message for _, d in result.diagnostics), result.render_diagnostics()


# --- the writer --------------------------------------------------------------------------------


def test_section_properties_go_into_the_last_paragraph() -> None:
    data = built(
        "One\n\n\\section[orientation=landscape]\n\n| a |\n\n\\section[orientation=portrait]\n\nThree"
    )
    body = part(data, "word/document.xml").find(f"{W}body")
    assert body is not None
    children = [etree.QName(child).localname for child in body]
    assert children == ["p", "tbl", "p", "p", "sectPr"]
    first, _, after_table, _, final = list(body)
    assert first.find(f"{W}pPr/{W}sectPr") is not None  # attached to "One"
    assert after_table.find(f"{W}pPr/{W}sectPr/{W}pgSz").get(f"{W}orient") == "landscape"  # type: ignore[union-attr]
    assert final.find(f"{W}pgSz").get(f"{W}orient") is None  # type: ignore[union-attr]


def test_header_parts_have_their_own_relationships(tmp_path: Path) -> None:
    PILImage.new("RGB", (40, 20), "red").save(tmp_path / "logo.png")
    data = built(
        "\\header{\\image[height=0.5cm]{logo.png} [site](https://x.org)}\n\\footer{\\image[height=0.5cm]{logo.png}}\n\n"
        "Body \\image[height=1cm]{logo.png}",
        tmp_path,
    )
    names = zipfile.ZipFile(io.BytesIO(data)).namelist()
    assert {
        "word/header1.xml",
        "word/footer1.xml",
        "word/_rels/header1.xml.rels",
        "word/_rels/footer1.xml.rels",
    } <= set(names)
    assert [n for n in names if n.startswith("word/media/")] == [
        "word/media/image1.png"
    ]  # stored once
    header_rels = zipfile.ZipFile(io.BytesIO(data)).read("word/_rels/header1.xml.rels").decode()
    assert "../media/image1.png" not in header_rels
    assert "media/image1.png" in header_rels
    assert "https://x.org" in header_rels


def test_fields_are_complex_fields() -> None:
    data = built('\\field{IF { PAGE } = 1 "first" "other"} and \\pagenum')
    runs = part(data, "word/document.xml").iter(f"{W}r")
    kinds: list[str | None] = []
    for run in runs:
        for child in run:
            tag = etree.QName(child).localname
            if tag == "fldChar":
                kinds.append(child.get(f"{W}fldCharType"))
            elif tag == "instrText":
                kinds.append(f"code:{child.text}")
    assert kinds[:6] == ["begin", "code: IF ", "begin", "code: PAGE ", "separate", "end"]
    assert kinds[6] == 'code: = 1 "first" "other" '


# --- fields in lowering -------------------------------------------------------------------------


def _fields(doc: ir.Document) -> list[ir.Field]:
    return [i for p in doc.all_paragraphs() for i in p.content if isinstance(i, ir.Field)]


def test_field_commands() -> None:
    doc = compiled(
        '\\meta[title="My Title"]\n\n\\pagenum \\numpages \\sectionpages \\field{TITLE} '
        '\\field{DOCPROPERTY Client} \\field[result="cached"]{AUTHOR}'
    )
    page, pages, section_pages, title, prop, author = _fields(doc)
    assert (page.code, page.result, page.dirty) == (" PAGE ", "1", False)
    assert pages.code == " NUMPAGES "
    assert section_pages.code == " SECTIONPAGES "
    assert (title.result, title.dirty) == ("My Title", False)
    assert (prop.result, prop.dirty) == ("", True)  # unknown in advance: Word updates it on open
    assert author.result == "cached"


def test_date(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SOURCE_DATE_EPOCH", "1792065600")  # 2026-10-15 12:00 UTC
    doc = compiled('\\date \\date[kind=today, format="dd/MM/yyyy"] \\date[kind=created]')
    (paragraph,) = doc.all_paragraphs()
    assert paragraph.content[0] == ir.Run("15 October 2026 ")
    today, created = _fields(doc)
    assert (today.code, today.result) == (' DATE \\@ "dd/MM/yyyy" ', "15/10/2026")
    assert created.code.startswith(" CREATEDATE")


def test_format_date() -> None:
    moment = datetime(2026, 3, 7, 14, 5, 9)
    assert format_date(moment, "dddd, d MMMM yyyy") == "Saturday, 7 March 2026"
    assert format_date(moment, "ddd dd MMM yy, h:mm am/pm") == "Sat 07 Mar 26, 2:05 pm"
    assert format_date(moment, "HH:mm:ss 'at' 'yyyy'") == "14:05:09 at yyyy"


def test_parse_instruction_nesting() -> None:
    parts = parse_instruction('IF { MERGEFIELD Name } <> "" "Dear { MERGEFIELD Name }"')
    assert isinstance(parts[1], ir.Field)
    assert parts[1].code == " MERGEFIELD Name "
    with pytest.raises(ValueError, match="unbalanced"):
        parse_instruction("IF { PAGE")


def test_field_errors() -> None:
    result = compile_source("\\field{} and \\field[bogus=1]{PAGE}")
    messages = [d.message for _, d in result.diagnostics]
    assert "\\field needs a field code: \\field{PAGE}" in messages
    assert any("unknown \\field option" in m for m in messages)


# --- tab stops -----------------------------------------------------------------------------------


def test_tabs_command_and_property() -> None:
    doc = compiled(
        '\\tabs[(8cm, center), (16cm, right, leader=dot)]{Left\\tab Mid\\tab Right}\n\n\\p[tabs="4cm decimal"]{x}'
    )
    first, second = doc.all_paragraphs()
    assert first.props == para_props(
        tabs=(TabStop(Length.cm(8), "center"), TabStop(Length.cm(16), "right", "dot"))
    )
    assert second.props == para_props(tabs="4cm decimal")


def test_tab_stops_are_written_sorted() -> None:
    data = built("\\tabs[(16cm, right, leader=dot), (8cm, center)]{x}")
    tabs = [
        (t.get(f"{W}val"), t.get(f"{W}pos"), t.get(f"{W}leader"))
        for t in part(data, "word/document.xml").iter(f"{W}tab")
    ]
    assert tabs == [("center", "4535", None), ("right", "9071", "dot")]
