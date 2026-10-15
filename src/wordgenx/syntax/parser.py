"""Hand-written recursive-descent parser: source text -> lossless CST + diagnostics.

The grammar is context-sensitive in well-defined modes (language design §3.1):

* block mode   - paragraphs, headings, lists, pipe tables, code fences, display math
* inline mode  - text, escapes, comments, emphasis, code spans, links, references, commands
* option mode  - ``[key=value, ...]`` lists, tuples, quoted strings, bare values
* verbatim     - string / raw / math command bodies, code and math blocks

The parser never fails: malformed input produces diagnostics and error tokens,
and the CST still covers every character of the source.
"""

from __future__ import annotations

import re
from bisect import bisect_right
from dataclasses import dataclass, replace

from wordgenx.syntax.commands import SPECIAL_FORMS, ArgKind, body_kind
from wordgenx.syntax.cst import Node, SyntaxKind, Token
from wordgenx.syntax.diagnostics import Code, Diagnostic, error, warning
from wordgenx.syntax.source import Source, Span

K = SyntaxKind

ESCAPABLE = frozenset("\\%{}[]*_`$#=|@~")
NAME_RE = re.compile(r"[A-Za-z][A-Za-z0-9]*(?:-[A-Za-z0-9]+)*")
LABEL_NAME = r"[A-Za-z][A-Za-z0-9_-]*(?::[A-Za-z0-9_-]+)*"
LABEL_RE = re.compile(r"\{#" + LABEL_NAME + r"\}")
REF_RE = re.compile(r"@" + LABEL_NAME)
HASH_LABEL_RE = re.compile(r"#" + LABEL_NAME)
PARAM_RE = re.compile(r"#[A-Za-z][A-Za-z0-9_-]*")
AUTOLINK_RE = re.compile(r"<[A-Za-z][A-Za-z0-9+.-]*:[^\s<>]+>")
HEADING_RE = re.compile(r"={1,9}\*?(?=[ \t])")
BULLET_RE = re.compile(r"-(?=[ \t])")
NUMBER_MARKER_RE = re.compile(r"\d{1,9}\.(?=[ \t])")
LETTER_MARKER_RE = re.compile(r"[a-z]\.(?=[ \t])")
FENCE_RE = re.compile(r"`{3,}")
TICKS_RE = re.compile(r"`+")
FENCE_LANG_RE = re.compile(r"[A-Za-z0-9_+.#-]+")
KEY_RE = re.compile(r"[A-Za-z][A-Za-z0-9_.-]*")

TEXT_SPECIAL = frozenset("\\%~*_`<[]@#{}|\n\r")
"""Characters that end a plain text run in inline mode."""
BARE_STOP = frozenset(',=()"{}[] \t\r\n')
"""Characters that end a bare option value."""
REF_PRECEDERS = frozenset(" \t\r\n([{\"'")
TAB_WIDTH = 4
MAX_DEPTH = 64
"""Maximum nesting of braces, lists, tuples, emphasis and links (keeps recursion bounded)."""


@dataclass(frozen=True)
class InlineContext:
    multiline: bool = True
    """Whether content may continue on following lines (paragraphs) or not (headings, cells)."""
    min_indent: int = 0
    """Continuation lines must be indented at least this much (list item content)."""
    stops: frozenset[str] = frozenset()
    """Characters that end the content (``]`` in link text, ``|`` in table cells)."""
    closers: frozenset[str] = frozenset()
    """Emphasis delimiters currently open."""
    label_stop: bool = False
    """Stop before a ``{#label}`` (end of a heading)."""


