"""The abstract syntax tree (AST): typed, immutable, with source spans.

Built from the CST by `build`. Comments and layout trivia are dropped, escapes
are resolved, and adjacent text is merged. Nothing is interpreted yet: commands
are kept generic (name, options, body); the expansion and lowering stages give
them meaning.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, fields

from wordgenx.syntax.commands import ArgKind, body_kind
from wordgenx.syntax.cst import Node as CstNode
from wordgenx.syntax.cst import SyntaxKind as K
from wordgenx.syntax.cst import Token
from wordgenx.syntax.source import Span

# --- nodes -----------------------------------------------------------------------------


@dataclass(frozen=True, kw_only=True)
class AstNode:
    span: Span


@dataclass(frozen=True)
class Label(AstNode):
    name: str


# Inline nodes.


@dataclass(frozen=True)
class Text(AstNode):
    text: str


@dataclass(frozen=True)
class SoftBreak(AstNode):
    """A line break inside a paragraph (rendered as a space)."""


@dataclass(frozen=True)
class NonBreakingSpace(AstNode):
    pass


@dataclass(frozen=True)
class Strong(AstNode):
    content: tuple[Inline, ...]


@dataclass(frozen=True)
class Emph(AstNode):
    content: tuple[Inline, ...]


@dataclass(frozen=True)
class CodeSpan(AstNode):
    code: str


@dataclass(frozen=True)
class Autolink(AstNode):
    url: str


@dataclass(frozen=True)
class Link(AstNode):
    content: tuple[Inline, ...]
    target: str


@dataclass(frozen=True)
class Ref(AstNode):
    label: str


@dataclass(frozen=True)
class Param(AstNode):
    """``#name`` inside a \\define body."""

    name: str


@dataclass(frozen=True)
class Command(AstNode):
    name: str
    options: Options | None
    body: Body | None


@dataclass(frozen=True)
class Define(AstNode):
    name: str
    params: Options | None
    body: MarkupBody | None


@dataclass(frozen=True)
class Let(AstNode):
    name: str
    body: MarkupBody | None


@dataclass(frozen=True)
class Group(AstNode):
    """A stray ``{...}`` (already reported as an error); its content is kept."""

    blocks: tuple[Block, ...]


Inline = (
    Text | SoftBreak | NonBreakingSpace | Strong | Emph | CodeSpan | Autolink | Link | Ref | Param
    | Command | Define | Let | Group
)  # fmt: skip

# Command bodies and options.


@dataclass(frozen=True)
class MarkupBody(AstNode):
    blocks: tuple[Block, ...]


@dataclass(frozen=True)
class VerbatimBody(AstNode):
    kind: ArgKind
    text: str
    unclosed: bool = False
    """The closing `}` is missing (reported): the text runs to the end of the file."""


Body = MarkupBody | VerbatimBody


@dataclass(frozen=True)
class BareValue(AstNode):
    text: str


@dataclass(frozen=True)
class StringValue(AstNode):
    text: str


@dataclass(frozen=True)
class TupleValue(AstNode):
    items: tuple[Option, ...]


@dataclass(frozen=True)
class ParamValue(AstNode):
    name: str


Value = BareValue | StringValue | TupleValue | MarkupBody | ParamValue


@dataclass(frozen=True)
class Option(AstNode):
    key: str | None
    value: Value


@dataclass(frozen=True)
class Options(AstNode):
    items: tuple[Option, ...]


# Blocks.


@dataclass(frozen=True)
class Paragraph(AstNode):
    content: tuple[Inline, ...]


@dataclass(frozen=True)
class Heading(AstNode):
    level: int
    numbered: bool
    content: tuple[Inline, ...]
    label: Label | None


@dataclass(frozen=True)
class ListItem(AstNode):
    marker: str
    label: Label | None
    blocks: tuple[Block, ...]


@dataclass(frozen=True)
class ListBlock(AstNode):
    ordered: bool
    items: tuple[ListItem, ...]


@dataclass(frozen=True)
class CodeBlock(AstNode):
    language: str | None
    options: Options | None
    code: str


