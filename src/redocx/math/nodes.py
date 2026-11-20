"""The math tree: what the LaTeX subset parses into and what the OMML writer reads.

Each node is one OMML construct (fraction, radical, script, n-ary operator, ...). A `Row`
is a sequence of nodes, the content of an OMML argument (m:e, m:num, m:sub, ...).
"""

from __future__ import annotations

import html
import re
from collections.abc import Iterator
from dataclasses import dataclass, field, fields, is_dataclass, replace
from typing import cast

Row = tuple["Node", ...]


@dataclass(frozen=True)
class Run:
    """Text: letters, digits, operators, symbols."""

    text: str
    style: str | None = None
    """m:sty: p (upright), b (bold), i (italic), bi; None lets Word choose (letters italic)."""
    script: str | None = None
    """m:scr: double-struck, script, fraktur, sans-serif, monospace."""
    normal: bool = False
    """Ordinary text (\\text{...}): the document's font, not math letters (m:nor)."""
    color: str | None = None
    """The text colour as RRGGBB (w:color); None is automatic."""
    shade: str | None = None
    """The background as RRGGBB (w:shd), from \\colorbox."""


@dataclass(frozen=True)
class Align:
    """An alignment point (&) in an equation array; the next run aligns."""


@dataclass(frozen=True)
class Frac:
    num: Row
    den: Row
    kind: str = "bar"
    """bar | noBar (binomials) | lin (a/b on one line)."""
    color: str | None = None
    """The colour of its own marks (bar, sign, brackets) as RRGGBB, from m:ctrlPr."""


@dataclass(frozen=True)
class Rad:
    e: Row
    degree: Row | None = None
    color: str | None = None
    """The colour of its own marks (bar, sign, brackets) as RRGGBB, from m:ctrlPr."""


@dataclass(frozen=True)
class Scripts:
    base: Row
    sub: Row | None = None
    sup: Row | None = None
    color: str | None = None
    """The colour of its own marks (bar, sign, brackets) as RRGGBB, from m:ctrlPr."""


@dataclass(frozen=True)
class Nary:
    """A big operator (sum, integral, ...) with its limits and operand."""

    chr: str
    sub: Row | None
    sup: Row | None
    e: Row
    lim_loc: str
    """undOvr (limits above and below) | subSup (beside)."""
    color: str | None = None
    """The colour of its own marks (bar, sign, brackets) as RRGGBB, from m:ctrlPr."""


@dataclass(frozen=True)
class Delim:
    beg: str
    end: str
    e: Row
    color: str | None = None
    """The colour of its own marks (bar, sign, brackets) as RRGGBB, from m:ctrlPr."""


@dataclass(frozen=True)
class Func:
    """A function name (upright) applied to an argument: sin x, lim_{x→0} f."""

    name: Row
    e: Row
    color: str | None = None
    """The colour of its own marks (bar, sign, brackets) as RRGGBB, from m:ctrlPr."""


@dataclass(frozen=True)
class Limit:
    """Something below (low) or above (upp) a base: lim with its limit, \\overset."""

    kind: str
    e: Row
    lim: Row
    color: str | None = None
    """The colour of its own marks (bar, sign, brackets) as RRGGBB, from m:ctrlPr."""


@dataclass(frozen=True)
class Accent:
    chr: str
    e: Row
    color: str | None = None
    """The colour of its own marks (bar, sign, brackets) as RRGGBB, from m:ctrlPr."""


@dataclass(frozen=True)
class Bar:
    pos: str
    """top (\\overline) | bot (\\underline)."""
    e: Row
    color: str | None = None
    """The colour of its own marks (bar, sign, brackets) as RRGGBB, from m:ctrlPr."""


@dataclass(frozen=True)
class GroupChr:
    """A brace over or under an expression (\\overbrace, \\underbrace)."""

    chr: str
    pos: str
    e: Row
    color: str | None = None
    """The colour of its own marks (bar, sign, brackets) as RRGGBB, from m:ctrlPr."""


@dataclass(frozen=True)
class Matrix:
    rows: tuple[tuple[Row, ...], ...]
    align: str = "center"
    """Column alignment: center | left (cases)."""
    color: str | None = None
    """The colour of its own marks (bar, sign, brackets) as RRGGBB, from m:ctrlPr."""


