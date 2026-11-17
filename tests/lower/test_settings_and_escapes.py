"""Document protection, mirrored margins, decimal columns, chapter-numbered captions,
references in headers, and the escape hatches (\\raw, ooxml.* options)."""

from __future__ import annotations

import io
import zipfile

import pytest
from lxml import etree

from redocx import ir
from redocx.compiler import build, compile_source
from redocx.values import Length, TabStop
from tests.support.xsd import validate_package

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"

HEADINGS = (
    '\\styles{ paragraph "Heading 1" { outline-level: 1; }\n'
    '  numbering Headings { level 1 { style: "Heading 1"; text: "%1"; } } }\n\n'
)


def compiled(text: str) -> ir.Document:
    result = compile_source(text)
    assert result.document is not None, result.render_diagnostics()
    assert not result.diagnostics, result.render_diagnostics()
    return result.document


def errors(text: str) -> list[str]:
    return [d.message for _, d in compile_source(text).diagnostics if d.is_error]


def built(text: str) -> zipfile.ZipFile:
    data, result = build(text)
    assert data is not None, result.render_diagnostics()
    assert validate_package(data) == {}
    return zipfile.ZipFile(io.BytesIO(data))


def part(package: zipfile.ZipFile, name: str) -> etree._Element:  # pyright: ignore[reportPrivateUsage]
    return etree.fromstring(package.read(name))


# --- settings ---------------------------------------------------------------------------------------


def test_track_changes_and_hyphenation() -> None:
    doc = compiled("\\settings[track-changes, hyphenation=auto]\n\nText.")
    assert (doc.settings.track_changes, doc.settings.auto_hyphenation) == (True, True)
    settings = part(
        built("\\settings[track-changes, hyphenation=auto]\n\nText."), "word/settings.xml"
    )
    assert settings.find(f"{W}trackRevisions") is not None
    assert settings.find(f"{W}autoHyphenation") is not None
    off = part(built("\\settings[hyphenation=off]\n\nText."), "word/settings.xml")
    assert off.find(f"{W}autoHyphenation") is None
    assert off.find(f"{W}trackRevisions") is None


def test_protection() -> None:
    doc = compiled("\\settings[protection=(mode=forms, enforce)]")
    assert (doc.settings.protection, doc.settings.protection_enforced) == ("forms", True)
    package = built("\\settings[protection=read-only]\n\nx")
    protection = part(package, "word/settings.xml").find(f"{W}documentProtection")
    assert protection is not None
    assert (protection.get(f"{W}edit"), protection.get(f"{W}enforcement")) == ("readOnly", "0")
    assert errors("\\settings[protection=(mode=locked)]") == [
        "`protection`: unknown value `locked`"
    ]


def test_mirrored_margins() -> None:
    doc = compiled("\\section[margins=(inside=3cm, outside=2cm)]")
    assert doc.settings.mirror_margins
    assert (doc.section.page.margin_left, doc.section.page.margin_right) == (
        Length.cm(3),
        Length.cm(2),
    )
    assert compiled("\\section[mirror]").settings.mirror_margins
    package = built("\\section[mirror]\n\nx")
    assert part(package, "word/settings.xml").find(f"{W}mirrorMargins") is not None


# --- decimal columns -----------------------------------------------------------------------------------


def test_decimal_columns_get_decimal_tab_stops() -> None:
    doc = compiled(
        "\\table[header-rows=1, widths=(4cm, 3cm, 3cm), columns=(left, decimal, (decimal, tab=1cm))]{\n"
        "| Item | Q1 | Q2 |\n| Revenue | 10.5 | 1,200.25 |\n}"
    )
    (table,) = [b for b in doc.body if isinstance(b, ir.Table)]
    header, row = table.rows
    (head_q1,) = header.cells[1].content
    assert isinstance(head_q1, ir.Paragraph)
    assert head_q1.props.get("align") == "right"  # headings have no figures to line up
    (q1,) = row.cells[1].content
    (q2,) = row.cells[2].content
    assert isinstance(q1, ir.Paragraph) and isinstance(q2, ir.Paragraph)  # noqa: PT018
    default = Length(Length.cm(3).emu - Length.twips(216).emu - Length.cm("0.6").emu)
    assert q1.props.get("tabs") == (TabStop(default, "decimal"),)
    assert q2.props.get("tabs") == (TabStop(Length.cm(1), "decimal"),)
    assert q2.props.get("align") == "left"


