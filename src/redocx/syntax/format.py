"""The formatter (``redocx fmt``): a canonical layout that keeps the meaning.

It works on the lossless CST, so comments, escapes and the author's line breaks stay
where they are; it only changes layout that never matters:

* trailing spaces and tabs at the end of a line,
* runs of blank lines (at most one is kept), blank lines at the start of the file, and
  the end of the file (exactly one newline),
* line endings (all as the file's first one),
* option lists on one line: ``[a = 1 ,b=2]`` -> ``[a=1, b=2]``,
* the space after a heading marker: ``=   Title`` -> ``= Title``.

Verbatim content (code blocks, display math, code spans, string and raw bodies) is left
exactly as written. Paragraphs are not re-wrapped and pipe tables are not aligned.

A file with syntax errors is not formatted. The result is checked: it must parse to the
same document (whitespace that does not matter aside), or `FormatError` is raised.
"""

from __future__ import annotations

import dataclasses
import re
from dataclasses import dataclass

from redocx.syntax import ast, parse
from redocx.syntax.cst import Node, SyntaxKind, Token
from redocx.syntax.diagnostics import Diagnostic
from redocx.syntax.source import Source

K = SyntaxKind

LONE_CR_RE = re.compile(r"\r(?!\n)")

VERBATIM_NODES = frozenset({K.DISPLAY_MATH, K.VERBATIM_BODY, K.CODE_SPAN})
OPTION_LISTS = frozenset({K.OPTIONS, K.TUPLE})
CLOSERS = frozenset({K.R_BRACKET, K.R_PAREN, K.R_BRACE})
VALUE_BODIES = frozenset({K.BODY, K.VERBATIM_BODY})
"""Option values whose content is not part of the option list's own layout."""


class FormatError(RuntimeError):
    """The formatted text would mean something else: a bug in the formatter."""


@dataclass(frozen=True)
class FormatResult:
    text: str | None
    """The formatted source; None when it is not formatted (see `problem`)."""
    diagnostics: tuple[Diagnostic, ...]
    problem: str | None = None
    """Why the source is not formatted: syntax errors, or a lone carriage return."""

    def changed(self, original: str) -> bool:
        return self.text is not None and self.text != original


@dataclass
class _Piece:
    kind: SyntaxKind
    text: str
    verbatim: bool


def format_source(source: Source | str) -> FormatResult:
    source = source if isinstance(source, Source) else Source("<input>", source)
    result = parse(source)
    errors = tuple(d for d in result.diagnostics if d.is_error)
    if errors:
        count = f"{len(errors)} syntax error{'s' * (len(errors) != 1)}"
        return FormatResult(None, errors, f"not formatted: {count}")
    if LONE_CR_RE.search(source.text):
        problem = "not formatted: a carriage return without a line feed (old Mac line endings)"
        return FormatResult(None, result.diagnostics, problem)
    pieces: list[_Piece] = []
    _walk(result.cst, pieces, verbatim=False)
    text = _layout(pieces, _newline(pieces))
    if text != source.text:
        again = parse(Source(source.name, text))
        if not again.ok or _meaning(again.ast) != _meaning(result.ast):
            raise FormatError(f"formatting {source.name} would change the document")
    return FormatResult(text, result.diagnostics)


def _newline(pieces: list[_Piece]) -> str:
    """The line ending of the first line break outside verbatim content (between blocks a
    CRLF's CR is a whitespace token of its own)."""
    for i, piece in enumerate(pieces):
        if piece.kind is K.NEWLINE and not piece.verbatim:
            before = pieces[i - 1].text if i > 0 else ""
            return "\r\n" if piece.text == "\r\n" or before.endswith("\r") else "\n"
    return "\n"


# --- tree walk ---------------------------------------------------------------------------


def _walk(node: Node, out: list[_Piece], *, verbatim: bool) -> None:
    verbatim = verbatim or node.kind in VERBATIM_NODES
    if not verbatim and node.kind in OPTION_LISTS and _on_one_line(node):
        _option_list(node, out)
        return
    for i, child in enumerate(node.children):
        if isinstance(child, Node):
            if node.kind is K.CODE_BLOCK and child.kind is not K.OPTIONS:
                _walk(child, out, verbatim=True)
            else:
                _walk(child, out, verbatim=verbatim)
            continue
        text = child.text
        keep = verbatim or (node.kind is K.CODE_BLOCK and child.kind is not K.FENCE_LANG)
        if (
            not verbatim
            and child.kind is K.WHITESPACE
            and i > 0
            and _is_token(node.children[i - 1], K.HEADING_MARKER, K.LIST_MARKER)
        ):
            # The space after a marker makes it one (`- ` is an empty item, `-` is text).
            keep = True
            if node.kind is K.HEADING:
                text = " "
        out.append(_Piece(child.kind, text, keep))


