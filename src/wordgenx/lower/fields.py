"""Fields: \\field, \\date, \\pagenum, \\numpages and \\sectionpages; parsing field
codes with nested fields, and Word date pictures (design §3.12)."""

from __future__ import annotations

import difflib
import os
import re
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from wordgenx import ir
from wordgenx.commands.bind import Args
from wordgenx.commands.builtin import DATE_FIELDS
from wordgenx.lower.common import Format, InlineHandler, Piece, verbatim
from wordgenx.syntax import ast
from wordgenx.syntax.diagnostics import Code, Severity

if TYPE_CHECKING:
    from wordgenx.lower.lowering import Lowering

PAGE_FIELDS = {"pagenum": "PAGE", "numpages": "NUMPAGES", "sectionpages": "SECTIONPAGES"}
META_FIELDS = {"TITLE": "title", "SUBJECT": "subject", "AUTHOR": "author", "KEYWORDS": "keywords"}
WORD_FIELDS = frozenset(
    """ADDIN ADDRESSBLOCK ADVANCE ASK AUTHOR AUTONUM AUTONUMLGL AUTONUMOUT AUTOTEXT AUTOTEXTLIST
    BARCODE BIBLIOGRAPHY BIDIOUTLINE CITATION COMMENTS COMPARE CONTROL CREATEDATE DATABASE DATE
    DDE DDEAUTO DISPLAYBARCODE DOCPROPERTY DOCVARIABLE EDITTIME EMBED EQ FILENAME FILESIZE FILLIN
    FORMCHECKBOX FORMDROPDOWN FORMTEXT GLOSSARY GOTOBUTTON GREETINGLINE HYPERLINK IF IMPORT
    INCLUDE INCLUDEPICTURE INCLUDETEXT INDEX INFO KEYWORDS LASTSAVEDBY LINK LISTNUM MACROBUTTON
    MERGEBARCODE MERGEFIELD MERGEREC MERGESEQ NEXT NEXTIF NOTEREF NUMCHARS NUMPAGES NUMWORDS PAGE
    PAGEREF PRINT PRINTDATE PRIVATE QUOTE RD REF REVNUM SAVEDATE SECTION SECTIONPAGES SEQ SET
    SHAPE SKIPIF STYLEREF SUBJECT SYMBOL TA TC TEMPLATE TIME TITLE TOA TOC USERADDRESS
    USERINITIALS USERNAME XE""".split()
)
"""The field types Word knows (Word's field-code reference); `=` (a formula) as well."""
DOCPROPERTY_RE = re.compile(r'\s*DOCPROPERTY\s+(?:"([^"]+)"|(\S+))', re.IGNORECASE)

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


# --- commands ------------------------------------------------------------------------------


def page_field(lw: Lowering, command: ast.Command, args: Args, fmt: Format) -> list[Piece]:
    r"""\pagenum, \numpages, \sectionpages."""
    field = ir.Field((f" {PAGE_FIELDS[command.name]} ",), "1", fmt.props, fmt.style)
    return [Piece("", fmt, "field", field=field)]


def any_field(lw: Lowering, command: ast.Command, args: Args, fmt: Format) -> list[Piece]:
    r"""\field[result="..."]{CODE}: any Word field; nested fields in { } as in Word."""
    code = verbatim(command)
    if not code:
        return []
    try:
        instruction = parse_instruction(code)
    except ValueError as e:
        lw.report(Code.INVALID_ARGUMENT, str(e), command.span)
        return []
    for name in _unknown_fields(instruction):
        close = difflib.get_close_matches(name, WORD_FIELDS, n=1)
        lw.report(
            Code.INVALID_ARGUMENT,
            f"Word has no field `{name}`",
            command.body.span if command.body is not None else command.span,
            help=f"did you mean `{close[0]}`?" if close else "Word shows an error in its place",
            severity=Severity.WARNING,
        )
    result = args.text("result")
    dirty = False
    if result is None:
        known = _known_result(lw, field_name(code), code)
        dirty = known is None  # Word computes it when the document opens
        result = known or ""
    field = ir.Field(merge_text((" ", *instruction, " ")), result, fmt.props, fmt.style, dirty)
    return [Piece("", fmt, "field", field=field)]


def _unknown_fields(parts: tuple[str | ir.Field, ...]) -> list[str]:
    """The names of the field and its nested fields that Word does not know."""
    unknown: list[str] = []
    if parts and isinstance(parts[0], str) and parts[0].strip():
        name = field_name(parts[0])
        if not name.startswith("=") and name not in WORD_FIELDS:
            unknown.append(name)
    for part in parts:
        if isinstance(part, ir.Field):
            unknown += _unknown_fields(part.instruction)
    return unknown


def _known_result(lw: Lowering, name: str, code: str) -> str | None:
    if name in ("PAGE", "SECTIONPAGES", "NUMPAGES"):
        return "1"
    if name == "DOCPROPERTY" and (match := DOCPROPERTY_RE.match(code)):
        value = lw.meta.custom_value(match.group(1) or match.group(2))
        if isinstance(value, bool):
            return "Y" if value else "N"
        return None if value is None else str(value)
    if name in META_FIELDS:
        value: object = getattr(lw.meta, META_FIELDS[name])
        if isinstance(value, tuple):
            return ", ".join(str(v) for v in value)  # pyright: ignore[reportUnknownVariableType, reportUnknownArgumentType]
        return value if isinstance(value, str) else None
    return None


def date(lw: Lowering, command: ast.Command, args: Args, fmt: Format) -> list[Piece]:
    r"""\date[kind=fixed|today|saved|created, format="d MMMM yyyy"]."""
    kind = args.text("kind", "fixed")
    picture = args.text("format", "d MMMM yyyy")
    assert kind is not None
    assert picture is not None
    text = format_date(build_time(), picture)
    if kind == "fixed":
        return [Piece(text, fmt, "code")]
    code = f' {DATE_FIELDS[kind]} \\@ "{picture}" '
    return [Piece("", fmt, "field", field=ir.Field((code,), text, fmt.props, fmt.style))]


INLINES: dict[str, InlineHandler] = {
    **dict.fromkeys(PAGE_FIELDS, page_field),
    "field": any_field,
    "date": date,
}