# --- chapter-numbered captions ----------------------------------------------------------------------


def test_chapter_numbered_captions() -> None:
    doc = compiled(
        HEADINGS
        + '\\settings[captions=(chapter-numbers, chapter-style="Heading 1", separator="-")]\n\n'
        "= One\n\n\\caption[Figure, id=fig:a]{A}\n\n\\caption[Figure]{B}\n\n= Two\n\n"
        "\\caption[Figure, id=fig:c]{C}\n\n@fig:a @fig:c"
    )
    captions = [p.text() for p in doc.all_paragraphs() if p.style == "Caption"]
    assert captions == ["Figure 1-1: A", "Figure 1-2: B", "Figure 2-1: C"]
    codes = [
        i.code.strip()
        for p in doc.all_paragraphs()
        if p.style == "Caption"
        for i in p.content
        if isinstance(i, ir.Field)
    ][:2]
    assert codes == ['STYLEREF "Heading 1" \\s', "SEQ Figure \\* ARABIC \\s 1"]
    refs = [
        i.result
        for p in doc.all_paragraphs()
        for i in p.content
        if isinstance(i, ir.Field) and i.code.strip().startswith("REF")
    ]
    assert refs == ["Figure 1-1", "Figure 2-1"]


# --- references in headers and footers ------------------------------------------------------------------


def test_references_in_the_final_sections_header() -> None:
    doc = compiled("\\header{See @sec:a}\n\n= Alpha {#sec:a}")
    ((_, blocks),) = doc.section.headers
    (paragraph,) = blocks
    assert isinstance(paragraph, ir.Paragraph)
    fields = [i for i in paragraph.content if isinstance(i, ir.Field)]
    assert [(f.code.strip(), f.result) for f in fields] == [("REF _Ref_sec_a \\h", "Alpha")]


# --- escape hatches ----------------------------------------------------------------------------------


def test_raw_runs_and_blocks() -> None:
    package = built(
        "Before \\raw[ooxml]{<w:r><w:rPr><w:smallCaps/></w:rPr><w:t>raw run</w:t></w:r>} after.\n\n"
        "\\raw[ooxml]{<w:p><w:pPr><w:jc w:val='center'/></w:pPr><w:r><w:t>raw paragraph</w:t></w:r></w:p>}\n\n"
        "\\raw[ooxml]{<w:tbl><w:tblPr><w:tblW w:w='0' w:type='auto'/></w:tblPr><w:tblGrid><w:gridCol w:w='2000'/>"
        "</w:tblGrid><w:tr><w:tc><w:p/></w:tc></w:tr></w:tbl>}"
    )
    body = part(package, "word/document.xml").find(f"{W}body")
    assert body is not None
    texts = ["".join(t.text or "" for t in p.iter(f"{W}t")) for p in body.findall(f"{W}p")]
    assert texts[:2] == ["Before raw run after.", "raw paragraph"]
    assert body.find(f"{W}tbl") is not None
    assert body[-2].tag == f"{W}p"  # Word needs a paragraph after a final table


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("\\raw[ooxml]{<w:r><w:t>x</w:r>}", "not well-formed XML"),
        ("\\raw[ooxml]{<p>x</p>}", "must be WordprocessingML"),
        ("\\raw[ooxml]{<w:r/><w:p/>}", "either run-level"),
        ("\\raw[html]{<w:r/>}", "`format`: unknown value `html`"),
        ("\\raw[ooxml]{<w:r><w:bogus/></w:r>}", "Word would reject: `w:bogus` is not allowed here"),
        ("Text \\raw[ooxml]{<w:r><w:p/></w:r>}", "`w:p` is not allowed here"),
        ("\\raw[ooxml]{<w:p><w:pPr><w:jc w:val='up'/></w:pPr></w:p>}", "`up` is not a valid value"),
    ],
)
def test_raw_errors(text: str, message: str) -> None:
    assert any(message in e for e in errors(text)), errors(text)