@dataclass(frozen=True)
class EqArr:
    """Rows of a multi-line equation (aligned, gathered); `Align` marks alignment points."""

    rows: tuple[Row, ...]
    color: str | None = None
    """The colour of its own marks (bar, sign, brackets) as RRGGBB, from m:ctrlPr."""


BOX_SIDES = ("top", "bot", "left", "right")
BOX_STRIKES = ("h", "v", "bltr", "tlbr")


@dataclass(frozen=True)
class BorderBox:
    """A box around an expression (m:borderBox): \\boxed, or with its sides hidden and a
    diagonal struck through it, \\cancel."""

    e: Row
    hide: tuple[str, ...] = ()
    """Sides left out, in BOX_SIDES order."""
    strike: tuple[str, ...] = ()
    """Lines through it, in BOX_STRIKES order: h, v, bltr (bottom left to top right), tlbr."""
    color: str | None = None
    """The colour of its own marks (bar, sign, brackets) as RRGGBB, from m:ctrlPr."""


@dataclass(frozen=True)
class Phant:
    """An expression that takes up space it may not show (m:phant): \\phantom, \\smash."""

    e: Row
    show: bool = True
    zero_width: bool = False
    zero_ascent: bool = False
    zero_descent: bool = False
    color: str | None = None
    """The colour of its own marks (bar, sign, brackets) as RRGGBB, from m:ctrlPr."""


@dataclass(frozen=True)
class Raw:
    """OMML written as XML (`\\ooxml{...}` in a formula), kept as it is: m: elements such as
    m:sPre or m:box that the LaTeX subset has no form for."""

    xml: str
    start: int = field(default=0, compare=False, repr=False)
    """Offsets of the command in the formula, for messages."""
    end: int = field(default=0, compare=False, repr=False)


Node = (
    Run
    | Align
    | Frac
    | Rad
    | Scripts
    | Nary
    | Delim
    | Func
    | Limit
    | Accent
    | Bar
    | GroupChr
    | Matrix
    | EqArr
    | BorderBox
    | Phant
    | Raw
)


def same_format(a: Run, b: Run) -> bool:
    """Whether two runs differ only in their text (and can be one run)."""
    return replace(a, text="") == replace(b, text="")


def colored(row: Row, color: str) -> Row:
    """The row in `color`: every run and object in it, at any depth, that has no colour of
    its own (\\textcolor, \\color)."""
    return tuple(_colored(node, color) for node in row)


def _colored(node: Node, color: str) -> Node:
    if isinstance(node, Align | Raw):
        return node
    changes: dict[str, object] = {
        f.name: _colored_value(getattr(node, f.name), color)
        for f in fields(node)
        if f.name != "color"
    }
    if node.color is None:
        changes["color"] = color
    return replace(node, **changes)


def _colored_value(value: object, color: str) -> object:
    if not isinstance(value, tuple):
        return value
    items = cast("tuple[object, ...]", value)
    return tuple(
        _colored(cast("Node", item), color) if is_dataclass(item) else _colored_value(item, color)
        for item in items
    )


def uncolored(row: Row, color: str) -> Row:
    """The row without `color` where it has it, at any depth: the inverse of `colored`."""
    return tuple(_uncolored(node, color) for node in row)


def _uncolored(node: Node, color: str) -> Node:
    if isinstance(node, Align | Raw):
        return node
    changes: dict[str, object] = {
        f.name: _uncolored_value(getattr(node, f.name), color)
        for f in fields(node)
        if f.name != "color"
    }
    if node.color == color:
        changes["color"] = None
    return replace(node, **changes)


def _uncolored_value(value: object, color: str) -> object:
    if not isinstance(value, tuple):
        return value
    items = cast("tuple[object, ...]", value)
    return tuple(
        _uncolored(cast("Node", item), color)
        if is_dataclass(item)
        else _uncolored_value(item, color)
        for item in items
    )


def walk(row: Row) -> Iterator[Node]:
    """Every node of a formula, outer ones first."""
    for node in row:
        yield node
        for f in fields(node):
            yield from _walk_value(getattr(node, f.name))


