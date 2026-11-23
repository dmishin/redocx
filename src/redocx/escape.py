"""Escaping for generated source: data written into a .rdx file reads as exactly that data.

A program or template engine that writes redocx source puts text in three kinds of places,
each with its own rules (design §3.2):

- markup (paragraphs, `\\p{…}`, `\\cell{…}`, `\\b{…}`, pipe-table cells): `text`, `lines`,
  or `inline` in the middle of a line
- string bodies (`\\image{…}`, `\\url{…}`, `\\include{…}`): `string`
- option values (`alt=…`, `title=…`, `\\meta[author=…]`): `value`

Each result depends only on the arguments. Escaped text keeps its spaces (runs of them, and
those at the start and end). Its quotes and hyphens get the document's smart punctuation, as
typed text does, unless `punctuation` asks to keep them as they are.
"""

from __future__ import annotations

import re

__all__ = ["inline", "line_start", "lines", "string", "text", "value"]

_LINE_START_RE = re.compile(r"(\d{1,9}\.|[a-z]\.)(?=[ \t])")
"""Numbered list markers: `1. ` and, inside list items, `a. `."""
_MARKDOWN_RE = re.compile(r"(#|>|\+)(?=[ \t#])")
"""Markdown block markers, which redocx warns about at the start of a line."""
_AUTOLINK_RE = re.compile(r"<(?=[A-Za-z][A-Za-z0-9+.-]*:)")
_NEWLINE_RE = re.compile(r"\r\n|[\r\n]")
_ALWAYS = frozenset("\\{}[]`~|")


def inline(text: str, *, punctuation: bool = False, edges: bool = True) -> str:
    """`text` as markup in the middle of a line: every mark that would be read as markup is
    escaped, and line breaks become spaces. Use `text` instead when the result may start a line.

    Spaces stay as they are: a space after another one is written `\\ `, and so is one at the
    start or end unless `edges` is off (for pieces of a longer line, as the importer prints).
    With `punctuation`, straight quotes and runs of hyphens are written `\\"`, `\\'`, `\\-`, so
    they stay as in the data whatever smart punctuation the document sets."""
    text = _NEWLINE_RE.sub(" ", text)
    out: list[str] = []
    last = len(text) - 1
    for i, ch in enumerate(text):
        prev = text[i - 1] if i else ""
        nxt = text[i + 1] if i < last else ""
        if ch in _ALWAYS:
            out.append("\\" + ch)
        elif ch == " ":
            edge = edges and (i == 0 or i == last)
            out.append("\\ " if edge or prev == " " else " ")
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
            out.append("\\<")
        elif punctuation and (ch in "\"'" or (ch == "-" and "-" in (prev, nxt))):
            out.append("\\" + ch)
        else:
            out.append(ch)
    return "".join(out)


def line_start(text: str) -> str:
    """`text`, already escaped with `inline`, made safe at the start of a line or body:
    heading, list, table and display-math markers there are made literal."""
    stripped = text.lstrip(" \t")
    lead = text[: len(text) - len(stripped)]
    if m := _LINE_START_RE.match(stripped):  # `1.\ x` is no marker: no plain space follows
        return lead + m.group(1) + "\\" + stripped[m.end() :]
    if stripped.startswith(("=", "|", "$$", "- ", "-\t")) or _MARKDOWN_RE.match(stripped):
        return lead + "\\" + stripped
    return text


def text(text: str, *, indent: int = 0, punctuation: bool = False) -> str:
    """`text` as markup that reads as exactly this text, wherever it is put. A line break
    stays a line break in the source: one is a space, a blank line starts a new paragraph.

    `indent` puts that many spaces before every line after the first, so text written after
    a list marker (`- `, `1. `) stays in its item: pass the width of the marker.
    `punctuation` is as for `inline`."""
    return _join(text, "\n", indent, punctuation)


def lines(text: str, *, indent: int = 0, punctuation: bool = False) -> str:
    """`text` as markup in which every line break of the data is a line break in the
    paragraph (`\\br`), as in an address. `indent` and `punctuation` are as for `text`."""
    return _join(text, "\\br\n", indent, punctuation)


def _join(text: str, separator: str, indent: int, punctuation: bool) -> str:
    rows = [
        line_start(inline(line, punctuation=punctuation)) if line.strip(" \t") else ""
        for line in _NEWLINE_RE.split(text)
    ]
    return (separator + " " * indent).join(rows)


def string(text: str) -> str:
    """`text` as a string body (`\\image{…}`, `\\url{…}`, `\\include{…}`)."""
    return text.replace("\\", "\\\\").replace("}", "\\}")


def value(text: str) -> str:
    """`text` as a quoted option value (`alt=…`, `\\meta[title=…]`). Line breaks become
    spaces, since an option value is one line."""
    escaped = text.replace("\\", "\\\\").replace('"', '\\"')
    return '"' + _NEWLINE_RE.sub(" ", escaped) + '"'
