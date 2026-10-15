"""Validate package parts against the ECMA-376 Transitional schemas in schemas/."""

import zipfile
from functools import cache
from io import BytesIO
from pathlib import Path

from lxml import etree

SCHEMAS = Path(__file__).resolve().parents[2] / "schemas"


@cache
def schema() -> etree.XMLSchema:
    return etree.XMLSchema(etree.parse(SCHEMAS / "wordgenx.xsd"))


def is_xml(name: str) -> bool:
    return name.endswith((".xml", ".rels"))


def validate_part(data: bytes) -> list[str]:
    """Schema errors for one XML part (empty when valid)."""
    xsd = schema()
    if xsd.validate(etree.fromstring(data)):
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