def _walk_value(value: object) -> Iterator[Node]:
    """The nodes in a field: a node, a row, or rows of rows (a matrix)."""
    if isinstance(value, tuple):
        for item in cast("tuple[object, ...]", value):
            if is_dataclass(item):
                yield from walk((cast("Node", item),))
            else:
                yield from _walk_value(item)


_TEXT_RE = re.compile(r"<m:t(?:\s[^>]*)?>([^<]*)</m:t>")


def canonical(row: Row) -> Row:
    """The formula in the form Word stores it, for comparing two formulas: adjacent runs of
    one kind merged into one, empty runs left out, alignment marks only before something,
    `-` as the minus sign U+2212, and an empty limit of a big operator left out."""
    out: list[Node] = []
    for node in row:
        node = _canonical_node(node)
        if isinstance(node, Run) and not node.text:
            continue
        last = out[-1] if out else None
        if isinstance(node, Align) and isinstance(last, Align):
            continue
        if isinstance(node, Run) and isinstance(last, Run) and same_format(last, node):
            out[-1] = replace(last, text=last.text + node.text)
            continue
        out.append(node)
    if out and isinstance(out[-1], Align):
        out.pop()
    return tuple(out)


def _canonical_node(node: Node) -> Node:
    if isinstance(node, Run):
        return node if node.normal else replace(node, text=node.text.replace("-", "−"))
    if isinstance(node, Raw | Align):
        return node
    changes = {f.name: _canonical_value(getattr(node, f.name)) for f in fields(node)}
    if isinstance(node, Nary):
        changes["sub"] = changes["sub"] or None
        changes["sup"] = changes["sup"] or None
    return replace(node, **changes)


def _canonical_value(value: object) -> object:
    """A row canonical; rows of rows (an array, a matrix) each canonical."""
    if not isinstance(value, tuple):
        return value
    items = cast("tuple[object, ...]", value)
    if all(is_dataclass(item) for item in items):
        return canonical(cast("Row", items))
    return tuple(_canonical_value(item) for item in items)


def linear_text(row: Row) -> str:
    """A rough plain-text rendering (for messages, alt text and tests)."""
    out: list[str] = []
    for node in row:
        match node:
            case Run(text=text):
                out.append(text)
            case Frac(num=num, den=den):
                out.append(f"({linear_text(num)})/({linear_text(den)})")
            case Rad(e=e, degree=degree):
                out.append(
                    f"√({linear_text(e)})"
                    if degree is None
                    else f"√[{linear_text(degree)}]({linear_text(e)})"
                )
            case Scripts(base=base, sub=sub, sup=sup):
                out.append(linear_text(base))
                if sub:
                    out.append(f"_({linear_text(sub)})")
                if sup:
                    out.append(f"^({linear_text(sup)})")
            case Nary(chr=c, sub=sub, sup=sup, e=e):
                out.append(
                    c
                    + (f"_({linear_text(sub)})" if sub else "")
                    + (f"^({linear_text(sup)})" if sup else "")
                )
                out.append(linear_text(e))
            case Delim(beg=beg, end=end, e=e):
                out.append(f"{beg}{linear_text(e)}{end}")
            case Func(name=name, e=e):
                out.append(f"{linear_text(name)} {linear_text(e)}")
            case Limit(e=e, lim=lim):
                out.append(f"{linear_text(e)}_({linear_text(lim)})")
            case Accent(e=e) | Bar(e=e) | GroupChr(e=e) | BorderBox(e=e):
                out.append(linear_text(e))
            case Matrix(rows=rows):
                out.append(
                    "[" + "; ".join(", ".join(linear_text(c) for c in r) for r in rows) + "]"
                )
            case EqArr(rows=rows):
                out.append(" \\\\ ".join(linear_text(r) for r in rows))
            case Phant(e=e, show=show):
                if show:
                    out.append(linear_text(e))
            case Align():
                pass
            case Raw(xml=xml):
                out.append("".join(html.unescape(t) for t in _TEXT_RE.findall(xml)))
    return "".join(out)