class Parser:
    def __init__(self, source: Source) -> None:
        self.text = source.text
        self.n = len(self.text)
        self.pos = 0
        self.diagnostics: list[Diagnostic] = []
        self.body_depth = 0
        """Nesting depth of ``{...}`` groups: inside one, ``}`` ends content."""
        self.define_depth = 0
        """Nesting depth of \\define bodies: inside one, ``#name`` is a parameter."""
        self.depth = 0
        """Current nesting depth (see MAX_DEPTH)."""
        self._failed: set[tuple[int, str, InlineContext]] = set()
        """Openers (`*`, `_`, `[`) known not to close in a context; avoids exponential retries."""
        # Every position where `*`, `_` or `](` could close something. An opener with
        # no candidate after it cannot succeed, so it is not attempted at all.
        self._closer_positions: dict[str, list[int]] = {
            d: [i for i, ch in enumerate(self.text) if ch == d and self.can_close(i)] for d in "*_"
        }
        self._closer_positions["["] = [m.start() for m in re.finditer(r"\]\(", self.text)]

        # Characters re-scanned by failed attempts so far, and the limit. Ordinary text never
        # comes close; adversarial input degrades to literal delimiters instead of slowing down.
        self._backtracked = 0
        self._backtrack_budget = 20 * self.n + 10_000

    def may_close_after(self, opener: str, pos: int) -> bool:
        if self._backtracked > self._backtrack_budget:
            return False
        positions = self._closer_positions[opener]
        return bisect_right(positions, pos) < len(positions)

    def backtrack(self, key: tuple[int, str, InlineContext], saved_diagnostics: int) -> None:
        """Abandon a failed attempt that started at key[0]."""
        self._failed.add(key)
        self._backtracked += self.pos - key[0]
        self.pos = key[0]
        del self.diagnostics[saved_diagnostics:]

    # --- low-level helpers ----------------------------------------------------------

    def char(self, offset: int = 0) -> str:
        i = self.pos + offset
        return self.text[i] if 0 <= i < self.n else ""

    def token(self, kind: SyntaxKind, end: int) -> Token:
        assert end > self.pos, f"empty {kind} token at {self.pos}"
        tok = Token(kind, self.text[self.pos : end], self.pos)
        self.pos = end
        return tok

    def report(self, diagnostic: Diagnostic) -> None:
        self.diagnostics.append(diagnostic)

    def line_begin(self, pos: int) -> int:
        return self.text.rfind("\n", 0, pos) + 1

    def line_end(self, pos: int) -> int:
        """Offset of the "\\n" ending the line containing `pos` (or the end of the text)."""
        end = self.text.find("\n", pos)
        return self.n if end < 0 else end

    def content_end(self, pos: int) -> int:
        """Like `line_end`, but before a "\\r" of a "\\r\\n" terminator."""
        end = self.line_end(pos)
        return end - 1 if end > pos and self.text[end - 1] == "\r" else end

    def at_newline(self, pos: int) -> bool:
        return self.text.startswith("\n", pos) or self.text.startswith("\r\n", pos)

    def newline_end(self, pos: int) -> int:
        return pos + 2 if self.text.startswith("\r\n", pos) else pos + 1

    def indentation(self, line_start: int) -> tuple[int, int]:
        """(width, end offset) of the indentation of the line starting at `line_start`."""
        width, i = 0, line_start
        while i < self.n and self.text[i] in " \t":
            width = width + 1 if self.text[i] == " " else (width // TAB_WIDTH + 1) * TAB_WIDTH
            i += 1
        return width, i

    def column(self, pos: int) -> int:
        """Indentation-style width of the text before `pos` on its line."""
        width = 0
        for ch in self.text[self.line_begin(pos) : pos]:
            width = (width // TAB_WIDTH + 1) * TAB_WIDTH if ch == "\t" else width + 1
        return width

    def rest_of_line_blank(self, pos: int) -> bool:
        return self.text[pos : self.line_end(pos)].strip(" \t\r") == ""

    def is_comment_start(self, pos: int) -> bool:
        return self.text.startswith("%", pos) and (pos == 0 or self.text[pos - 1] in " \t\r\n")

    def comment(self) -> Token:
        return self.token(K.COMMENT, max(self.content_end(self.pos), self.pos + 1))

    def whitespace(self, out: list[Node | Token]) -> None:
        """Consume spaces and tabs (not newlines) into `out`."""
        end = self.pos
        while end < self.n and self.text[end] in " \t":
            end += 1
        if end > self.pos:
            out.append(self.token(K.WHITESPACE, end))

    def trailing_trivia(self, out: list[Node | Token]) -> None:
        """Whitespace and a comment up to (not including) the end of the line."""
        self.whitespace(out)
        if self.is_comment_start(self.pos):
            out.append(self.comment())

    def too_deep(self) -> Token:
        """Report excessive nesting and turn the opening character into an error token."""
        self.report(
            error(
                Code.NESTING_TOO_DEEP,
                f"nesting deeper than {MAX_DEPTH} levels",
                Span(self.pos, self.pos + 1),
                help="split the content into smaller pieces",
            )
        )
        return self.token(K.ERROR_TOKEN, self.pos + 1)

    # --- document and blocks ----------------------------------------------------------

    def parse(self) -> Node:
        root = Node(K.DOCUMENT, offset=0)
        root.children = self.blocks(min_indent=0)
        assert self.pos == self.n
        return root

    def at_block_end(self) -> bool:
        return self.pos >= self.n or (self.body_depth > 0 and self.text[self.pos] == "}")

    def blocks(self, min_indent: int, body_start: bool = False) -> list[Node | Token]:
        """Blocks up to the end of the text, the enclosing `}`, or a line indented less than
        `min_indent`.

        With `body_start`, a pipe row right after the opening `{` (``\\table{| a | b |}``)
        starts a table even though it is not at the start of a line.
        """
        out: list[Node | Token] = []
        if body_start:
            self.whitespace(out)
            if self.text.startswith("|", self.pos):
                out.append(self.pipe_table(min_indent))
        while not self.at_block_end():
            before = self.pos
            line_start = self.line_begin(self.pos)
            if self.rest_of_line_blank(self.pos):
                end = self.line_end(self.pos)
                if end > self.pos:
                    out.append(self.token(K.WHITESPACE, end))
                if self.pos < self.n:
                    out.append(self.token(K.NEWLINE, self.pos + 1))
                continue
            if self.pos == line_start:
                width, _ = self.indentation(line_start)
                if width < min_indent:
                    break
                self.whitespace(out)
                if self.at_block_end():
                    break
                if self.is_comment_start(self.pos):
                    out.append(self.comment())
                    continue
                block = self.block_start(min_indent, width)
                if block is not None:
                    out.append(block)
                    continue
            out.append(self.paragraph(min_indent))
            if self.pos == before:  # safety net: always make progress
                out.append(self.token(K.ERROR_TOKEN, self.pos + 1))
        return out

    def starts_block(self, pos: int, min_indent: int) -> bool:
        """Whether the text at `pos` (after indentation) starts a non-paragraph block."""
        text = self.text
        return bool(
            HEADING_RE.match(text, pos)
            or FENCE_RE.match(text, pos)
            or text.startswith("$$", pos)
            or text.startswith("|", pos)
            or self.list_marker(min_indent, pos)
        )

    def block_start(self, min_indent: int, width: int) -> Node | None:
        text, pos = self.text, self.pos
        if HEADING_RE.match(text, pos):
            return self.heading()
        if FENCE_RE.match(text, pos):
            return self.code_block()
        if text.startswith("$$", pos):
            return self.display_math()
        if text.startswith("|", pos):
            return self.pipe_table(min_indent)
        if self.list_marker(min_indent, pos) is not None and self.depth < MAX_DEPTH:
            self.depth += 1
            node = self.list_block(min_indent, width)
            self.depth -= 1
            return node
        return None

    def list_marker(self, min_indent: int, pos: int) -> tuple[str, bool] | None:
        """(marker text, ordered) if a list marker starts at `pos`.

        Letter markers (``a.``) are only recognised inside a list item, so a
        sentence starting with "A. Smith wrote" is never mistaken for a list.
        """
        if m := BULLET_RE.match(self.text, pos):
            return m.group(0), False
        if m := NUMBER_MARKER_RE.match(self.text, pos):
            return m.group(0), True
        if min_indent > 0 and (m := LETTER_MARKER_RE.match(self.text, pos)):
            return m.group(0), True
        return None

    def paragraph(self, min_indent: int) -> Node:
        node = Node(K.PARAGRAPH, offset=self.pos)
        node.children = self.inlines(InlineContext(min_indent=min_indent))
        return node

    def heading(self) -> Node:
        node = Node(K.HEADING, offset=self.pos)
        m = HEADING_RE.match(self.text, self.pos)
        assert m is not None
        node.children.append(self.token(K.HEADING_MARKER, m.end()))
        self.whitespace(node.children)
        node.children.extend(self.inlines(InlineContext(multiline=False, label_stop=True)))
        if LABEL_RE.match(self.text, self.pos):
            node.children.append(self.label())
            self.trailing_trivia(node.children)
        return node

    def label(self) -> Token:
        m = LABEL_RE.match(self.text, self.pos)
        assert m is not None
        return self.token(K.LABEL, m.end())

    def list_block(self, min_indent: int, marker_width: int) -> Node:
        node = Node(K.LIST, offset=self.pos)
        first = self.list_marker(min_indent, self.pos)
        assert first is not None
        while True:
            node.children.append(self.list_item(marker_width))
            if self.at_block_end() or self.pos != self.line_begin(self.pos):
                break
            width, indent_end = self.indentation(self.pos)
            marker = self.list_marker(min_indent, indent_end)
            if width != marker_width or marker is None or marker[1] != first[1]:
                break
            self.whitespace(node.children)
        return node

    def list_item(self, marker_width: int) -> Node:
        node = Node(K.LIST_ITEM, offset=self.pos)
        marker = self.list_marker(1, self.pos)  # inside a list, letter markers are allowed
        assert marker is not None
        node.children.append(self.token(K.LIST_MARKER, self.pos + len(marker[0])))
        self.whitespace(node.children)
        content_indent = self.column(self.pos)
        if LABEL_RE.match(self.text, self.pos):
            node.children.append(self.label())
            self.whitespace(node.children)
        node.children.extend(self.blocks(min_indent=max(content_indent, marker_width + 1)))
        return node

    def code_block(self) -> Node:
        node = Node(K.CODE_BLOCK, offset=self.pos)
        start = self.pos
        m = FENCE_RE.match(self.text, self.pos)
        assert m is not None
        fence = m.group(0)
        node.children.append(self.token(K.FENCE, m.end()))
        self.whitespace(node.children)
        if lang := FENCE_LANG_RE.match(self.text, self.pos):
            node.children.append(self.token(K.FENCE_LANG, lang.end()))
            self.whitespace(node.children)
        if self.char() == "{":
            node.children.append(self.options("{", "}", fence_attributes=True))
        self.trailing_trivia(node.children)
        if not self.rest_of_line_blank(self.pos):
            end = self.content_end(self.pos)
            self.report(
                error(
                    Code.INVALID_OPTION,
                    "unexpected text after the code fence",
                    Span(self.pos, end),
                    help='write attributes as {#label, caption="..."}',
                )
            )
            node.children.append(self.token(K.ERROR_TOKEN, end))
        end = self.line_end(self.pos)
        if end > self.pos:
            node.children.append(self.token(K.WHITESPACE, end))
        if self.pos >= self.n:
            self.report(self._unclosed_fence(start, fence))
            return node
        node.children.append(self.token(K.NEWLINE, self.pos + 1))

        # The block ends at a line holding only backticks (at least as many as the fence).
        content_start = line = self.pos
        while line < self.n:
            _, after_indent = self.indentation(line)
            close = FENCE_RE.match(self.text, after_indent)
            if close and len(close.group(0)) >= len(fence) and self.rest_of_line_blank(close.end()):
                if line > content_start:
                    node.children.append(self.token(K.VERBATIM, line))
                self.whitespace(node.children)
                node.children.append(self.token(K.FENCE, close.end()))
                self.trailing_trivia(node.children)
                return node
            line = self.line_end(line) + 1
        if self.n > content_start:
            node.children.append(self.token(K.VERBATIM, self.n))
        self.report(self._unclosed_fence(start, fence))
        return node

    def _unclosed_fence(self, start: int, fence: str) -> Diagnostic:
        return error(
            Code.UNCLOSED_CODE_FENCE,
            "unclosed code block",
            Span(start, start + len(fence)),
            "this fence is never closed",
            help=f"end the block with a line containing {fence}",
        )

    def display_math(self) -> Node:
        node = Node(K.DISPLAY_MATH, offset=self.pos)
        start = self.pos
        node.children.append(self.token(K.DOLLARS, self.pos + 2))
        close = self.text.find("$$", self.pos)
        if close < 0:
            if self.pos < self.n:
                node.children.append(self.token(K.VERBATIM, self.n))
            self.report(
                error(
                    Code.UNCLOSED_DISPLAY_MATH,
                    "unclosed display math",
                    Span(start, start + 2),
                    "these `$$` are never closed",
                    help="end the equation with `$$`",
                )
            )
            return node
        if close > self.pos:
            node.children.append(self.token(K.VERBATIM, close))
        node.children.append(self.token(K.DOLLARS, close + 2))
        self.whitespace(node.children)
        if LABEL_RE.match(self.text, self.pos):
            node.children.append(self.label())
        self.trailing_trivia(node.children)
        return node

    def pipe_table(self, min_indent: int) -> Node:
        node = Node(K.PIPE_TABLE, offset=self.pos)
        while True:
            node.children.append(self.pipe_row())
            if not self.at_newline(self.pos):
                break
            next_line = self.newline_end(self.pos)
            width, indent_end = self.indentation(next_line)
            if width < min_indent or not self.text.startswith("|", indent_end):
                break
            node.children.append(self.token(K.NEWLINE, next_line))
            self.whitespace(node.children)
        return node

    def pipe_row(self) -> Node:
        node = Node(K.PIPE_ROW, offset=self.pos)
        node.children.append(self.token(K.PIPE, self.pos + 1))
        context = InlineContext(multiline=False, stops=frozenset("|"))
        while not self.at_block_end() and not self.at_newline(self.pos):
            cell = Node(K.PIPE_CELL, offset=self.pos)
            cell.children = self.inlines(context)
            node.children.append(cell)
            if self.char() != "|":
                break
            node.children.append(self.token(K.PIPE, self.pos + 1))
        return node

    # --- inline content ----------------------------------------------------------------

    def continues(self, newline_at: int, context: InlineContext) -> bool:
        """Whether a paragraph continues on the line after the newline at `newline_at`."""
        line = self.newline_end(newline_at)
        if line >= self.n or self.rest_of_line_blank(line):
            return False
        width, indent_end = self.indentation(line)
        if width < context.min_indent:
            return False
        if self.body_depth > 0 and self.text.startswith("}", indent_end):
            return False
        return not self.starts_block(indent_end, context.min_indent)

    def inlines(self, context: InlineContext) -> list[Node | Token]:
        out: list[Node | Token] = []
        while self.pos < self.n:
            c = self.text[self.pos]
            before = self.pos
            if c == "}" and self.body_depth > 0:
                break
            if c in context.stops:
                break
            if c in context.closers and self.can_close(self.pos):
                break
            if self.at_newline(self.pos):
                if not context.multiline or not self.continues(self.pos, context):
                    break
                out.append(self.token(K.NEWLINE, self.newline_end(self.pos)))
                self.whitespace(out)
                continue
            if context.label_stop and c == "{" and LABEL_RE.match(self.text, self.pos):
                break
            out.append(self.inline(c, context))
            if self.pos == before:  # safety net
                out.append(self.token(K.ERROR_TOKEN, self.pos + 1))
        return out

    def inline(self, c: str, context: InlineContext) -> Node | Token:
        text, pos = self.text, self.pos
        if c == "\\":
            return self.backslash()
        if c == "%" and self.is_comment_start(pos):
            return self.comment()
        if c == "~":
            return self.token(K.NBSP, pos + 1)
        if c in "*_" and self.can_open(pos):
            return self.delimited(c, context)
        if c == "`":
            return self.code_span()
        if c == "<" and (m := AUTOLINK_RE.match(text, pos)):
            return self.token(K.AUTOLINK, m.end())
        if c == "[":
            return self.link(context)
        if (
            c == "@"
            and (pos == 0 or text[pos - 1] in REF_PRECEDERS)
            and (m := REF_RE.match(text, pos))
        ):
            return self.token(K.REF, m.end())
        if c == "#" and self.char(1) == "{":
            self.report(
                error(
                    Code.RESERVED_SYNTAX,
                    "`#{` is reserved",
                    Span(pos, pos + 2),
                    help="escape it as `\\#{` for literal text",
                )
            )
            return self.token(K.ERROR_TOKEN, pos + 1)
        if c == "#" and self.define_depth > 0 and (m := PARAM_RE.match(text, pos)):
            return self.token(K.PARAM, m.end())
        if c == "{":
            return self.group()
        if c == "}":  # only reached outside any body
            self.report(
                error(
                    Code.UNEXPECTED_CLOSING_BRACE,
                    "unexpected `}`",
                    Span(pos, pos + 1),
                    "no `{` is open here",
                    help="escape it as `\\}` for a literal brace",
                )
            )
            return self.token(K.ERROR_TOKEN, pos + 1)
        return self.text_run()

    def text_run(self) -> Token:
        end = self.pos + 1
        while end < self.n and self.text[end] not in TEXT_SPECIAL:
            end += 1
        return self.token(K.TEXT, end)

    def can_open(self, pos: int) -> bool:
        before = self.text[pos - 1] if pos > 0 else " "
        after = self.text[pos + 1] if pos + 1 < self.n else " "
        return not before.isalnum() and not after.isspace()

    def can_close(self, pos: int) -> bool:
        before = self.text[pos - 1] if pos > 0 else " "
        after = self.text[pos + 1] if pos + 1 < self.n else " "
        return not before.isspace() and not after.isalnum()

    def delimited(self, delimiter: str, context: InlineContext) -> Node | Token:
        start = self.pos
        key = (start, delimiter, context)
        if self.depth >= MAX_DEPTH:
            return self.token(K.TEXT, start + 1)
        if key not in self._failed and self.may_close_after(delimiter, start):
            saved = len(self.diagnostics)
            opener = self.token(K.DELIMITER, start + 1)
            self.depth += 1
            children = self.inlines(replace(context, closers=context.closers | {delimiter}))
            self.depth -= 1
            if self.char() == delimiter and self.can_close(self.pos) and children:
                closer = self.token(K.DELIMITER, self.pos + 1)
                kind = K.STRONG if delimiter == "*" else K.EMPH
                return Node(kind, [opener, *children, closer], offset=start)
            self.backtrack(key, saved)
        name = "strong text" if delimiter == "*" else "emphasis"
        self.report(
            warning(
                Code.UNCLOSED_DELIMITER,
                f"`{delimiter}` opens {name} but is never closed",
                Span(start, start + 1),
                "treated as literal text",
                help=f"escape it as `\\{delimiter}` to silence this warning",
            )
        )
        return self.token(K.TEXT, start + 1)

    def code_span(self) -> Node | Token:
        start = self.pos
        m = TICKS_RE.match(self.text, start)
        assert m is not None
        ticks = m.group(0)
        closing = re.compile(r"(?<!`)" + ticks + r"(?!`)")
        close = closing.search(self.text, m.end(), self.content_end(start))
        if close is None:
            self.report(
                error(
                    Code.UNCLOSED_CODE_SPAN,
                    "unclosed code span",
                    Span(start, m.end()),
                    "never closed on this line",
                    help=f"close it with {ticks}, or escape a literal backtick as \\`",
                )
            )
            return self.token(K.TEXT, m.end())
        node = Node(K.CODE_SPAN, offset=start)
        node.children.append(self.token(K.BACKTICKS, m.end()))
        if close.start() > self.pos:
            node.children.append(self.token(K.VERBATIM, close.start()))
        node.children.append(self.token(K.BACKTICKS, close.end()))
        return node

    def link(self, context: InlineContext) -> Node | Token:
        start = self.pos
        key = (start, "[", context)
        if key not in self._failed and self.depth < MAX_DEPTH and self.may_close_after("[", start):
            saved = len(self.diagnostics)
            label = Node(K.LINK_TEXT, offset=start)
            label.children.append(self.token(K.L_BRACKET, start + 1))
            self.depth += 1
            label.children.extend(self.inlines(replace(context, stops=context.stops | {"]"})))
            self.depth -= 1
            if self.text.startswith("](", self.pos):
                close = self.text.find(")", self.pos + 2, self.content_end(self.pos))
                if close != -1:
                    label.children.append(self.token(K.R_BRACKET, self.pos + 1))
                    target = Node(K.LINK_TARGET, offset=self.pos)
                    target.children.append(self.token(K.L_PAREN, self.pos + 1))
                    if close > self.pos:
                        target.children.append(self.token(K.VERBATIM, close))
                    target.children.append(self.token(K.R_PAREN, close + 1))
                    return Node(K.LINK, [label, target], offset=start)
            self.backtrack(key, saved)
        return self.token(K.TEXT, start + 1)

    def group(self) -> Node | Token:
        """A stray `{ ... }` in markup: an error, parsed as a group to keep structure."""
        start = self.pos
        if self.depth >= MAX_DEPTH:
            return self.too_deep()
        self.report(
            error(
                Code.UNEXPECTED_OPENING_BRACE,
                "unexpected `{` in text",
                Span(start, start + 1),
                help="escape it as `\\{` for a literal brace; `{` only follows a command",
            )
        )
        node = Node(K.GROUP, offset=start)
        node.children.append(self.token(K.L_BRACE, start + 1))
        self.body_depth += 1
        self.depth += 1
        node.children.extend(self.blocks(min_indent=0))
        self.depth -= 1
        self.body_depth -= 1
        self.close_brace(node, start, Code.UNCLOSED_GROUP)
        return node

    def close_brace(self, node: Node, open_at: int, code: Code = Code.UNCLOSED_BRACE) -> None:
        if self.char() == "}":
            node.children.append(self.token(K.R_BRACE, self.pos + 1))
        else:
            self.report(
                error(
                    code,
                    "unclosed `{`",
                    Span(open_at, open_at + 1),
                    "this `{` is never closed",
                    help="add a matching `}`",
                )
            )

    # --- commands ------------------------------------------------------------------

    def backslash(self) -> Node | Token:
        pos = self.pos
        nxt = self.char(1)
        if nxt == "#" and self.char(2) == "{":
            return self.token(K.ESCAPE, pos + 3)  # \#{ is a literal "#{" (design §3.2)
        if nxt and nxt in ESCAPABLE:
            return self.token(K.ESCAPE, pos + 2)
        if NAME_RE.match(self.text, pos + 1):
            return self.command()
        self.report(
            error(
                Code.INVALID_ESCAPE,
                "invalid escape or command",
                Span(pos, min(pos + 2, self.n)),
                help="write `\\\\` for a literal backslash",
            )
        )
        return self.token(K.ERROR_TOKEN, pos + 1)

    def command(self) -> Node:
        start = self.pos
        m = NAME_RE.match(self.text, start + 1)
        assert m is not None
        name = m.group(0)
        node = Node(K.COMMAND, offset=start)
        node.children.append(self.token(K.COMMAND_NAME, m.end()))
        if name in SPECIAL_FORMS:
            return self.definition(node, name)
        if self.char() == "[":
            node.children.append(self.options("[", "]"))
        if self.char() == "{":
            node.children.append(self.body(body_kind(name)))
        return node

    def body(self, kind: ArgKind) -> Node | Token:
        start = self.pos
        if kind is ArgKind.MARKUP:
            if self.depth >= MAX_DEPTH:
                return self.too_deep()
            node = Node(K.BODY, offset=start)
            node.children.append(self.token(K.L_BRACE, start + 1))
            self.body_depth += 1
            self.depth += 1
            node.children.extend(self.blocks(min_indent=0, body_start=True))
            self.depth -= 1
            self.body_depth -= 1
            self.close_brace(node, start)
            return node

        node = Node(K.VERBATIM_BODY, offset=start)
        node.children.append(self.token(K.L_BRACE, start + 1))
        end = self.verbatim_end(kind)
        if end is None:
            if self.pos < self.n:
                node.children.append(self.token(K.VERBATIM, self.n))
            self.close_brace(node, start)
            return node
        if end > self.pos:
            node.children.append(self.token(K.VERBATIM, end))
        node.children.append(self.token(K.R_BRACE, end + 1))
        return node

    def verbatim_end(self, kind: ArgKind) -> int | None:
        """Offset of the `}` closing a verbatim body that starts at `self.pos`."""
        depth, i, text = 0, self.pos, self.text
        while i < self.n:
            c = text[i]
            if c == "\\" and kind is not ArgKind.RAW:
                i += 2
                continue
            if c == "{" and kind is not ArgKind.STRING:
                depth += 1
            elif c == "}":
                if depth == 0:
                    return i
                depth -= 1
            i += 1
        return None

    def definition(self, node: Node, name: str) -> Node:
        """\\define{name}[params]{body} and \\let{name}{value}."""
        node.kind = K.DEFINE if name == "define" else K.LET
        if self.char() != "{" or not NAME_RE.match(self.text, self.pos + 1):
            self.report(
                error(
                    Code.MALFORMED_DEFINITION,
                    f"`\\{name}` needs a name in braces",
                    Span(node.offset, self.pos),
                    help=f"write \\{name}{{name}}... ; names start with a letter",
                )
            )
            return node
        group = Node(K.NAME_GROUP, offset=self.pos)
        group.children.append(self.token(K.L_BRACE, self.pos + 1))
        m = NAME_RE.match(self.text, self.pos)
        assert m is not None
        group.children.append(self.token(K.BARE, m.end()))
        node.children.append(group)
        if self.char() != "}":
            self.report(
                error(
                    Code.MALFORMED_DEFINITION,
                    "expected `}` after the name",
                    Span(self.pos, min(self.pos + 1, self.n)),
                    help="names contain letters, digits and hyphens",
                )
            )
            return node
        group.children.append(self.token(K.R_BRACE, self.pos + 1))
        if node.kind is K.DEFINE and self.char() == "[":
            node.children.append(self.options("[", "]"))
        if self.char() != "{":
            self.report(
                error(
                    Code.MALFORMED_DEFINITION,
                    f"`\\{name}` needs a body in braces",
                    Span(node.offset, self.pos),
                    help=f"write \\{name}{{name}}{{...}}",
                )
            )
            return node
        self.define_depth += node.kind is K.DEFINE
        node.children.append(self.body(ArgKind.MARKUP))
        self.define_depth -= node.kind is K.DEFINE
        return node

    # --- options -------------------------------------------------------------------

    def option_trivia(self, out: list[Node | Token]) -> None:
        while self.pos < self.n:
            c = self.text[self.pos]
            if c in " \t":
                self.whitespace(out)
            elif self.at_newline(self.pos):
                out.append(self.token(K.NEWLINE, self.newline_end(self.pos)))
            elif c == "\r":
                out.append(self.token(K.WHITESPACE, self.pos + 1))
            elif self.is_comment_start(self.pos):
                out.append(self.comment())
            else:
                break

    def options(self, open_char: str, close_char: str, *, fence_attributes: bool = False) -> Node:
        node = Node(K.OPTIONS, offset=self.pos)
        open_kind = K.L_BRACKET if open_char == "[" else K.L_BRACE
        node.children.append(self.token(open_kind, self.pos + 1))
        self.items(node, close_char, fence_attributes=fence_attributes)
        return node

    def items_interrupted(self, close: str) -> bool:
        """Whether option parsing must give up here (end of text or an enclosing body)."""
        c = self.char()
        return not c or (c == "}" and close != "}" and self.body_depth > 0)

    def items(self, node: Node, close: str, *, fence_attributes: bool = False) -> None:
        """Comma-separated option items, through the `close` character."""
        open_at = node.offset
        close_kind = {"]": K.R_BRACKET, "}": K.R_BRACE, ")": K.R_PAREN}[close]
        while True:
            self.option_trivia(node.children)
            if self.char() == close:
                node.children.append(self.token(close_kind, self.pos + 1))
                return
            if self.items_interrupted(close):
                opening = self.text[open_at]
                self.report(
                    error(
                        Code.UNCLOSED_OPTIONS,
                        f"unclosed `{opening}`",
                        Span(open_at, open_at + 1),
                        f"this `{opening}` is never closed",
                        help=f"add a matching `{close}`",
                    )
                )
                return
            option = self.option(fence_attributes)
            if option is not None:
                node.children.append(option)
                self.option_trivia(node.children)
            c = self.char()
            if c == ",":
                node.children.append(self.token(K.COMMA, self.pos + 1))
            elif c != close and not self.items_interrupted(close):
                if option is not None:
                    self.report(
                        error(
                            Code.INVALID_OPTION,
                            f"expected `,` or `{close}`",
                            Span(self.pos, self.pos + 1),
                            help="separate options with commas; quote values containing spaces",
                        )
                    )
                node.children.append(self.token(K.ERROR_TOKEN, self.pos + 1))

    def option(self, fence_attributes: bool) -> Node | None:
        node = Node(K.OPTION, offset=self.pos)
        if fence_attributes and (m := HASH_LABEL_RE.match(self.text, self.pos)):
            node.children.append(self.token(K.HASH_LABEL, m.end()))
            return node
        first = self.value()
        if first is None:
            return None
        node.children.append(first)
        save = self.pos
        spaces: list[Node | Token] = []
        self.whitespace(spaces)
        if self.char() != "=":
            self.pos = save
            return node
        if not (isinstance(first, Token) and first.kind is K.BARE and KEY_RE.fullmatch(first.text)):
            self.report(
                error(
                    Code.INVALID_OPTION,
                    "invalid option name",
                    first.span,
                    help="option names start with a letter, e.g. space-before=12pt",
                )
            )
        node.children.extend(spaces)
        node.children.append(self.token(K.EQUALS, self.pos + 1))
        self.whitespace(node.children)
        value = self.value()
        if value is not None:
            node.children.append(value)
        return node

    def value(self) -> Node | Token | None:
        c = self.char()
        if c == '"':
            return self.string()
        if c == "(":
            if self.depth >= MAX_DEPTH:
                return self.too_deep()
            node = Node(K.TUPLE, offset=self.pos)
            node.children.append(self.token(K.L_PAREN, self.pos + 1))
            self.depth += 1
            self.items(node, ")")
            self.depth -= 1
            return node
        if c == "{":
            return self.body(ArgKind.MARKUP)
        if c == "#" and self.define_depth > 0 and (m := PARAM_RE.match(self.text, self.pos)):
            return self.token(K.PARAM, m.end())
        end = self.pos
        while end < self.n and self.text[end] not in BARE_STOP:
            end += 1
        if end > self.pos and self.text.startswith("(", end):
            # A function-style value such as theme.accent1.darker(25%).
            close = self.text.find(")", end, self.content_end(end))
            if close != -1:
                end = close + 1
        if end == self.pos:
            self.report(
                error(
                    Code.INVALID_OPTION,
                    "expected an option value",
                    Span(self.pos, min(self.pos + 1, self.n)),
                    help='values are words, numbers with units, "strings", (tuples) or {markup}',
                )
            )
            return None
        return self.token(K.BARE, end)

    def string(self) -> Token:
        start = self.pos
        i = start + 1
        while i < self.n and self.text[i] not in '"\n':
            i += 2 if self.text[i] == "\\" else 1
        if i < self.n and self.text[i] == '"':
            return self.token(K.STRING, i + 1)
        self.report(
            error(
                Code.UNCLOSED_STRING,
                "unclosed string",
                Span(start, start + 1),
                'this `"` is never closed',
                help='a string must end with `"` on the same line',
            )
        )
        return self.token(K.STRING, max(self.content_end(start), start + 1))


def parse_cst(source: Source) -> tuple[Node, list[Diagnostic]]:
    parser = Parser(source)
    root = parser.parse()
    return root, parser.diagnostics
