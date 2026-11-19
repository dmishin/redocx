"""Escaping for generated source: data written into a .rdx file reads as exactly that data.

A program or template engine that writes redocx source puts text in three kinds of places,
each with its own rules (design §3.2):

- markup (paragraphs, `\\p{…}`, `\\cell{…}`, `\\b{…}`, pipe-table cells): `text`
- string bodies (`\\image{…}`, `\\url{…}`, `\\include{…}`): `string`
- option values (`alt=…`, `title=…`, `\\meta[author=…]`): `value`

Smart quotes and dashes still apply to escaped text; turn them off with
`\\document[smart-quotes=off]` to keep `"` and `--` as typed. Runs of spaces collapse to one,
as everywhere in markup.
"""

from __future__ import annotations

import re

__all__ = ["inline", "line_start", "string", "text", "value"]

_LINE_START_RE = re.compile(r"(-|\d{1,9}\.|[a-z]\.)(?=[ \t])")
"""List markers: `- `, `1. ` and, inside list items, `a. `."""
_AUTOLINK_RE = re.compile(r"<(?=[A-Za-z][A-Za-z0-9+.-]*:)")
_ALWAYS = frozenset("\\{}[]`~|")


def inline(text: str) -> str:
    """`text` as markup in the middle of a line: every mark that would be read as markup is
    escaped. Use `text` instead when the result may start a line."""
    out: list[str] = []
    for i, ch in enumerate(text):
        prev = text[i - 1] if i else ""
        nxt = text[i + 1] if i + 1 < len(text) else ""
        if ch in _ALWAYS:
            out.append("\\" + ch)
        elif ch == "\N{NO-BREAK SPACE}":
            out.append("~")
        elif ch == "%" and (not prev or prev.isspace()):
            out.append("\\%")
        elif ch in "*_" and not (prev.isalnum() and nxt.isalnum()):
            out.append("\\" + ch)  # it could open or close emphasis; snake_case stays as is
        elif ch == "@" and nxt.isalpha() and not prev.isalnum():
            out.append("\\@")
        elif ch == "#" and (nxt == "{" or nxt.isalpha()):
            out.append("\\#")  # `#{` is reserved, `#name` is a macro parameter
        elif ch == "<" and _AUTOLINK_RE.match(text, i):
            out.append("\\font{<}")
        else:
            out.append(ch)
    return "".join(out)


def line_start(text: str) -> str:
    """`text`, already escaped with `inline`, made safe at the start of a line or body:
    heading, list, table and display-math markers there are made literal."""
    stripped = text.lstrip(" \t")
    lead = text[: len(text) - len(stripped)]
    if m := _LINE_START_RE.match(stripped):
        return lead + "\\font{" + m.group(1) + "}" + stripped[m.end() :]
    if stripped.startswith(("=", "|", "$$")):
        return lead + "\\" + stripped
    return text


def text(text: str) -> str:
    """`text` as markup that reads as exactly this text, wherever it is put. A line break
    stays a line break in the source: one is a space, a blank line starts a new paragraph."""
    return "\n".join(line_start(inline(line)) for line in text.split("\n"))


def string(text: str) -> str:
    """`text` as a string body (`\\image{…}`, `\\url{…}`, `\\include{…}`)."""
    return text.replace("\\", "\\\\").replace("}", "\\}")


def value(text: str) -> str:
    """`text` as a quoted option value (`alt=…`, `\\meta[title=…]`). Line breaks become
    spaces, since an option value is one line."""
    escaped = text.replace("\\", "\\\\").replace('"', '\\"')
    return '"' + re.sub(r"\r\n|[\r\n]", " ", escaped) + '"'
