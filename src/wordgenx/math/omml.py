"""Write the math tree as Office Math (OMML), the way Word writes it.

Word's reference output (the M3 survey) shapes the details: every math run carries the
Cambria Math font in w:rPr, every object's properties end with an m:ctrlPr, function names
are upright (m:sty p), and a numbered display equation is an m:eqArr whose row ends with
Word's own `#` and the number in a delimiter.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable

from wordgenx.math.nodes import (
    Accent,
    Align,
    Bar,
    Delim,
    EqArr,
    Frac,
    Func,
    GroupChr,
    Limit,
    Matrix,
    Nary,
    Node,
    Rad,
    Row,
    Run,
    Scripts,
)
from wordgenx.ooxml.xml import E, Element

MATH_FONT = "Cambria Math"
DEFAULT_ACCENT = "̂"


def _math_rpr(italic: bool = False) -> Element:
    return E(
        "w:rPr",
        E("w:rFonts", {"w:ascii": MATH_FONT, "w:hAnsi": MATH_FONT}),
        E("w:i") if italic else None,
    )


def _ctrl() -> Element:
    return E("m:ctrlPr", _math_rpr(italic=True))


def _val(tag: str, value: str) -> Element:
    return E(tag, {"m:val": value})


def _no_bookmarks(name: str) -> int:
    return 0


class _Writer:
    def __init__(self, bookmark: Callable[[str], int] | None = None) -> None:
        self.bookmark_id: Callable[[str], int] = bookmark or _no_bookmarks

    def row(self, row: Row) -> list[Element]:
        out: list[Element] = []
        align = False
        for node in _merge_runs(row):
            if isinstance(node, Align):
                align = True
                continue
            if align and not isinstance(node, Run):
                out.append(self.run(Run(""), align=True))
            elif align:
                out.append(self.run(node, align=True))  # type: ignore[arg-type]
                align = False
                continue
            align = False
            out.append(self.node(node))
        return out

    def arg(self, tag: str, row: Row | None) -> Element:
        return E(tag, *self.row(row or ()))

    def run(self, run: Run, align: bool = False) -> Element:
        props: list[Element] = []
        if run.normal:
            props.append(E("m:nor"))
        else:
            if run.script:
                props.append(_val("m:scr", run.script))
            if run.style:
                props.append(_val("m:sty", run.style))
        if align:
            props.append(E("m:aln"))
        text_props = (
            E(
                "w:rPr",
                E("w:b") if run.style == "b" else None,
                E("w:i") if run.style == "i" else None,
            )
            if run.normal
            else _math_rpr()
        )
        if not len(text_props):
            text_props = None
        space = {"xml:space": "preserve"} if run.text != run.text.strip() or not run.text else None
        return E(
            "m:r", E("m:rPr", *props) if props else None, text_props, E("m:t", space, text=run.text)
        )

    def node(self, node: Node) -> Element:
        match node:
            case Run():
                return self.run(node)
            case Frac(num=num, den=den, kind=kind):
                kind_el = _val("m:type", kind) if kind != "bar" else None
                return E(
                    "m:f",
                    E("m:fPr", kind_el, _ctrl()),
                    self.arg("m:num", num),
                    self.arg("m:den", den),
                )
            case Rad(e=e, degree=degree):
                hide = _val("m:degHide", "1") if degree is None else None
                return E(
                    "m:rad",
                    E("m:radPr", hide, _ctrl()),
                    self.arg("m:deg", degree),
                    self.arg("m:e", e),
                )
            case Scripts(base=base, sub=sub, sup=sup):
                kind = "sSubSup" if sub and sup else "sSub" if sub else "sSup"
                parts = [self.arg("m:e", base)]
                if sub:
                    parts.append(self.arg("m:sub", sub))
                if sup:
                    parts.append(self.arg("m:sup", sup))
                return E(f"m:{kind}", E(f"m:{kind}Pr", _ctrl()), *parts)
            case Nary(chr=c, sub=sub, sup=sup, e=e, lim_loc=loc):
                props = E(
                    "m:naryPr",
                    _val("m:chr", c) if c != "∫" else None,
                    _val("m:limLoc", loc),
                    _val("m:subHide", "1") if not sub else None,
                    _val("m:supHide", "1") if not sup else None,
                    _ctrl(),
                )
                return E(
                    "m:nary",
                    props,
                    self.arg("m:sub", sub),
                    self.arg("m:sup", sup),
                    self.arg("m:e", e),
                )
            case Delim(beg=beg, end=end, e=e):
                props = E(
                    "m:dPr",
                    _val("m:begChr", beg) if beg != "(" else None,
                    _val("m:endChr", end) if end != ")" else None,
                    _ctrl(),
                )
                return E("m:d", props, self.arg("m:e", e))
            case Func(name=name, e=e):
                return E(
                    "m:func", E("m:funcPr", _ctrl()), self.arg("m:fName", name), self.arg("m:e", e)
                )
            case Limit(kind=kind, e=e, lim=lim):
                tag = "m:limLow" if kind == "low" else "m:limUpp"
                return E(tag, E(f"{tag}Pr", _ctrl()), self.arg("m:e", e), self.arg("m:lim", lim))
            case Accent(chr=c, e=e):
                return E(
                    "m:acc",
                    E("m:accPr", _val("m:chr", c) if c != DEFAULT_ACCENT else None, _ctrl()),
                    self.arg("m:e", e),
                )
            case Bar(pos=pos, e=e):
                return E(
                    "m:bar",
                    E("m:barPr", _val("m:pos", pos) if pos != "bot" else None, _ctrl()),
                    self.arg("m:e", e),
                )
            case GroupChr(chr=c, pos=pos, e=e):
                props = E(
                    "m:groupChrPr",
                    _val("m:chr", c),
                    _val("m:pos", pos) if pos != "bot" else None,
                    _val("m:vertJc", "bot") if pos == "top" else None,
                    _ctrl(),
                )
                return E("m:groupChr", props, self.arg("m:e", e))
            case Matrix(rows=rows, align=align):
                columns = max((len(r) for r in rows), default=1)
                mcs = E(
                    "m:mcs",
                    E("m:mc", E("m:mcPr", _val("m:count", str(columns)), _val("m:mcJc", align))),
                )
                return E(
                    "m:m",
                    E("m:mPr", mcs, _ctrl()),
                    *(E("m:mr", *(self.arg("m:e", cell) for cell in r)) for r in rows),
                )
            case EqArr(rows=rows):
                return E("m:eqArr", E("m:eqArrPr", _ctrl()), *(self.arg("m:e", r) for r in rows))
            case Align():
                return self.run(Run(""), align=True)
        raise TypeError(f"unknown math node {node!r}")

    def numbered(self, row: Row, number: str, bookmark: str | None) -> Element:
        """Word's numbered display equation: an array whose (last) row ends with # (n)."""
        rows = list(row[0].rows) if len(row) == 1 and isinstance(row[0], EqArr) else [row]
        start: list[Element] = []
        end: list[Element] = []
        if bookmark is not None:
            ident = str(self.bookmark_id(bookmark))
            start = [E("w:bookmarkStart", {"w:id": ident, "w:name": bookmark})]
            end = [E("w:bookmarkEnd", {"w:id": ident})]
        # The bookmark holds the number alone, so a REF to it (which adds its own
        # parentheses) shows "(2.1)", not "((2.1))". A separator other than "." is text:
        # as math, an en dash would be spaced like a minus sign.
        text = Run(number, normal=any(c not in "0123456789." for c in number))
        delim = self.node(Delim("(", ")", ()))
        delim[-1].extend([*start, self.run(text), *end])
        tail = [self.run(Run("#")), delim]
        elements = [E("m:e", *self.row(r)) for r in rows]
        elements[-1].extend(tail)
        return E("m:eqArr", E("m:eqArrPr", _val("m:maxDist", "1"), _ctrl()), *elements)


