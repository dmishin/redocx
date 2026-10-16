"""Validate package parts against the ECMA-376 Transitional schemas in schemas/."""

import zipfile
from functools import cache
from io import BytesIO
from pathlib import Path

from lxml import etree

SCHEMAS = Path(__file__).resolve().parents[2] / "schemas"
MC = "http://schemas.openxmlformats.org/markup-compatibility/2006"


@cache
def schema() -> etree.XMLSchema:
    return etree.XMLSchema(etree.parse(SCHEMAS / "wordgenx.xsd"))


@cache
def understood() -> frozenset[str]:
    """The namespaces the schemas define: what an ECMA-376 reader understands."""
    found: set[str] = set()
    for path in SCHEMAS.rglob("*.xsd"):
        target = etree.parse(path).getroot().get("targetNamespace")
        if target:
            found.add(target)
    return frozenset(found)


def is_xml(name: str) -> bool:
    return name.endswith((".xml", ".rels"))


def process_compatibility(root: etree._Element) -> None:  # pyright: ignore[reportPrivateUsage]
    """Markup Compatibility (ECMA-376 Part 3) as an ECMA-376 reader applies it: each
    mc:AlternateContent becomes its first Choice whose required namespaces are understood,
    else its Fallback, else nothing. Word 2010+ markup (wps shapes) is in a Choice."""
    known = understood()
    for alternate in list(root.iter(f"{{{MC}}}AlternateContent")):
        chosen: list[etree._Element] = []  # pyright: ignore[reportPrivateUsage]
        for branch in alternate:
            if branch.tag == f"{{{MC}}}Choice":
                prefixes = (branch.get("Requires") or "").split()
                if all(branch.nsmap.get(prefix) in known for prefix in prefixes):
                    chosen = list(branch)
                    break
            elif branch.tag == f"{{{MC}}}Fallback":
                chosen = list(branch)
        parent = alternate.getparent()
        assert parent is not None
        index = parent.index(alternate)
        parent.remove(alternate)
        for offset, child in enumerate(chosen):
            parent.insert(index + offset, child)


def validate_part(data: bytes) -> list[str]:
    """Schema errors for one XML part (empty when valid)."""
    xsd = schema()
    root = etree.fromstring(data)
    process_compatibility(root)
    if xsd.validate(root):
        return []
    return [f"{e.line}:{e.column}: {e.message}" for e in xsd.error_log]


def validate_package(docx: bytes) -> dict[str, list[str]]:
    """Part name -> schema errors, for every XML part that has errors."""
    errors: dict[str, list[str]] = {}
    with zipfile.ZipFile(BytesIO(docx)) as archive:
        for name in archive.namelist():
            if is_xml(name):
                part_errors = validate_part(archive.read(name))
                if part_errors:
                    errors[name] = part_errors
    return errors