def _is_token(element: Node | Token, *kinds: SyntaxKind) -> bool:
    return isinstance(element, Token) and element.kind in kinds


def _on_one_line(node: Node) -> bool:
    """No line break or comment in the list itself (markup values may have them)."""
    for child in node.children:
        if isinstance(child, Token):
            if child.kind in (K.NEWLINE, K.COMMENT, K.ERROR_TOKEN):
                return False
        elif child.kind not in VALUE_BODIES and not _on_one_line(child):
            return False
    return True


def _option_list(node: Node, out: list[_Piece]) -> None:
    """``[a=1, b=(x, y)]``: no spaces around `=` or inside the brackets, one after commas."""
    space = False
    for child in node.children:
        if _is_token(child, K.WHITESPACE):
            continue
        if space and not (isinstance(child, Token) and child.kind in CLOSERS):
            out.append(_Piece(K.WHITESPACE, " ", False))
        space = _is_token(child, K.COMMA)
        if isinstance(child, Token):
            out.append(_Piece(child.kind, child.text, False))
        elif child.kind is K.OPTION:
            for part in child.children:
                if _is_token(part, K.WHITESPACE):
                    continue
                if isinstance(part, Token):
                    out.append(_Piece(part.kind, part.text, False))
                elif part.kind in OPTION_LISTS:
                    _option_list(part, out)
                else:
                    _walk(part, out, verbatim=False)
        else:
            _walk(child, out, verbatim=False)


# --- whitespace and blank lines ----------------------------------------------------------


def _layout(pieces: list[_Piece], newline: str) -> str:
    out: list[str] = []
    pending_newlines = 0
    started = False
    for i, piece in enumerate(pieces):
        if piece.verbatim:
            out.append(newline * pending_newlines)
            pending_newlines = 0
            out.append(piece.text)
            started = True
            continue
        if piece.kind is K.NEWLINE:
            if started:
                pending_newlines = min(pending_newlines + 1, 2)
            continue
        text = piece.text
        if _ends_line(pieces, i) and piece.kind is not K.ESCAPE:  # `\ ` is a kept space
            text = text.rstrip(" \t\r")  # a block-level CRLF's CR is a whitespace token
        if not text:
            continue
        out.append(newline * pending_newlines)
        pending_newlines = 0
        out.append(text)
        started = True
    if started:
        out.append(newline)
    return "".join(out)


def _ends_line(pieces: list[_Piece], i: int) -> bool:
    """Whether only spaces and tabs separate piece `i` from the end of its line."""
    for piece in pieces[i + 1 :]:
        if piece.kind is K.NEWLINE and not piece.verbatim:
            return True
        blank = piece.kind in (K.WHITESPACE, K.TEXT) and not piece.text.strip(" \t\r")
        if piece.verbatim or not blank:
            return False
    return True


# --- meaning -----------------------------------------------------------------------------


def _meaning(node: object) -> object:
    """The AST without positions, and with the whitespace that does not matter removed:
    spaces next to a line break within a paragraph, or at the end of a run of text."""
    if isinstance(node, ast.AstNode):
        return (
            type(node).__name__,
            *(
                _meaning(getattr(node, f.name))
                for f in dataclasses.fields(node)
                if f.name != "span"
            ),
        )
    if isinstance(node, tuple):
        items: tuple[object, ...] = node  # pyright: ignore[reportUnknownVariableType]
        return tuple(_meaning(item) for item in _inline_whitespace(items))
    return node


def _inline_whitespace(items: tuple[object, ...]) -> list[object]:
    out = list(items)
    for i, item in enumerate(out):
        if not isinstance(item, ast.Text) or item.literal:  # `\ ` is a space that counts
            continue
        text = item.text
        if i + 1 == len(out) or isinstance(out[i + 1], ast.SoftBreak):
            text = text.rstrip(" \t\r")
        if i > 0 and isinstance(out[i - 1], ast.SoftBreak):
            text = text.lstrip(" \t")
        out[i] = dataclasses.replace(item, text=text)
    return [item for item in out if not (isinstance(item, ast.Text) and not item.text)]
