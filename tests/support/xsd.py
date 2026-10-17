"""Validate package parts against the ECMA-376 Transitional schemas (wordgenx.ooxml.validate)."""

from wordgenx.ooxml.validate import (
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
