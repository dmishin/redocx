"""Convert the project's Markdown documents (docs/*.md) to redocx source.

Covers the subset those documents use: ATX headings, paragraphs, bullet and numbered
lists (nested), fenced code, GitHub pipe tables, block quotes, inline code, strong and
emphasis, links and autolinks. It exists to rebuild the design documents in redocx
(the M2 exit criterion), not as a general Markdown importer.

    python tools/md2rdx.py docs/*.md --out docs/book/chapters
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

ESCAPED = set("\\%{}[]*_$#|@~")
"""Characters escaped in redocx text (`=` only at the start of a paragraph)."""

HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
NUMBER_PREFIX_RE = re.compile(r"^\d+(?:\.\d+)*\.?\s+")
FENCE_RE = re.compile(r"^(`{3,})(.*)$")
LIST_RE = re.compile(r"^(\s*)([-*+]|\d+[.)])\s+(.*)$")
TABLE_SEPARATOR_RE = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)*\|?\s*$")
LINK_RE = re.compile(r"\[([^\]]*)\]\(([^)\s]+)\)")
AUTOLINK_RE = re.compile(r"<(https?://[^>\s]+)>")


# --- inline -----------------------------------------------------------------------------------


def escape(text: str) -> str:
    return "".join("\\" + c if c in ESCAPED else c for c in text)


def code_span(code: str) -> str:
    longest = max((len(m) for m in re.findall(r"`+", code)), default=0)
    ticks = "`" * (longest + 1)
    pad = " " if code.startswith("`") or code.endswith("`") else ""
    return f"{ticks}{pad}{code}{pad}{ticks}"


def inline(text: str, *, in_table: bool = False) -> str:
    """Markdown inline markup -> redocx inline markup."""
    out: list[str] = []
    i = 0
    strong = emph = False
    while i < len(text):
        c = text[i]
        if c == "`":
            run = re.match(r"`+", text[i:]).group(0)  # type: ignore[union-attr]
            # CommonMark: the span ends at the next backtick run of exactly the same length
            end = next(
                (
                    m.start()
                    for m in re.finditer(r"`+", text[i + len(run) :])
                    if len(m.group(0)) == len(run)
                ),
                -1,
            )
            end = end + i + len(run) if end != -1 else -1
            if end == -1:
                out.append(escape(run))
                i += len(run)
                continue
            code = text[i + len(run) : end]
            if len(run) > 1 and code.startswith(" ") and code.endswith(" ") and code.strip():
                code = code[1:-1]
            if in_table:
                code = code.replace("\\|", "|")
            out.append(code_span(code))
            i = end + len(run)
        elif c == "\\" and i + 1 < len(text) and not text[i + 1].isalnum():
            out.append(escape(text[i + 1]))  # a Markdown escape: the literal character
            i += 2
        elif text.startswith("**", i):
            strong = not strong
            out.append("*")
            i += 2
        elif c in "*_" and _flanks(text, i, opening=not emph):
            emph = not emph
            out.append("_")
            i += 1
        elif c == "[" and (m := LINK_RE.match(text, i)):
            label, url = m.group(1), m.group(2)
            if url.startswith(("http://", "https://", "mailto:")):
                out.append(f"[{inline(label)}]({url})")
            else:
                out.append(inline(label))  # a link between the docs: the book has them all
            i = m.end()
        elif c == "<" and (m := AUTOLINK_RE.match(text, i)):
            out.append(f"<{m.group(1)}>")
            i = m.end()
        else:
            out.append(escape(c))
            i += 1
    if strong or emph:  # unbalanced in the source: show the markers literally
        return escape(text)
    return "".join(out)


def _flanks(text: str, i: int, *, opening: bool) -> bool:
    before = text[i - 1] if i > 0 else " "
    after = text[i + 1] if i + 1 < len(text) else " "
    if opening:
        return not after.isspace() and not before.isalnum() and text.find(text[i], i + 1) != -1
    return not before.isspace() and not after.isalnum()


def paragraph_text(text: str) -> str:
    converted = inline(text)
    if converted.startswith(("= ", "- ", "+ ")) or re.match(r"\d+\. ", converted):
        converted = "\\" + converted if converted[0] in "=" else converted
    return converted


# --- blocks -----------------------------------------------------------------------------------


@dataclass
class Converter:
    lines: list[str]
    out: list[str] = field(default_factory=lambda: [])
    i: int = 0

    def run(self) -> str:
        while self.i < len(self.lines):
            line = self.lines[self.i]
            if not line.strip():
                self.i += 1
            elif m := HEADING_RE.match(line):
                self.heading(len(m.group(1)), m.group(2))
            elif FENCE_RE.match(line):
                self.fence()
            elif line.lstrip().startswith(">"):
                self.quote()
            elif self.is_table():
                self.table()
            elif LIST_RE.match(line):
                self.list_block()
            else:
                self.paragraph()
        return "\n".join(self.out).rstrip() + "\n"

    def emit(self, *lines: str) -> None:
        self.out.extend(lines)

    def heading(self, level: int, text: str) -> None:
        text = NUMBER_PREFIX_RE.sub("", text)  # the book numbers its headings itself
        self.emit("=" * level + " " + inline(text), "")
        self.i += 1

    def fence(self) -> None:
        m = FENCE_RE.match(self.lines[self.i])
        assert m is not None
        ticks, info = m.group(1), m.group(2).strip()
        language = info.split()[0].split("{")[0] if info else ""
        body: list[str] = []
        self.i += 1
        while self.i < len(self.lines) and not self.lines[self.i].startswith(ticks):
            body.append(self.lines[self.i])
            self.i += 1
        self.i += 1  # the closing fence
        longest = max((len(m) for line in body for m in re.findall(r"^`{3,}", line)), default=0)
        fence = "`" * max(3, longest + 1)
        self.emit(fence + language, *body, fence, "")

    def quote(self) -> None:
        paragraphs: list[list[str]] = [[]]
        while self.i < len(self.lines) and self.lines[self.i].lstrip().startswith(">"):
            text = self.lines[self.i].lstrip()[1:].strip()
            if text:
                paragraphs[-1].append(text)
            elif paragraphs[-1]:
                paragraphs.append([])
            self.i += 1
        for lines in paragraphs:
            if lines:
                self.emit("\\p[style=Note]{" + inline(" ".join(lines)) + "}", "")

    def is_table(self) -> bool:
        line = self.lines[self.i]
        nxt = self.lines[self.i + 1] if self.i + 1 < len(self.lines) else ""
        return line.lstrip().startswith("|") and bool(TABLE_SEPARATOR_RE.match(nxt))

    def table(self) -> None:
        header = _cells(self.lines[self.i])
        aligns = [_alignment(c) for c in _cells(self.lines[self.i + 1])]
        rows = [header]
        self.i += 2
        while self.i < len(self.lines) and self.lines[self.i].lstrip().startswith("|"):
            rows.append(_cells(self.lines[self.i]))
            self.i += 1
        columns = len(header)
        weights = []
        for c in range(columns):  # widths in proportion to the text, never narrower than a word
            texts = [_plain(row[c]) if c < len(row) else "" for row in rows]
            longest_word = max((len(w) for t in texts for w in t.split()), default=1)
            longest = max(len(t) for t in texts)
            weights.append(max(longest_word + 2, min(longest, 48)))
        options = [
            'style="Book Table"',
            "header-rows=1",
            "widths=(" + ", ".join(f"{w}fr" for w in weights) + ")",
        ]
        if any(a != "left" for a in aligns):
            options.append("columns=(" + ", ".join(aligns) + ")")
        self.emit("\\table[" + ", ".join(options) + "]{")
        for row in rows:
            cells = [inline(cell, in_table=True) or " " for cell in row[:columns]]
            cells += [" "] * (columns - len(cells))
            self.emit("| " + " | ".join(cells) + " |")
        self.emit("}", "")

    def list_block(self) -> None:
        stack: list[int] = []  # markdown indentation of each open level
        while self.i < len(self.lines):
            line = self.lines[self.i]
            m = LIST_RE.match(line)
            if m is None:
                break
            indent = len(m.group(1).expandtabs(4))
            while stack and indent < stack[-1]:
                stack.pop()
            if not stack or indent > stack[-1]:
                stack.append(indent)
            marker = m.group(2)
            marker = "-" if marker in "-*+" else marker.rstrip(".)") + "."
            text = [m.group(3)]
            content_column = indent + len(m.group(2)) + 1
            self.i += 1
            while self.i < len(self.lines):  # continuation lines (lazy ones too)
                nxt = self.lines[self.i]
                if (
                    not nxt.strip()
                    or LIST_RE.match(nxt)
                    or HEADING_RE.match(nxt)
                    or FENCE_RE.match(nxt)
                ):
                    break
                if len(nxt) - len(nxt.lstrip()) < content_column and nxt.lstrip().startswith(
                    ("|", ">")
                ):
                    break
                text.append(nxt.strip())
                self.i += 1
            depth = len(stack) - 1
            pad = "".join("   " for _ in range(depth))
            self.emit(f"{pad}{marker} {inline(' '.join(text))}")
            if self.i < len(self.lines) and not self.lines[self.i].strip():
                nxt = self.lines[self.i + 1] if self.i + 1 < len(self.lines) else ""
                if not LIST_RE.match(nxt):
                    break
                self.i += 1
        self.emit("")

    def paragraph(self) -> None:
        text: list[str] = []
        while self.i < len(self.lines):
            line = self.lines[self.i]
            if (
                not line.strip()
                or HEADING_RE.match(line)
                or FENCE_RE.match(line)
                or line.lstrip().startswith(">")
                or (text and LIST_RE.match(line))
                or self.is_table()
            ):
                break
            text.append(line.strip())
            self.i += 1
        self.emit(paragraph_text(" ".join(text)), "")


def _cells(line: str) -> list[str]:
    body = line.strip()
    body = body.removeprefix("|")
    if body.endswith("|") and not body.endswith("\\|"):
        body = body[:-1]
    cells: list[str] = []
    current = ""
    in_code = 0
    i = 0
    while i < len(body):
        c = body[i]
        if c == "`":
            run = re.match(r"`+", body[i:]).group(0)  # type: ignore[union-attr]
            in_code = 0 if in_code == len(run) else (in_code or len(run))
            current += run
            i += len(run)
            continue
        if c == "\\" and body[i + 1 : i + 2] == "|":
            current += "\\|"
            i += 2
            continue
        if c == "|" and not in_code:
            cells.append(current.strip())
            current = ""
        else:
            current += c
        i += 1
    cells.append(current.strip())
    return cells


def _plain(cell: str) -> str:
    """Roughly the text a cell shows (for column widths)."""
    text = LINK_RE.sub(lambda m: m.group(1), cell)
    return re.sub(r"[`*_\\]", "", text)


def _alignment(cell: str) -> str:
    cell = cell.strip()
    if cell.startswith(":") and cell.endswith(":"):
        return "center"
    if cell.endswith(":"):
        return "right"
    return "left"


def convert(markdown: str) -> str:
    return Converter(markdown.splitlines()).run()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("files", nargs="+", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    args.out.mkdir(parents=True, exist_ok=True)
    for path in args.files:
        target = args.out / path.with_suffix(".rdx").name
        header = f"% Generated from {path.as_posix()} by tools/md2rdx.py; do not edit.\n\n"
        target.write_text(
            header + convert(path.read_text(encoding="utf-8")), encoding="utf-8", newline="\n"
        )
        print(f"wrote {target}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