@dataclass(frozen=True)
class DisplayMath(AstNode):
    source: str
    label: Label | None


@dataclass(frozen=True)
class PipeCell(AstNode):
    content: tuple[Inline, ...]


@dataclass(frozen=True)
class PipeRow(AstNode):
    cells: tuple[PipeCell, ...]


@dataclass(frozen=True)
class PipeTable(AstNode):
    rows: tuple[PipeRow, ...]


Block = Paragraph | Heading | ListBlock | CodeBlock | DisplayMath | PipeTable


@dataclass(frozen=True)
class Document(AstNode):
    blocks: tuple[Block, ...]


# --- CST -> AST ------------------------------------------------------------------------


def build(root: CstNode) -> Document:
    return Document(_blocks(root.children), span=root.span)


def _blocks(children: Iterable[CstNode | Token]) -> tuple[Block, ...]:
    blocks: list[Block] = []
    for child in children:
        if isinstance(child, Token):
            continue  # trivia: whitespace, newlines, comments, error tokens between blocks
        block = _block(child)
        if block is not None:
            blocks.append(block)
    return tuple(blocks)


def _block(node: CstNode) -> Block | None:
    match node.kind:
        case K.PARAGRAPH:
            content = _inlines(node.children)
            return Paragraph(content, span=node.span) if content else None
        case K.HEADING:
            marker = node.children[0]
            assert isinstance(marker, Token)
            return Heading(
                marker.text.count("="),
                not marker.text.endswith("*"),
                _strip(_inlines(node.children[1:])),
                _label(node),
                span=node.span,
            )
        case K.LIST:
            items = tuple(_list_item(item) for item in node.nodes(K.LIST_ITEM))
            ordered = items[0].marker != "-"
            return ListBlock(ordered, items, span=node.span)
        case K.CODE_BLOCK:
            return _code_block(node)
        case K.DISPLAY_MATH:
            verbatim = node.first_token(K.VERBATIM)
            source = verbatim.text.strip() if verbatim else ""
            return DisplayMath(source, _label(node), span=node.span)
        case K.PIPE_TABLE:
            rows = tuple(_pipe_row(row) for row in node.nodes(K.PIPE_ROW))
            return PipeTable(rows, span=node.span)
        case _:
            raise AssertionError(f"unexpected block node {node.kind}")


def _label(node: CstNode) -> Label | None:
    token = node.first_token(K.LABEL)
    if token is None:
        return None
    return Label(token.text[2:-1], span=token.span)


def _list_item(node: CstNode) -> ListItem:
    marker = node.first_token(K.LIST_MARKER)
    assert marker is not None
    return ListItem(marker.text, _label(node), _blocks(node.children), span=node.span)


def _code_block(node: CstNode) -> CodeBlock:
    lang = node.first_token(K.FENCE_LANG)
    options = node.first_node(K.OPTIONS)
    verbatim = node.first_token(K.VERBATIM)
    code = verbatim.text if verbatim else ""
    fence = node.first_token(K.FENCE)
    assert fence is not None
    indent = _indent_before(node)
    lines = code.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    code = "\n".join(_dedent(line.rstrip("\r"), indent) for line in lines)
    return CodeBlock(
        lang.text if lang else None,
        _options(options) if options else None,
        code,
        span=node.span,
    )


def _indent_before(node: CstNode) -> int:
    """Indentation of the closing fence (used to dedent the code)."""
    tokens = list(node.tokens())
    for i in range(len(tokens) - 1, 0, -1):
        if tokens[i].kind is K.FENCE and tokens[i - 1].kind is K.WHITESPACE:
            return len(tokens[i - 1].text.expandtabs(4))
    return 0


def _dedent(line: str, indent: int) -> str:
    i = 0
    while i < len(line) and i < indent and line[i] == " ":
        i += 1
    return line[i:]


def _pipe_row(node: CstNode) -> PipeRow:
    cells: list[PipeCell] = []
    children = node.children
    for i, child in enumerate(children):
        if isinstance(child, CstNode) and child.kind is K.PIPE_CELL:
            content = _strip(_inlines(child.children))
            closed = i + 1 < len(children) and isinstance(children[i + 1], Token)
            if not closed and not content:
                continue  # whitespace after the last "|"
            cells.append(PipeCell(content, span=child.span))
    return PipeRow(tuple(cells), span=node.span)


