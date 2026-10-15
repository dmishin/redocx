"""Diagnostics (errors, warnings) and their terminal rendering.

error[WGX0101]: unclosed `{`
  --> chapter.wgx:3:12
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

from wordgenx.syntax.source import Source, Span

TAB_WIDTH = 4


class Severity(Enum):
    ERROR = "error"
    WARNING = "warning"


class Code(Enum):
    """Diagnostic codes; the value is the code shown to users."""

    # Syntax (WGX01xx)
    UNCLOSED_BRACE = "WGX0101"
    UNEXPECTED_CLOSING_BRACE = "WGX0102"
    UNCLOSED_OPTIONS = "WGX0103"
    INVALID_OPTION = "WGX0104"
    UNCLOSED_STRING = "WGX0105"
    UNCLOSED_CODE_SPAN = "WGX0106"
    UNCLOSED_CODE_FENCE = "WGX0107"
    UNCLOSED_DISPLAY_MATH = "WGX0108"
    RESERVED_SYNTAX = "WGX0109"
    UNEXPECTED_OPENING_BRACE = "WGX0110"
    INVALID_ESCAPE = "WGX0112"
    UNCLOSED_DELIMITER = "WGX0113"
    MALFORMED_DEFINITION = "WGX0114"
    UNCLOSED_GROUP = "WGX0115"
    NESTING_TOO_DEEP = "WGX0116"

    # Style sheets (WGX02xx)
    STYLESHEET_SYNTAX = "WGX0201"
    UNKNOWN_PROPERTY = "WGX0202"
    INVALID_VALUE = "WGX0203"
    STYLE_DEFINITION = "WGX0204"
    UNSUPPORTED = "WGX0205"
    STYLESHEET_NOT_FOUND = "WGX0206"

    # Lowering: documents to the IR (WGX03xx)
    UNKNOWN_COMMAND = "WGX0301"
    INVALID_ARGUMENT = "WGX0302"
    MISPLACED = "WGX0303"
    NOT_SUPPORTED_YET = "WGX0304"
    MACRO = "WGX0305"
    INCLUDE = "WGX0306"
    UNDEFINED_STYLE = "WGX0307"
    UNDEFINED_LABEL = "WGX0308"
    DUPLICATE_LABEL = "WGX0309"
    TEMPLATE = "WGX0310"


@dataclass(frozen=True)
class Diagnostic:
    severity: Severity
    code: Code
    message: str
    span: Span
    label: str = ""
    """Short text shown under the primary span."""
    help: str | None = None

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
    if diagnostic.help:
        out += [f"{gutter} |", f"{gutter} = help: {diagnostic.help}"]
    return "\n".join(out) + "\n"


def render_all(diagnostics: Iterable[Diagnostic], source: Source) -> str:
    return "\n".join(render(d, source) for d in diagnostics)
