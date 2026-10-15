"""Fields: parsing field codes with nested fields, and Word date pictures."""

from __future__ import annotations

import os
import re
from datetime import UTC, datetime

from wordgenx import ir

_MONTHS = (
    "January February March April May June July August September October November December".split()
)
_DAYS = "Monday Tuesday Wednesday Thursday Friday Saturday Sunday".split()
_DATE_TOKEN = re.compile(
    r"'[^']*'|yyyy|yy|MMMM|MMM|MM|M|dddd|ddd|dd|d|HH|H|hh|h|mm|m|ss|s|am/pm|AM/PM"
)


def parse_instruction(code: str) -> tuple[str | ir.Field, ...]:
    """Split ``IF { MERGEFIELD x } = 1 "y"`` into text and nested fields.

    Braces mark nested fields, as in Word's field-code view; they must balance.
    """
    parts: list[str | ir.Field] = []
    text: list[str] = []
    depth = 0
    nested_start = 0
    for i, ch in enumerate(code):
        if ch == "{":
            if depth == 0:
                if text:
                    parts.append("".join(text))
                    text = []
                nested_start = i + 1
            depth += 1
        elif ch == "}" and depth > 0:
            depth -= 1
            if depth == 0:
                inner = parse_instruction(code[nested_start:i].strip())
                parts.append(ir.Field(merge_text((" ", *inner, " ")), dirty=True))
        elif depth == 0:
            text.append(ch)
    if depth != 0:
        raise ValueError("unbalanced { } in the field code")
    if text:
        parts.append("".join(text))
    return tuple(parts)


def merge_text(parts: tuple[str | ir.Field, ...]) -> tuple[str | ir.Field, ...]:
    """Join neighbouring text parts, so each stretch of code is one w:instrText."""
    merged: list[str | ir.Field] = []
    for part in parts:
        if isinstance(part, str) and merged and isinstance(merged[-1], str):
            merged[-1] += part
        else:
            merged.append(part)
    return tuple(merged)


def field_name(code: str) -> str:
    """The field type: the first word of the code, upper-cased (PAGE, DATE, ...)."""
    words = code.strip().split()
    return words[0].upper() if words else ""


def build_time() -> datetime:
    """Now, or SOURCE_DATE_EPOCH for reproducible builds."""
    epoch = os.environ.get("SOURCE_DATE_EPOCH")
    if epoch and epoch.isdigit():
        return datetime.fromtimestamp(int(epoch), UTC)
    return datetime.now(UTC).astimezone()


def format_date(moment: datetime, picture: str) -> str:
    """Format like Word's date picture switch (\\@ "d MMMM yyyy"), in English."""

    def token(match: re.Match[str]) -> str:
        t = match.group(0)
        hour12 = moment.hour % 12 or 12
        table = {
            "yyyy": f"{moment.year:04d}",
            "yy": f"{moment.year % 100:02d}",
            "MMMM": _MONTHS[moment.month - 1],
            "MMM": _MONTHS[moment.month - 1][:3],
            "MM": f"{moment.month:02d}",
            "M": str(moment.month),
            "dddd": _DAYS[moment.weekday()],
            "ddd": _DAYS[moment.weekday()][:3],
            "dd": f"{moment.day:02d}",
            "d": str(moment.day),
            "HH": f"{moment.hour:02d}",
            "H": str(moment.hour),
            "hh": f"{hour12:02d}",
            "h": str(hour12),
            "mm": f"{moment.minute:02d}",
            "m": str(moment.minute),
            "ss": f"{moment.second:02d}",
            "s": str(moment.second),
            "am/pm": "am" if moment.hour < 12 else "pm",
            "AM/PM": "AM" if moment.hour < 12 else "PM",
        }
        return t[1:-1] if t.startswith("'") else table[t]

    return _DATE_TOKEN.sub(token, picture)