def _inlines(children: Sequence[CstNode | Token]) -> tuple[Inline, ...]:
    out: list[Inline] = []
    after_newline = False
    for child in children:
        if isinstance(child, Token):
            match child.kind:
                case K.TEXT | K.ERROR_TOKEN:
                    _append_text(out, child.text, child.span)
                case K.ESCAPE:
                    _append_text(out, child.text[1:], child.span)
                case K.WHITESPACE:
                    if not after_newline:
                        _append_text(out, child.text, child.span)
                case K.NEWLINE:
                    # Two breaks in a row only arise around a comment-only line.
                    if not (out and isinstance(out[-1], SoftBreak)):
                        out.append(SoftBreak(span=child.span))
                case K.NBSP:
                    out.append(NonBreakingSpace(span=child.span))
                case K.AUTOLINK:
                    out.append(Autolink(child.text[1:-1], span=child.span))
                case K.REF:
                    out.append(Ref(child.text[1:], span=child.span))
                case K.PARAM:
                    out.append(Param(child.text[1:], span=child.span))
                case K.COMMENT | K.LABEL:
                    pass
                case _:
                    raise AssertionError(f"unexpected inline token {child.kind}")
            after_newline = child.kind is K.NEWLINE
            continue
        after_newline = False
        out.append(_inline_node(child))
    return tuple(out)


def _append_text(out: list[Inline], text: str, span: Span) -> None:
    last = out[-1] if out else None
    if isinstance(last, Text) and last.span.end == span.start:
        out[-1] = Text(last.text + text, span=last.span.cover(span))
    else:
        out.append(Text(text, span=span))


def _inline_node(node: CstNode) -> Inline:
    match node.kind:
        case K.STRONG:
            return Strong(_inlines(node.children[1:-1]), span=node.span)
        case K.EMPH:
            return Emph(_inlines(node.children[1:-1]), span=node.span)
        case K.CODE_SPAN:
            verbatim = node.first_token(K.VERBATIM)
            return CodeSpan(verbatim.text if verbatim else "", span=node.span)
        case K.LINK:
            label, target = node.children
            assert isinstance(label, CstNode)
            assert isinstance(target, CstNode)
            url = target.first_token(K.VERBATIM)
            content = _inlines(label.children[1:-1])
            return Link(content, url.text.strip() if url else "", span=node.span)
        case K.COMMAND:
            name = node.children[0]
            assert isinstance(name, Token)
            options = node.first_node(K.OPTIONS)
            body = node.first_node(K.BODY) or node.first_node(K.VERBATIM_BODY)
            return Command(
                name.text[1:],
                _options(options) if options else None,
                _body(body, name.text[1:]) if body else None,
                span=node.span,
            )
        case K.DEFINE | K.LET:
            group = node.first_node(K.NAME_GROUP)
            name_token = group.first_token(K.BARE) if group else None
            name = name_token.text if name_token else ""
            body_node = node.first_node(K.BODY)
            body = MarkupBody(_body_blocks(body_node), span=body_node.span) if body_node else None
            if node.kind is K.LET:
                return Let(name, body, span=node.span)
            params = node.first_node(K.OPTIONS)
            return Define(name, _options(params) if params else None, body, span=node.span)
        case K.GROUP:
            return Group(_body_blocks(node), span=node.span)
        case _:
            raise AssertionError(f"unexpected inline node {node.kind}")


def _body(node: CstNode, command: str) -> Body:
    if node.kind is K.BODY:
        return MarkupBody(_body_blocks(node), span=node.span)
    kind = body_kind(command)
    verbatim = node.first_token(K.VERBATIM)
    text = verbatim.text if verbatim else ""
    if kind is ArgKind.STRING:
        text = _unescape(text, "}\\")
    unclosed = node.first_token(K.R_BRACE) is None
    return VerbatimBody(kind, text, unclosed, span=node.span)


def _body_blocks(node: CstNode) -> tuple[Block, ...]:
    return _blocks(node.children)