def _merge_runs(row: Row) -> Iterable[Node]:
    """Adjacent runs of the same kind as one m:r, as Word writes them."""
    pending: Run | None = None
    for node in row:
        if (
            isinstance(node, Run)
            and pending is not None
            and (pending.style, pending.script, pending.normal)
            == (node.style, node.script, node.normal)
        ):
            pending = Run(pending.text + node.text, node.style, node.script, node.normal)
            continue
        if pending is not None:
            yield pending
            pending = None
        if isinstance(node, Run):
            pending = node
        else:
            yield node
    if pending is not None:
        yield pending


def omml(
    row: Row,
    *,
    display: bool,
    number: str | None = None,
    bookmark: str | None = None,
    bookmark_id: Callable[[str], int] | None = None,
) -> Element:
    """m:oMath (inline) or m:oMathPara (display) for a formula; a display equation with a
    `number` gets Word's # numbering, and `bookmark` around the number."""
    writer = _Writer(bookmark_id)
    content = [writer.numbered(row, number, bookmark)] if number is not None else writer.row(row)
    math = E("m:oMath", *content)
    return E("m:oMathPara", math) if display else math


MATH_PROPERTIES = (
    ("mathFont", MATH_FONT),
    ("brkBin", "before"),
    ("brkBinSub", "--"),
    ("smallFrac", "0"),
    ("dispDef", None),
    ("lMargin", "0"),
    ("rMargin", "0"),
    ("defJc", "centerGroup"),
    ("wrapIndent", "1440"),
    ("intLim", "subSup"),
    ("naryLim", "undOvr"),
)
"""m:mathPr in settings.xml, as Word writes it for a new document."""


def math_properties() -> Element:
    return E(
        "m:mathPr",
        *(
            E(f"m:{name}", {"m:val": value}) if value else E(f"m:{name}")
            for name, value in MATH_PROPERTIES
        ),
    )


__all__ = ["math_properties", "omml"]
