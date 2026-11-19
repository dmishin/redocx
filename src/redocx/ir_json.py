"""The IR as JSON (``redocx build --emit-ir``), for debugging and other tools.

Each IR object becomes ``{"type": "Paragraph", ...}`` with the fields that differ from
their defaults. Lengths and colours read as in the language ("12pt", "3175emu",
"#1F4E79"); binary data (pictures, embedded documents) as its size and SHA-256.
The output is one-way: it describes the document, and is not read back.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
from collections.abc import Mapping
from decimal import Decimal
from enum import Enum
from typing import cast

from redocx import ir
from redocx.ooxml.template import Template
from redocx.values import Color, Length

EMU_PER_POINT = 12_700
EMU_PER_CM = 360_000


def to_json(document: ir.Document) -> str:
    return json.dumps(encode(document), indent=2, ensure_ascii=False) + "\n"


def encode(value: object) -> object:
    """A JSON-compatible form of an IR value."""
    if value is None or isinstance(value, bool | int | float | str):
        return value
    if isinstance(value, Enum):
        return encode(value.value)
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, Length):
        return length(value)
    if isinstance(value, Color):
        return str(value)
    if isinstance(value, bytes):
        return {"bytes": len(value), "sha256": hashlib.sha256(value).hexdigest()}
    if isinstance(value, Template):
        return {"type": "Template", "name": value.name}
    if isinstance(value, Mapping):
        return {str(k): encode(v) for k, v in cast("Mapping[object, object]", value).items()}
    if isinstance(value, tuple | list):
        return [encode(item) for item in cast("tuple[object, ...]", value)]
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        out: dict[str, object] = {"type": type(value).__name__}
        for f in dataclasses.fields(value):
            item = getattr(value, f.name)
            if f.compare and not _is_default(f, item):  # compare=False: source offsets
                out[f.name] = encode(item)
        return out
    return str(value)


def length(value: Length) -> str:
    """Exact and short: points to two decimals, else centimetres to three, else EMU."""
    for unit, per_unit, places in (("pt", EMU_PER_POINT, 100), ("cm", EMU_PER_CM, 1000)):
        amount = Decimal(value.emu) / per_unit
        if (amount * places) % 1 == 0:
            return f"{amount.normalize():f}{unit}"
    return f"{value.emu}emu"


def _is_default(f: dataclasses.Field[object], value: object) -> bool:
    if f.default is not dataclasses.MISSING:
        return value == f.default
    if f.default_factory is not dataclasses.MISSING:
        try:
            return value == f.default_factory()
        except TypeError:
            return False
    return False
