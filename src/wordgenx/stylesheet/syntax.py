"""Style-sheet syntax (.wgxs files and \\styles{...} bodies), language design §3.5 / §3.7.

    defaults { font: theme.minor; size: 11pt; }

    paragraph "Body Text" : Normal {
      space-after: 6pt; justify; next: "Body Text";
    }
    modify paragraph "Heading 1" { color: theme.accent1; numbering: Headings level 1; }
    character Term { italic; color: theme.accent2; }
    linked Quote : Normal { indent: (left=1cm, right=1cm); italic; }

    numbering Headings {
      level 1 { format: decimal; text: "%1"; style: "Heading 1"; }
    }

A rule is ``[modify|replace] kind name [: base] { ... }``. A block holds
declarations ``key: value;``, flags ``key;`` and nested blocks ``name args { ... }``
(``level 1 { ... }``, ``header-row { ... }``). Values are kept as raw text here and
interpreted by the compiler. Comments start with ``%`` at a line start or after
whitespace, as in documents.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from wordgenx.syntax.diagnostics import Code, Diagnostic, error
from wordgenx.syntax.source import Source, Span

STYLE_KINDS = ("paragraph", "character", "linked", "table")
ACTIONS = ("modify", "replace")
WORD_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*")
RULES_HELP = "rules start with defaults, paragraph, character, linked, table, numbering or theme"


@dataclass(frozen=True)
class Declaration:
    key: str
    key_span: Span
    value: str | None
    """Raw value text (trimmed); None for a flag such as ``bold;``."""
    value_span: Span | None

    @property
    def span(self) -> Span:
        return self.key_span if self.value_span is None else self.key_span.cover(self.value_span)


@dataclass(frozen=True)
class Block:
    """A nested block: ``level 1 { ... }``, ``header-row { ... }``."""

    name: str
    args: tuple[str, ...]
    span: Span
    """The header (name and arguments)."""
    items: tuple[Declaration | Block, ...]


@dataclass(frozen=True)
class Name:
    text: str
    span: Span


@dataclass(frozen=True)
class StyleRule:
    action: str
    """``define``, ``modify`` or ``replace``."""
    kind: str
    name: Name
    based_on: Name | None
    items: tuple[Declaration | Block, ...]
    span: Span
    """The rule header."""


@dataclass(frozen=True)
class DefaultsRule:
    items: tuple[Declaration | Block, ...]
    span: Span


@dataclass(frozen=True)
class NumberingRule:
    name: Name
    items: tuple[Declaration | Block, ...]
    span: Span


@dataclass(frozen=True)
class ThemeRule:
    """``theme [Name] { accent1: #0B7A75; major-font: "Georgia"; }``."""

    name: Name | None
    items: tuple[Declaration | Block, ...]
    span: Span


Rule = StyleRule | DefaultsRule | NumberingRule | ThemeRule


@dataclass(frozen=True)
class StyleSheet:
    source: Source
    rules: tuple[Rule, ...]
    diagnostics: tuple[Diagnostic, ...]


class _Parser:
    def __init__(self, source: Source, start: int, end: int) -> None:
        self.source = source
        self.text = source.text
        self.pos: int = start
        self.end: int = end
        self.diagnostics: list[Diagnostic] = []

    # --- scanning -----------------------------------------------------------------

    def char(self) -> str:
        return self.text[self.pos] if self.pos < self.end else ""

    def is_comment_start(self, pos: int) -> bool:
        return self.text.startswith("%", pos) and (pos == 0 or self.text[pos - 1] in " \t\r\n")

    def skip_trivia(self) -> None:
        while self.pos < self.end:
            c = self.text[self.pos]
            if c in " \t\r\n":
                self.pos += 1
            elif self.is_comment_start(self.pos):
                newline = self.text.find("\n", self.pos, self.end)
                self.pos = self.end if newline < 0 else newline
            else:
                break

    def skip_spaces(self) -> None:
        while self.pos < self.end and self.text[self.pos] in " \t":
            self.pos += 1

    def fail(self, message: str, span: Span | None = None, help: str | None = None) -> None:
        span = span or Span(self.pos, min(self.pos + 1, self.end))
        self.diagnostics.append(error(Code.STYLESHEET_SYNTAX, message, span, help=help))

    def word(self) -> Name | None:
        m = WORD_RE.match(self.text, self.pos, self.end)
        if m is None:
            return None
        self.pos = m.end()
        return Name(m.group(0), Span(m.start(), m.end()))

    def string(self) -> Name | None:
        if self.char() != '"':
            return None
        start = self.pos
        i = start + 1
        chars: list[str] = []
        while i < self.end and self.text[i] not in '"\n':
            if self.text[i] == "\\" and i + 1 < self.end:
                i += 1
            chars.append(self.text[i])
            i += 1
        if i < self.end and self.text[i] == '"':
            self.pos = i + 1
        else:
            self.pos = i
            self.fail(
                "unclosed string", Span(start, start + 1), help='end it with `"` on the same line'
            )
        return Name("".join(chars), Span(start, self.pos))

    def name(self) -> Name | None:
        return self.string() or self.word()

    def skip_past_block(self) -> None:
        """Error recovery: skip to just after the next `}` at depth 0 (or the end)."""
        depth = 0
        while self.pos < self.end:
            c = self.text[self.pos]
            self.pos += 1
            if c == "{":
                depth += 1
            elif c == "}":
                if depth <= 1:
                    return
                depth -= 1

    # --- grammar --------------------------------------------------------------------

    def sheet(self) -> tuple[Rule, ...]:
        rules: list[Rule] = []
        while True:
            self.skip_trivia()
            if self.pos >= self.end:
                return tuple(rules)
            start = self.pos
            rule = self.rule()
            if rule is not None:
                rules.append(rule)
            elif self.pos == start:
                self.skip_past_block()

    def rule(self) -> Rule | None:
        start = self.pos
        keyword = self.word()
        if keyword is None:
            self.fail(
                "expected a rule",
                help=RULES_HELP,
            )
            return None
        if keyword.text == "defaults":
            items = self.block()
            return DefaultsRule(items, keyword.span) if items is not None else None
        if keyword.text == "theme":
            self.skip_spaces()
            name = self.name() if self.char() != "{" else None
            items = self.block()
            if items is None:
                return None
            return ThemeRule(name, items, Span(start, (name or keyword).span.end))
        if keyword.text == "numbering":
            self.skip_spaces()
            name = self.name()
            if name is None:
                self.fail("expected a numbering name", help="e.g. numbering Headings { ... }")
                return None
            items = self.block()
            if items is None:
                return None
            return NumberingRule(name, items, Span(start, name.span.end))

        action = "define"
        kind = keyword
        if keyword.text in ACTIONS:
            action = keyword.text
            self.skip_spaces()
            next_word = self.word()
            if next_word is None or next_word.text not in STYLE_KINDS:
                self.fail(
                    f"expected a style kind after `{action}`",
                    next_word.span if next_word else None,
                    help="paragraph, character, linked or table",
                )
                return None
            kind = next_word
        if kind.text not in STYLE_KINDS:
            self.fail(
                f"unknown rule `{kind.text}`",
                kind.span,
                help=RULES_HELP,
            )
            return None
        self.skip_spaces()
        name = self.name()
        if name is None:
            self.fail(
                f"expected a {kind.text} style name", help='quote names with spaces: "Body Text"'
            )
            return None
        header_end = name.span.end
        based_on: Name | None = None
        self.skip_spaces()
        if self.char() == ":":
            self.pos += 1
            self.skip_spaces()
            based_on = self.name()
            if based_on is None:
                self.fail("expected the name of the base style after `:`")
                return None
            header_end = based_on.span.end
        items = self.block()
        if items is None:
            return None
        return StyleRule(action, kind.text, name, based_on, items, Span(start, header_end))

    def block(self) -> tuple[Declaration | Block, ...] | None:
        self.skip_trivia()
        if self.char() != "{":
            self.fail("expected `{`")
            return None
        open_at = self.pos
        self.pos += 1
        items: list[Declaration | Block] = []
        while True:
            self.skip_trivia()
            c = self.char()
            if not c:
                self.fail("unclosed `{`", Span(open_at, open_at + 1), help="add a matching `}`")
                return tuple(items)
            if c == "}":
                self.pos += 1
                return tuple(items)
            if c == ";":
                self.pos += 1  # stray separator
                continue
            before = self.pos
            item = self.item()
            if item is not None:
                items.append(item)
            elif self.pos == before:
                self.recover()

    def recover(self) -> None:
        """Skip to after the next `;`, or to the next `}` of this block."""
        while self.pos < self.end and self.text[self.pos] not in ";}":
            if self.text[self.pos] == "{":
                self.skip_past_block()
                continue
            self.pos += 1
        if self.char() == ";":
            self.pos += 1

    def item(self) -> Declaration | Block | None:
        key = self.word()
        if key is None:
            self.fail("expected a property name", help="declarations look like `size: 11pt;`")
            return None
        self.skip_spaces()
        if self.char() == ":":
            self.pos += 1
            return self.declaration(key)
        args: list[Name] = []
        while True:
            self.skip_spaces()
            arg = self.word() or self.string()
            if arg is None:
                break
            args.append(arg)
        c = self.char()
        if c == "{":
            header_end = args[-1].span.end if args else key.span.end
            items = self.block()
            return Block(
                key.text, tuple(a.text for a in args), Span(key.span.start, header_end), items or ()
            )
        if c in ";}\r\n" or not c or self.is_comment_start(self.pos):
            if args:
                self.fail(
                    f"expected `:` after `{key.text}`",
                    Span(key.span.end, key.span.end + 1),
                    help="write `key: value;`",
                )
                return None
            if c == ";":
                self.pos += 1
            return Declaration(key.text, key.span, None, None)
        self.fail(f"expected `:` after `{key.text}`", help="write `key: value;`")
        return None

    def declaration(self, key: Name) -> Declaration | None:
        """The value after `key:`, up to `;` or the block's `}` (outside quotes and parentheses)."""
        self.skip_spaces()
        start = self.pos
        depth = 0
        end = start
        while self.pos < self.end:
            c = self.text[self.pos]
            if c == '"':
                self.string()
                end = self.pos
                continue
            if self.is_comment_start(self.pos):
                newline = self.text.find("\n", self.pos, self.end)
                self.pos = self.end if newline < 0 else newline
                continue
            if c == "(":
                depth += 1
            elif c == ")":
                depth = max(0, depth - 1)
            elif depth == 0 and c in ";}":
                break
            elif depth == 0 and c == "{":
                self.fail("unexpected `{` in a value", help="did you forget a `;`?")
                break
            self.pos += 1
            if not c.isspace():
                end = self.pos
        value = self.text[start:end]
        if self.char() == ";":
            self.pos += 1
        if not value:
            self.fail(f"`{key.text}` has no value", key.span, help=f"write `{key.text}: value;`")
            return None
        return Declaration(key.text, key.span, value, Span(start, end))


def parse_stylesheet(source: Source, start: int = 0, end: int | None = None) -> StyleSheet:
    """Parse a style sheet: a whole .wgxs source, or the region [start, end) of a document."""
    parser = _Parser(source, start, len(source.text) if end is None else end)
    rules = parser.sheet()
    return StyleSheet(source, rules, tuple(parser.diagnostics))