def _options(node: CstNode) -> Options:
    return Options(_option_items(node), span=node.span)


def _option_items(node: CstNode) -> tuple[Option, ...]:
    items: list[Option] = []
    for child in node.nodes(K.OPTION):
        parts = [c for c in child.children if not _is_option_trivia(c)]
        if any(isinstance(p, Token) and p.kind is K.ERROR_TOKEN for p in parts):
            continue  # already reported (e.g. nesting too deep)
        first = parts[0]
        if isinstance(first, Token) and first.kind is K.HASH_LABEL:
            items.append(Option("id", BareValue(first.text[1:], span=first.span), span=child.span))
            continue
        has_key = len(parts) >= 2 and isinstance(parts[1], Token) and parts[1].kind is K.EQUALS
        if has_key:
            assert isinstance(first, Token)
            if len(parts) < 3:
                continue  # "key=" without a value; already reported
            items.append(Option(first.text, _value(parts[2]), span=child.span))
        else:
            items.append(Option(None, _value(first), span=child.span))
    return tuple(items)


def _is_option_trivia(element: CstNode | Token) -> bool:
    return isinstance(element, Token) and element.kind in (K.WHITESPACE, K.NEWLINE, K.COMMENT)


def _value(element: CstNode | Token) -> Value:
    if isinstance(element, Token):
        match element.kind:
            case K.BARE:
                return BareValue(element.text, span=element.span)
            case K.STRING:
                text = element.text
                closed = len(text) >= 2 and text.endswith('"')
                inner = text[1:-1] if closed else text[1:]
                return StringValue(_unescape(inner, '"\\'), span=element.span)
            case K.PARAM:
                return ParamValue(element.text[1:], span=element.span)
            case _:
                raise AssertionError(f"unexpected value token {element.kind}")
    if element.kind is K.TUPLE:
        return TupleValue(_option_items(element), span=element.span)
    return MarkupBody(_body_blocks(element), span=element.span)


def _unescape(text: str, escapable: str) -> str:
    out: list[str] = []
    i = 0
    while i < len(text):
        if text[i] == "\\" and i + 1 < len(text) and text[i + 1] in escapable:
            out.append(text[i + 1])
            i += 2
        else:
            out.append(text[i])
            i += 1
    return "".join(out)


def _strip(content: tuple[Inline, ...]) -> tuple[Inline, ...]:
    """Trim leading/trailing whitespace text (headings, table cells)."""
    items = list(content)
    if items and isinstance(items[0], Text):
        stripped = items[0].text.lstrip(" \t")
        if stripped:
            items[0] = Text(stripped, span=items[0].span)
        else:
            items.pop(0)
    if items and isinstance(items[-1], Text):
        stripped = items[-1].text.rstrip(" \t")
        if stripped:
            items[-1] = Text(stripped, span=items[-1].span)
        else:
            items.pop()
    return tuple(items)


# --- debugging dump ------------------------------------------------------------------------


def dump(node: AstNode) -> str:
    """A compact s-expression, e.g. ``(paragraph (text "Hi ") (strong (text "you")))``."""
    name = _snake(type(node).__name__)
    parts: list[str] = [name]
    for f in fields(node):
        if f.name == "span":
            continue
        value = getattr(node, f.name)
        parts.extend(_dump_value(f.name, value))
    return "(" + " ".join(parts) + ")"


def _dump_value(name: str, value: object) -> list[str]:
    if value is None or value == ():
        return []
    if isinstance(value, AstNode):
        return [dump(value)]
    if isinstance(value, tuple):
        return [dump(v) for v in value if isinstance(v, AstNode)]  # pyright: ignore[reportUnknownVariableType]
    if isinstance(value, bool):
        return [f":{name}"] if value else []
    if isinstance(value, ArgKind):
        return [f":{value.value}"]
    if isinstance(value, int):
        return [str(value)]
    return [_quote(str(value))]


def _quote(text: str) -> str:
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'


def _snake(name: str) -> str:
    return "".join("_" + c.lower() if c.isupper() and i else c.lower() for i, c in enumerate(name))
