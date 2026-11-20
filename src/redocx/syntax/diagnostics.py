"""Diagnostics (errors, warnings) and their terminal rendering.

error[RDX0101]: unclosed `{`
  --> chapter.rdx:3:12
   |
 3 | Some \\b{bold text
   |         ^ this `{` is never closed
   |
   = help: add a matching `}`
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import Enum

from redocx.syntax.source import Source, Span

TAB_WIDTH = 4


class Severity(Enum):
    ERROR = "error"
    WARNING = "warning"


class Code(Enum):
    """Diagnostic codes; the value is the code shown to users."""

    # Syntax (RDX01xx)
    UNCLOSED_BRACE = "RDX0101"
    UNEXPECTED_CLOSING_BRACE = "RDX0102"
    UNCLOSED_OPTIONS = "RDX0103"
    INVALID_OPTION = "RDX0104"
    UNCLOSED_STRING = "RDX0105"
    UNCLOSED_CODE_SPAN = "RDX0106"
    UNCLOSED_CODE_FENCE = "RDX0107"
    UNCLOSED_DISPLAY_MATH = "RDX0108"
    RESERVED_SYNTAX = "RDX0109"
    UNEXPECTED_OPENING_BRACE = "RDX0110"
    INVALID_ESCAPE = "RDX0112"
    UNCLOSED_DELIMITER = "RDX0113"
    MALFORMED_DEFINITION = "RDX0114"
    UNCLOSED_GROUP = "RDX0115"
    NESTING_TOO_DEEP = "RDX0116"

    # Style sheets (RDX02xx)
    STYLESHEET_SYNTAX = "RDX0201"
    UNKNOWN_PROPERTY = "RDX0202"
    INVALID_VALUE = "RDX0203"
    STYLE_DEFINITION = "RDX0204"
    UNSUPPORTED = "RDX0205"
    STYLESHEET_NOT_FOUND = "RDX0206"

    # Lowering: documents to the IR (RDX03xx)
    UNKNOWN_COMMAND = "RDX0301"
    INVALID_ARGUMENT = "RDX0302"
    MISPLACED = "RDX0303"
    NOT_SUPPORTED_YET = "RDX0304"
    MACRO = "RDX0305"
    INCLUDE = "RDX0306"
    UNDEFINED_STYLE = "RDX0307"
    UNDEFINED_LABEL = "RDX0308"
    DUPLICATE_LABEL = "RDX0309"
    TEMPLATE = "RDX0310"
    MATH = "RDX0311"
    CITATION = "RDX0312"
    OUTSIDE_ROOT = "RDX0313"

    # Output: the written package (RDX04xx)
    INCONSISTENT_OUTPUT = "RDX0401"
    INVALID_MATH = "RDX0402"


@dataclass(frozen=True)
class Diagnostic:
    severity: Severity
    code: Code
    message: str
    span: Span
    label: str = ""
    """Short text shown under the primary span."""
    help: str | None = None
    notes: tuple[str, ...] = ()
    """Context shown after the help: the macro calls the text came through."""
    located: bool = True
    """False for a problem of the whole file, shown without a source line."""

    @property
    def is_error(self) -> bool:
        return self.severity is Severity.ERROR


def error(
    code: Code, message: str, span: Span, label: str = "", help: str | None = None
) -> Diagnostic:
    return Diagnostic(Severity.ERROR, code, message, span, label, help)


def warning(
    code: Code, message: str, span: Span, label: str = "", help: str | None = None
) -> Diagnostic:
    return Diagnostic(Severity.WARNING, code, message, span, label, help)


def _expand_tabs(text: str) -> str:
    return text.expandtabs(TAB_WIDTH)


def render(diagnostic: Diagnostic, source: Source) -> str:
    """Render one diagnostic in the compiler's terminal format (no colors)."""
    if not diagnostic.located:
        out = [
            f"{diagnostic.severity.value}[{diagnostic.code.value}]: {diagnostic.message}",
            f" --> {source.name}",
        ]
        out += [f"  = note: {note}" for note in diagnostic.notes]
        if diagnostic.help:
            out.append(f"  = help: {diagnostic.help}")
        return "\n".join(out) + "\n"
    start = source.position(diagnostic.span.start)
    end = source.position(diagnostic.span.end)
    line_no = start.line
    gutter = " " * len(str(line_no))

    line = source.line_text(line_no)
    prefix = _expand_tabs(line[: start.column - 1])
    if end.line == start.line:
        marked = line[start.column - 1 : end.column - 1]
    else:
        marked = line[start.column - 1 :]  # multi-line span: mark to the end of the first line
    width = max(1, len(_expand_tabs(prefix + marked)) - len(prefix))
    carets = " " * len(prefix) + "^" * width
    if diagnostic.label:
        carets += " " + diagnostic.label

    out = [
        f"{diagnostic.severity.value}[{diagnostic.code.value}]: {diagnostic.message}",
        f"{gutter}--> {source.name}:{start.line}:{start.column}",
        f"{gutter} |",
        f"{line_no} | {_expand_tabs(line)}".rstrip(),
        f"{gutter} | {carets}",
    ]
    if diagnostic.help or diagnostic.notes:
        out.append(f"{gutter} |")
    if diagnostic.help:
        out.append(f"{gutter} = help: {diagnostic.help}")
    out += [f"{gutter} = note: {note}" for note in diagnostic.notes]
    return "\n".join(out) + "\n"


def to_dict(diagnostic: Diagnostic, source: Source) -> dict[str, object]:
    """One diagnostic as JSON-ready data (`--diagnostics json`): 1-based lines and columns,
    `null` for a problem of the whole file."""
    data: dict[str, object] = {
        "type": "diagnostic",
        "severity": diagnostic.severity.value,
        "code": diagnostic.code.value,
        "message": diagnostic.message,
        "file": source.name,
        "line": None,
        "column": None,
        "end_line": None,
        "end_column": None,
    }
    if diagnostic.located:
        start = source.position(diagnostic.span.start)
        end = source.position(diagnostic.span.end)
        data.update(line=start.line, column=start.column, end_line=end.line, end_column=end.column)
    data.update(label=diagnostic.label, help=diagnostic.help, notes=list(diagnostic.notes))
    return data


def expansion_notes(span: Span) -> tuple[str, ...]:
    """A note ``in `\\name`, called at file:line:column`` for each macro call `span` came
    through, innermost first."""
    notes: list[str] = []
    expansion = span.expansion
    while expansion is not None:
        at = expansion.call_source.position(expansion.call.start)
        where = f"{expansion.call_source.name}:{at.line}:{at.column}"
        notes.append(f"in `\\{expansion.macro}`, called at {where}")
        expansion = expansion.call.expansion
    return tuple(notes)


def render_all(diagnostics: Iterable[Diagnostic], source: Source) -> str:
    return "\n".join(render(d, source) for d in diagnostics)