def test_ooxml_property_options() -> None:
    package = built(
        "\\styles{ paragraph Tight : Normal { ooxml.pPr: \"<w:snapToGrid w:val='0'/>\"; } }\n\n"
        '\\p[style=Tight, ooxml.pPr="<w:suppressAutoHyphens/>"]{x}'
    )
    styles = part(package, "word/styles.xml")
    (tight,) = [s for s in styles.findall(f"{W}style") if s.get(f"{W}styleId") == "Tight"]
    assert tight.find(f"{W}pPr/{W}snapToGrid") is not None
    paragraph = part(package, "word/document.xml").find(f".//{W}p")
    assert paragraph is not None
    assert paragraph.find(f"{W}pPr/{W}suppressAutoHyphens") is not None
    assert any("is not a w:pPr property" in e for e in errors('\\p[ooxml.pPr="<w:b/>"]{x}'))
    bad = errors("\\p[ooxml.pPr=\"<w:ind w:left='wide'/>\"]{x}")
    assert any("`w:left` of `w:ind`: `wide` is not a valid value" in e for e in bad), bad


def test_raw_xml_of_other_namespaces_is_not_checked() -> None:
    text = (
        "\\raw[ooxml]{<w:r><w14:anything xmlns:w14="
        "'http://schemas.microsoft.com/office/word/2010/wordml'/><w:t>x</w:t></w:r>}"
    )
    assert errors(text) == []


# --- smaller properties ------------------------------------------------------------------------------


def test_line_numbers_and_vertical_alignment() -> None:
    doc = compiled(
        r"\section[line-numbers=(count-by=5, start=3, restart=each-section), valign=center]"
    )
    section = doc.section
    assert section.line_numbers == ir.LineNumbers(count_by=5, start=3, restart="each-section")
    assert section.vertical_align == "center"
    package = built(r"\section[line-numbers=on, valign=justify]" + "\n\nx")
    sect = part(package, "word/document.xml").find(f".//{W}sectPr")
    assert sect is not None
    numbers = sect.find(f"{W}lnNumType")
    assert numbers is not None
    assert (numbers.get(f"{W}countBy"), numbers.get(f"{W}start")) == ("1", "0")  # Word's offset
    valign = sect.find(f"{W}vAlign")
    assert valign is not None
    assert valign.get(f"{W}val") == "both"


def test_run_paragraph_and_cell_properties() -> None:
    package = built(
        r"\p[suppress-line-numbers, suppress-hyphenation]{\font[scale=150%, kerning=10pt, emboss, "
        r"outline, shadow]{x} \font[imprint]{y}}"
        "\n\n"
        r"| \cell[text-direction=up, cell-margins=(top=0.3cm)]{z} |"
    )
    document = part(package, "word/document.xml")
    ppr = document.find(f".//{W}p/{W}pPr")
    assert ppr is not None
    assert [etree.QName(c).localname for c in ppr] == ["suppressLineNumbers", "suppressAutoHyphens"]
    rpr = document.find(f".//{W}r/{W}rPr")
    assert rpr is not None
    assert [etree.QName(c).localname for c in rpr] == ["outline", "shadow", "emboss", "w", "kern"]
    tcpr = document.find(f".//{W}tcPr")
    assert tcpr is not None
    assert tcpr.find(f"{W}textDirection").get(f"{W}val") == "btLr"  # pyright: ignore[reportOptionalMemberAccess]
    assert tcpr.find(f"{W}tcMar/{W}top") is not None
