from pathlib import Path
from typing import Any

import docx  # python-docx, for read-back
import pytest

from redocx import ir
from redocx.ooxml.parts.styles import StyleError
from redocx.ooxml.style_names import stored_name, style_id
from redocx.ooxml.writer import to_docx_bytes, write_docx
from redocx.props import para_props
from tests.golden.cases import styled


@pytest.mark.parametrize(
    ("name", "stored", "sid"),
    [
        ("Heading 1", "heading 1", "Heading1"),
        ("heading 2", "heading 2", "Heading2"),
        ("Caption", "caption", "Caption"),
        ("TOC 1", "toc 1", "TOC1"),
        ("Normal Table", "Normal Table", "TableNormal"),
        ("table of figures", "table of figures", "TableofFigures"),
        ("Body Text", "Body Text", "BodyText"),
        ("Callout title", "Callout title", "Callouttitle"),
        ("Code (inline)", "Code (inline)", "Codeinline"),
    ],
)
def test_style_names_and_ids(name: str, stored: str, sid: str) -> None:
    assert stored_name(name) == stored
    assert style_id(name) == sid


def test_undefined_paragraph_style_is_an_error() -> None:
    doc = ir.Document(body=(ir.paragraph("x", style="Heading 7"),))
    with pytest.raises(StyleError, match="paragraph style 'Heading 7' is not defined"):
        to_docx_bytes(doc)


def test_style_kind_mismatch_is_an_error() -> None:
    doc = ir.Document(
        styles=(ir.character_style("Term", italic=True),),
        body=(ir.paragraph("x", style="Term"),),
    )
    with pytest.raises(StyleError, match="is a character style, not paragraph"):
        to_docx_bytes(doc)


def test_duplicate_and_cyclic_styles_are_errors() -> None:
    dup = ir.Document(styles=(ir.paragraph_style("Body"), ir.paragraph_style("Body")))
    with pytest.raises(StyleError, match="defined twice"):
        to_docx_bytes(dup)
    cyclic = ir.Document(
        styles=(ir.paragraph_style("A", based_on="B"), ir.paragraph_style("B", based_on="A"))
    )
    with pytest.raises(StyleError, match="cyclic"):
        to_docx_bytes(cyclic)


def test_python_docx_reads_styles_and_text(tmp_path: Path) -> None:
    path = tmp_path / "styled.docx"
    write_docx(styled(), path)
    document = docx.Document(str(path))
    first = document.paragraphs[0]
    assert first.text == "Styled document"
    assert first.style is not None
    assert first.style.name == "Heading 1"
    term = document.paragraphs[1].runs[1].style
    assert term is not None
    assert term.name == "Term"
    heading: Any = document.styles["Heading 1"]  # python-docx's style types are loosely typed
    assert heading.font.size.pt == 16


def test_tabs_and_line_breaks_become_elements() -> None:
    doc = ir.Document(body=(ir.paragraph("a\tb\nc  d", align="left"),))
    xml = _part(to_docx_bytes(doc), "word/document.xml")
    assert '<w:t>a</w:t><w:tab/><w:t>b</w:t><w:br/><w:t xml:space="preserve">c  d</w:t>' in xml
    assert '<w:jc w:val="left"/>' in xml


def test_default_run_properties_include_theme_fonts() -> None:
    xml = _part(
        to_docx_bytes(ir.Document(default_paragraph=para_props(space_after="0pt"))),
        "word/styles.xml",
    )
    assert 'w:asciiTheme="minorHAnsi"' in xml
    assert '<w:sz w:val="22"/>' in xml
    assert '<w:spacing w:after="0"/>' in xml


def _part(docx_bytes: bytes, name: str) -> str:
    import zipfile
    from io import BytesIO

    with zipfile.ZipFile(BytesIO(docx_bytes)) as archive:
        return archive.read(name).decode("utf-8")
