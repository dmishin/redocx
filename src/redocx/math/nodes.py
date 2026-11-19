"""The math tree: what the LaTeX subset parses into and what the OMML writer reads.

Each node is one OMML construct (fraction, radical, script, n-ary operator, ...). A `Row`
is a sequence of nodes, the content of an OMML argument (m:e, m:num, m:sub, ...).
"""

from __future__ import annotations

import html
import re
from collections.abc import Iterator
from dataclasses import dataclass, field, fields, is_dataclass
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


@dataclass(frozen=True)
class Align:
    """An alignment point (&) in an equation array; the next run aligns."""


@dataclass(frozen=True)
class Frac:
    num: Row
    den: Row
    kind: str = "bar"
    """bar | noBar (binomials) | lin (a/b on one line)."""


@dataclass(frozen=True)
class Rad:
    e: Row
    degree: Row | None = None


@dataclass(frozen=True)
class Scripts:
    base: Row
    sub: Row | None = None
    sup: Row | None = None


@dataclass(frozen=True)
class Nary:
    """A big operator (sum, integral, ...) with its limits and operand."""

    chr: str
    sub: Row | None
    sup: Row | None
    e: Row
    lim_loc: str
    """undOvr (limits above and below) | subSup (beside)."""


@dataclass(frozen=True)
class Delim:
    beg: str
    end: str
    e: Row


@dataclass(frozen=True)
class Func:
    """A function name (upright) applied to an argument: sin x, lim_{x→0} f."""

    name: Row
    e: Row


@dataclass(frozen=True)
class Limit:
    """Something below (low) or above (upp) a base: lim with its limit, \\overset."""

    kind: str
    e: Row
    lim: Row


@dataclass(frozen=True)
class Accent:
    chr: str
    e: Row


@dataclass(frozen=True)
class Bar:
    pos: str
    """top (\\overline) | bot (\\underline)."""
    e: Row


@dataclass(frozen=True)
class GroupChr:
    """A brace over or under an expression (\\overbrace, \\underbrace)."""

    chr: str
    pos: str
    e: Row


@dataclass(frozen=True)
class Matrix:
    rows: tuple[tuple[Row, ...], ...]
    align: str = "center"
    """Column alignment: center | left (cases)."""


@dataclass(frozen=True)
class EqArr:
    """Rows of a multi-line equation (aligned, gathered); `Align` marks alignment points."""

    rows: tuple[Row, ...]


@dataclass(frozen=True)
class Raw:
    """OMML written as XML (`\\ooxml{...}` in a formula), kept as it is: m: elements such as
    m:sPre or m:box that the LaTeX subset has no form for."""

    xml: str
    start: int = field(default=0, compare=False)
    """Offsets of the command in the formula, for messages."""
    end: int = field(default=0, compare=False)


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
    | Raw
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
            case Accent(e=e) | Bar(e=e) | GroupChr(e=e):
                out.append(linear_text(e))
            case Matrix(rows=rows):
                out.append(
                    "[" + "; ".join(", ".join(linear_text(c) for c in r) for r in rows) + "]"
                )
            case EqArr(rows=rows):
                out.append(" \\\\ ".join(linear_text(r) for r in rows))
            case Align():
                pass
            case Raw(xml=xml):
                out.append("".join(html.unescape(t) for t in _TEXT_RE.findall(xml)))
    return "".join(out)
