"""Paragraph borders and shading; custom document properties and DOCPROPERTY fields."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

from lxml import etree

from tests.support.xsd import validate_package
from wordgenx import ir
from wordgenx.compiler import build, compile_source
from wordgenx.props import para_props
from wordgenx.values import Border, Color, Length

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
CUSTOM = "{http://schemas.openxmlformats.org/officeDocument/2006/custom-properties}"
VT = "{http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes}"


def compiled(text: str) -> ir.Document:
    result = compile_source(text)
    assert result.document is not None, result.render_diagnostics()
    assert not result.diagnostics, result.render_diagnostics()
    return result.document


def built(text: str, base_dir: Path | None = None, template: Path | None = None) -> zipfile.ZipFile:
    data, result = build(text, base_dir, template)
    assert data is not None, result.render_diagnostics()
    assert validate_package(data) == {}
    return zipfile.ZipFile(io.BytesIO(data))


# --- paragraph borders and shading ---------------------------------------------------------------


def test_paragraph_style_borders_and_shading() -> None:
    doc = compiled(
        "\\styles{\n"
        "  paragraph Quote : Normal { border-left: (single, 3pt, theme.accent1, space=6pt); "
        "shading: #F2F2F2; }\n"
        "  paragraph Box : Normal { borders: (all=(single, 0.75pt), between=(dotted, 0.5pt)); }\n"
        "}\n\n\\p[style=Quote]{q}\n\n\\p[style=Box]{b}"
    )
    styles = {s.name: s for s in doc.styles}
    quote = styles["Quote"].paragraph
    assert quote["border-left"] == Border(
        "single", Length.pt(3), Color.parse("theme.accent1"), Length.pt(6)
    )
    assert quote["shading"] == Color.parse("#F2F2F2")
    box = styles["Box"].paragraph
    assert {k for k in box if k.startswith("border-")} == {
        "border-top",
        "border-left",
        "border-bottom",
        "border-right",
        "border-between",
    }


def test_direct_paragraph_borders() -> None:
    doc = compiled("\\p[borders=(bottom=(double, 1.5pt)), shading=theme.accent2.lighter(80%)]{x}")
    (paragraph,) = doc.all_paragraphs()
    assert paragraph.props == para_props(
        border_bottom="(double, 1.5pt)", shading="theme.accent2.lighter(80%)"
    )


def test_paragraph_borders_are_written_in_schema_order() -> None:
    package = built(
        "\\styles{ paragraph Box : Normal { borders: (bar=(single, 1pt), all=(single, 0.5pt)); "
        "shading: #EEEEEE; } }\n\n\\p[style=Box]{x}"
    )
    styles = etree.fromstring(package.read("word/styles.xml"))
    (box,) = [s for s in styles.findall(f"{W}style") if s.get(f"{W}styleId") == "Box"]
    pbdr = box.find(f"{W}pPr/{W}pBdr")
    assert pbdr is not None
    assert [etree.QName(e).localname for e in pbdr] == ["top", "left", "bottom", "right", "bar"]
    assert box.find(f"{W}pPr/{W}shd") is not None


def test_table_style_borders_stay_table_borders() -> None:
    doc = compiled(
        '\\styles{ table T : "Table Grid" { borders: none; shading: #EEEEEE; } }\n\n\\table[style=T]{| a |}'
    )
    style = next(s for s in doc.styles if s.name == "T")
    assert "border-top" in style.table
    assert "shading" in style.cell
    assert not style.paragraph


# --- custom document properties ----------------------------------------------------------------


def test_custom_properties_are_typed_by_their_form() -> None:
    doc = compiled(
        '\\meta[custom.project = "WX-001", custom.revision = 3, custom.ratio = 0.5, '
        'custom.approved = true, custom.code = abc, custom.quoted = "7"]'
    )
    assert doc.meta.custom == (
        ("project", "WX-001"),
        ("revision", 3),
        ("ratio", 0.5),
        ("approved", True),
        ("code", "abc"),
        ("quoted", "7"),
    )


def test_custom_properties_part() -> None:
    package = built(
        '\\meta[custom.project = "WX-001", custom.revision = 3, custom.approved = true]\n\nx'
    )
    root = etree.fromstring(package.read("docProps/custom.xml"))
    entries = [(p.get("name"), p.get("pid"), etree.QName(p[0]).localname, p[0].text) for p in root]
    assert entries == [
        ("project", "2", "lpwstr", "WX-001"),
        ("revision", "3", "i4", "3"),
        ("approved", "4", "bool", "true"),
    ]
    assert "custom-properties" in package.read("_rels/.rels").decode()


def test_docproperty_fields_show_the_value() -> None:
    doc = compiled('\\meta[custom.project = "WX-001"]\n\nCode: \\field{DOCPROPERTY "project"}')
    (paragraph,) = doc.all_paragraphs()
    field = paragraph.content[-1]
    assert isinstance(field, ir.Field)
    assert (field.result, field.dirty) == ("WX-001", False)


def test_template_custom_properties_are_merged(tmp_path: Path) -> None:
    data, result = build(
        '\\meta[custom.owner = "Legal", custom.project = "old"]', tmp_path, as_template=True
    )
    assert data is not None, result.render_diagnostics()
    template = tmp_path / "t.dotx"
    template.write_bytes(data)
    package = built('\\meta[custom.project = "WX-001"]\n\nx', tmp_path, template)
    root = etree.fromstring(package.read("docProps/custom.xml"))
    values = {p.get("name"): p[0].text for p in root}
    assert values == {"owner": "Legal", "project": "WX-001"}
    pids = [p.get("pid") for p in root]
    assert len(pids) == len(set(pids))
    # without properties of its own, the document keeps the template's part as it is
    package = built("x", tmp_path, template)
    assert any(n.endswith("custom.xml") for n in package.namelist())
