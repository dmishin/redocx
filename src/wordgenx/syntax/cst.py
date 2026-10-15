"""The concrete syntax tree (CST).

Lossless: every character of the source belongs to exactly one token, in order,
so ``node.text`` of the root reproduces the source exactly, including comments,
whitespace and erroneous input. The formatter and language server work on the
CST; the compiler works on the AST built from it (`wordgenx.syntax.ast`).
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from enum import StrEnum

from wordgenx.syntax.source import Span


class SyntaxKind(StrEnum):
    # Tokens -------------------------------------------------------------------
    TEXT = "TEXT"
    WHITESPACE = "WHITESPACE"
    NEWLINE = "NEWLINE"
    COMMENT = "COMMENT"
    ESCAPE = "ESCAPE"  # \% \{ ...
    NBSP = "NBSP"  # ~
    COMMAND_NAME = "COMMAND_NAME"  # \name
    L_BRACKET = "L_BRACKET"
    R_BRACKET = "R_BRACKET"
    L_BRACE = "L_BRACE"
    R_BRACE = "R_BRACE"
    L_PAREN = "L_PAREN"
    R_PAREN = "R_PAREN"
    COMMA = "COMMA"
    EQUALS = "EQUALS"
    STRING = "STRING"  # "quoted" option value
    BARE = "BARE"  # unquoted option key or value
    PARAM = "PARAM"  # #name inside \define
    HASH_LABEL = "HASH_LABEL"  # #label inside code-fence attributes
    VERBATIM = "VERBATIM"  # string/raw/math bodies, code and math blocks
    DELIMITER = "DELIMITER"  # * or _
    BACKTICKS = "BACKTICKS"
    AUTOLINK = "AUTOLINK"  # <scheme:...>
    REF = "REF"  # @label
    LABEL = "LABEL"  # {#label}
    HEADING_MARKER = "HEADING_MARKER"  # = == =* ...
    LIST_MARKER = "LIST_MARKER"  # - 1. a.
    PIPE = "PIPE"
    FENCE = "FENCE"  # ```
    FENCE_LANG = "FENCE_LANG"
    DOLLARS = "DOLLARS"  # $$
    ERROR_TOKEN = "ERROR_TOKEN"

    # Nodes --------------------------------------------------------------------
    DOCUMENT = "DOCUMENT"
    PARAGRAPH = "PARAGRAPH"
    HEADING = "HEADING"
    LIST = "LIST"
    LIST_ITEM = "LIST_ITEM"
    CODE_BLOCK = "CODE_BLOCK"
    DISPLAY_MATH = "DISPLAY_MATH"
    PIPE_TABLE = "PIPE_TABLE"
    PIPE_ROW = "PIPE_ROW"
    PIPE_CELL = "PIPE_CELL"
    COMMAND = "COMMAND"
    OPTIONS = "OPTIONS"  # [ ... ] or a code fence's { ... }
    OPTION = "OPTION"
    TUPLE = "TUPLE"
    BODY = "BODY"  # { markup }
    VERBATIM_BODY = "VERBATIM_BODY"  # { string | raw | math }
    NAME_GROUP = "NAME_GROUP"  # {name} of \define / \let
    STRONG = "STRONG"
    EMPH = "EMPH"
    CODE_SPAN = "CODE_SPAN"
    LINK = "LINK"  # [text](target)
    LINK_TEXT = "LINK_TEXT"
    LINK_TARGET = "LINK_TARGET"
    GROUP = "GROUP"  # a stray { ... } in markup (an error, kept for recovery)
    DEFINE = "DEFINE"
    LET = "LET"


@dataclass(slots=True)
class Token:
    kind: SyntaxKind
    text: str
    offset: int

    @property
    def span(self) -> Span:
        return Span(self.offset, self.offset + len(self.text))


@dataclass(slots=True)
class Node:
    kind: SyntaxKind
    children: list[Node | Token] = field(default_factory=lambda: [])
    offset: int = 0
    """Start offset; set by the parser (needed for empty nodes)."""

    @property
    def text(self) -> str:
        return "".join(t.text for t in self.tokens())

    @property
    def span(self) -> Span:
        return Span(self.offset, self.offset + sum(len(t.text) for t in self.tokens()))

    def tokens(self) -> Iterator[Token]:
        for child in self.children:
            if isinstance(child, Token):
                yield child
            else:
                yield from child.tokens()

    def nodes(self, kind: SyntaxKind | None = None) -> Iterator[Node]:
        """Direct child nodes, optionally of one kind."""
        for child in self.children:
            if isinstance(child, Node) and (kind is None or child.kind is kind):
                yield child

    def child_tokens(self, kind: SyntaxKind | None = None) -> Iterator[Token]:
        for child in self.children:
            if isinstance(child, Token) and (kind is None or child.kind is kind):
                yield child

    def first_node(self, kind: SyntaxKind) -> Node | None:
        return next(self.nodes(kind), None)

    def first_token(self, kind: SyntaxKind) -> Token | None:
        return next(self.child_tokens(kind), None)


def dump(node: Node, indent: int = 0) -> str:
    """An indented tree for debugging and tests."""
    pad = "  " * indent
    lines = [f"{pad}{node.kind}@{node.span.start}..{node.span.end}"]
    for child in node.children:
        if isinstance(child, Token):
            lines.append(f"{pad}  {child.kind}@{child.offset} {child.text!r}")
        else:
            lines.append(dump(child, indent + 1))
    return "\n".join(lines)
