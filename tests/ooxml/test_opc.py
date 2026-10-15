import zipfile
from collections.abc import Callable
from io import BytesIO
from pathlib import Path

import docx  # python-docx, used only to read our output back
import pytest
from lxml import etree

from wordgenx.ooxml import CT, NS, RT, Package, PartNameError
from wordgenx.ooxml.opc import rels_part_name, validate_part_name

HelloFactory = Callable[[], Package]


def _zip(pkg: Package) -> zipfile.ZipFile:
    return zipfile.ZipFile(BytesIO(pkg.to_bytes()))


def _rels(archive: zipfile.ZipFile, name: str) -> list[dict[str, str]]:
    root = etree.fromstring(archive.read(name))
    return [dict(el.attrib) for el in root.iter(f"{{{NS.RELATIONSHIPS}}}Relationship")]


# --- part names --------------------------------------------------------------


@pytest.mark.parametrize("name", ["/word/document.xml", "/a", "/customXml/item1.xml"])
def test_valid_part_names(name: str) -> None:
    validate_part_name(name)


@pytest.mark.parametrize(
    "name", ["word/document.xml", "/word/", "/word//x.xml", "/word/x.", "/word/a b.xml", "/ä.xml"]
)
def test_invalid_part_names(name: str) -> None:
    with pytest.raises(PartNameError):
        validate_part_name(name)


def test_duplicate_part_names_are_case_insensitive() -> None:
    pkg = Package()
    pkg.add_part("/word/document.xml", CT.WML_DOCUMENT_MAIN)
    with pytest.raises(PartNameError, match="duplicate"):
        pkg.add_part("/Word/Document.xml", CT.WML_DOCUMENT_MAIN)


@pytest.mark.parametrize("name", ["/[Content_Types].xml", "/_rels/.rels", "/word/_rels/x.rels"])
def test_generated_parts_cannot_be_added(name: str) -> None:
    with pytest.raises(PartNameError, match="generated"):
        Package().add_part(name, CT.XML)


def test_rels_part_name() -> None:
    assert rels_part_name(None) == "/_rels/.rels"
    assert rels_part_name("/word/document.xml") == "/word/_rels/document.xml.rels"
    assert rels_part_name("/word/header1.xml") == "/word/_rels/header1.xml.rels"


# --- relationships -----------------------------------------------------------


def test_relationship_targets_are_relative_to_the_source(hello_package: HelloFactory) -> None:
    archive = _zip(hello_package())
    package_rels = _rels(archive, "_rels/.rels")
    assert [(r["Id"], r["Target"]) for r in package_rels] == [
        ("rId1", "word/document.xml"),
        ("rId2", "docProps/core.xml"),
    ]
    doc_rels = _rels(archive, "word/_rels/document.xml.rels")
    assert doc_rels[0] == {"Id": "rId1", "Type": RT.STYLES, "Target": "styles.xml"}


def test_relative_target_across_directories() -> None:
    pkg = Package()
    header = pkg.add_part("/word/header1.xml", CT.WML_HEADER)
    image = pkg.add_part("/media/image1.png", CT.PNG, b"\x89PNG")
    header.relate(image, RT.IMAGE)
    assert next(iter(header.rels)).target == "../media/image1.png"


def test_external_relationship_is_escaped(hello_package: HelloFactory) -> None:
    archive = _zip(hello_package())
    raw = archive.read("word/_rels/document.xml.rels")
    assert b"a=1&amp;b=2" in raw
    link = _rels(archive, "word/_rels/document.xml.rels")[1]
    assert link["TargetMode"] == "External"
    assert link["Target"] == "https://example.com/?a=1&b=2"


# --- content types -----------------------------------------------------------


def test_content_types_use_defaults_and_overrides() -> None:
    pkg = Package()
    doc = pkg.add_part("/word/document.xml", CT.WML_DOCUMENT_MAIN)
    pkg.add_part("/word/media/image1.png", CT.PNG, b"x")
    pkg.add_part("/customXml/item1.xml", CT.XML, b"<a/>")
    pkg.relate(doc, RT.OFFICE_DOCUMENT)
    root = etree.fromstring(pkg.content_types_xml())
    defaults = {
        el.get("Extension"): el.get("ContentType")
        for el in root.iter(f"{{{NS.CONTENT_TYPES}}}Default")
    }
    overrides = {
        el.get("PartName"): el.get("ContentType")
        for el in root.iter(f"{{{NS.CONTENT_TYPES}}}Override")
    }
    assert defaults == {"png": CT.PNG, "rels": CT.RELATIONSHIPS, "xml": CT.XML}
    assert overrides == {"/word/document.xml": CT.WML_DOCUMENT_MAIN}


def test_part_differing_from_extension_default_gets_override() -> None:
    pkg = Package()
    pkg.add_part("/a.png", CT.PNG)
    pkg.add_part("/b.png", "image/x-not-png")
    root = etree.fromstring(pkg.content_types_xml())
    overrides = {el.get("PartName") for el in root.iter(f"{{{NS.CONTENT_TYPES}}}Override")}
    assert overrides == {"/b.png"}


# --- zip output --------------------------------------------------------------


def test_content_types_is_first_entry(hello_package: HelloFactory) -> None:
    names = _zip(hello_package()).namelist()
    assert names[0] == "[Content_Types].xml"
    assert sorted(names[1:]) == names[1:]
    assert set(names) == {
        "[Content_Types].xml",
        "_rels/.rels",
        "docProps/core.xml",
        "word/_rels/document.xml.rels",
        "word/document.xml",
        "word/styles.xml",
    }


def test_output_is_deterministic(hello_package: HelloFactory) -> None:
    assert hello_package().to_bytes() == hello_package().to_bytes()


def test_zip_metadata_is_fixed(hello_package: HelloFactory) -> None:
    for info in _zip(hello_package()).infolist():
        assert info.date_time == (1980, 1, 1, 0, 0, 0)
        assert info.create_system == 0
        assert info.compress_type == zipfile.ZIP_DEFLATED


def test_all_xml_entries_are_well_formed(hello_package: HelloFactory) -> None:
    archive = _zip(hello_package())
    for name in archive.namelist():
        etree.fromstring(archive.read(name))


# --- read-back ---------------------------------------------------------------


def test_python_docx_reads_the_package(hello_package: HelloFactory, tmp_path: Path) -> None:
    path = tmp_path / "hello.docx"
    hello_package().save(path)
    document = docx.Document(str(path))
    assert [p.text for p in document.paragraphs] == [
        "Hello, wordgenx",
        "Written by the OPC package writer. See example.com",
    ]
    assert document.paragraphs[0].style is not None
    assert document.paragraphs[0].style.name == "Heading 1"
    assert document.core_properties.title == "Hello & welcome"
