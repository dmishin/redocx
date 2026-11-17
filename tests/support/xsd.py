"""Validate package parts against the ECMA-376 Transitional schemas (redocx.ooxml.validate)."""

from redocx.ooxml.validate import (
    SCHEMAS,
    is_xml,
    process_compatibility,
    schema,
    understood,
    validate_package,
    validate_part,
)

__all__ = [
    "SCHEMAS",
    "is_xml",
    "process_compatibility",
    "schema",
    "understood",
    "validate_package",
    "validate_part",
]
