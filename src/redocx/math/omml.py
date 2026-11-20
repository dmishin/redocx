"""Write the math tree as Office Math (OMML), the way Word writes it.

Word's reference output (the M3 survey) shapes the details: every math run carries the
Cambria Math font in w:rPr, every object's properties end with an m:ctrlPr, function names
are upright (m:sty p), and a numbered display equation is an m:eqArr whose row ends with
Word's own `#` and the number in a delimiter.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import replace

from redocx.math.nodes import (
    Accent,
    Align,
    Bar,
    BorderBox,
    Delim,
    EqArr,
    Frac,
    Func,
    GroupChr,
    Limit,
    Matrix,
    Nary,
    Node,
    Phant,
    Rad,
    Raw,
    Row,
    Run,
    Scripts,
    SPre,
    same_format,
)
from redocx.ooxml.xml import E, Element, parse_fragment, qn

MATH_FONT = "Cambria Math"
SHADING_GUARD = "\N{ZERO WIDTH SPACE}"
"""The run in front of an equation that starts with a shaded run."""
DEFAULT_ACCENT = "̂"
_STRIKES = {"h": "H", "v": "V", "bltr": "BLTR", "tlbr": "TLBR"}


def _math_rpr(italic: bool = False) -> Element:
    return E(
        "w:rPr",
        E("w:rFonts", {"w:ascii": MATH_FONT, "w:hAnsi": MATH_FONT}),
        E("w:i") if italic else None,
    )


def _colors(color: str | None, shade: str | None) -> list[Element]:
    """w:color and w:shd for a run's w:rPr, in schema order (after w:b and w:i)."""
    out: list[Element] = []
    if color is not None:
        out.append(E("w:color", {"w:val": color}))
    if shade is not None:
        out.append(E("w:shd", {"w:val": "clear", "w:color": "auto", "w:fill": shade}))
    return out


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
        # Word marks an alignment point in an equation array with `&` in the math text (it
        # ignores m:aln there), so an Align is written as & at the start of the next run.
        out: list[Element] = []
        align = False
        for node in _merge_runs(row):
            if isinstance(node, Align):
                if align:
                    out.append(self.run(Run("&")))
                align = True
                continue
            if align and isinstance(node, Run) and not node.normal:
                out.append(self.run(replace(node, text="&" + node.text)))
                align = False
                continue
            if align:
                out.append(self.run(Run("&")))
            align = False
            if isinstance(node, Raw):  # checked against the schemas when it was lowered
                out.extend(parse_fragment(node.xml, math=True))
                continue
            out.append(self.node(node))
        return out

    def arg(self, tag: str, row: Row | None) -> Element:
        return E(tag, *self.row(row or ()))

    def run(self, run: Run) -> Element:
        props: list[Element] = []
        if run.normal:
            props.append(E("m:nor"))
        else:
            if run.script:
                props.append(_val("m:scr", run.script))
            if run.style:
                props.append(_val("m:sty", run.style))
        text_props = (
            E(
                "w:rPr",
                E("w:b") if run.style in ("b", "bi") else None,
                E("w:i") if run.style in ("i", "bi") else None,
            )
            if run.normal
            else _math_rpr()
        )
        text_props.extend(_colors(run.color, run.shade))
        if not len(text_props):
            text_props = None
        space = {"xml:space": "preserve"} if run.text != run.text.strip() or not run.text else None
        return E(
            "m:r", E("m:rPr", *props) if props else None, text_props, E("m:t", space, text=run.text)
        )

    def node(self, node: Node) -> Element:
        element = self.object(node)
        if not isinstance(node, Run | Align | Raw) and node.color is not None:
            # The colour of the object's own marks (fraction bar, radical sign, brackets)
            # is the colour of its control properties, as Word writes it.
            ctrl = element[0].find(qn("m:ctrlPr"))
            if ctrl is not None:
                ctrl[0].extend(_colors(node.color, None))
        return element

    def object(self, node: Node) -> Element:
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
                # An empty script (x^{}) is an empty argument, as Word writes a placeholder;
                # the element needs it to be valid.
                kind = (
                    "sSubSup"
                    if sub is not None and sup is not None
                    else "sSub"
                    if sub is not None
                    else "sSup"
                )
                parts = [self.arg("m:e", base)]
                if kind != "sSup":
                    parts.append(self.arg("m:sub", sub))
                if kind != "sSub":
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
            case Delim(beg=beg, end=end, e=e, sep=sep, rest=rest):
                props = E(
                    "m:dPr",
                    _val("m:begChr", beg) if beg != "(" else None,
                    _val("m:sepChr", sep) if rest else None,
                    _val("m:endChr", end) if end != ")" else None,
                    _ctrl(),
                )
                return E("m:d", props, *(self.arg("m:e", part) for part in (e, *rest)))
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
            case GroupChr(chr=c, pos=pos, e=e, vert=vert):
                vert = vert or ("bot" if pos == "top" else "")
                props = E(
                    "m:groupChrPr",
                    _val("m:chr", c),
                    _val("m:pos", pos) if pos != "bot" else None,
                    _val("m:vertJc", vert) if vert else None,
                    _ctrl(),
                )
                return E("m:groupChr", props, self.arg("m:e", e))
            case Matrix(rows=rows, align=align):
                # A matrix has at least one row of one cell, or it is not valid OMML.
                columns = max((len(r) for r in rows), default=0) or 1
                rows = tuple(tuple(r) + ((),) * (columns - len(r)) for r in rows or ((),))
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
                return E(
                    "m:eqArr",
                    E("m:eqArrPr", _ctrl()),
                    *(self.arg("m:e", r) for r in rows or ((),)),
                )
            case BorderBox(e=e, hide=hide, strike=strike):
                props = E(
                    "m:borderBoxPr",
                    *(_val(f"m:hide{side.capitalize()}", "1") for side in hide),
                    *(_val(f"m:strike{_STRIKES[line]}", "1") for line in strike),
                    _ctrl(),
                )
                return E("m:borderBox", props, self.arg("m:e", e))
            case SPre(sub=sub, sup=sup, e=e):
                return E(
                    "m:sPre",
                    E("m:sPrePr", _ctrl()),
                    self.arg("m:sub", sub),
                    self.arg("m:sup", sup),
                    self.arg("m:e", e),
                )
            case Phant(e=e, show=show, zero_width=wid, zero_ascent=asc, zero_descent=desc):
                props = E(
                    "m:phantPr",
                    _val("m:show", "0") if not show else None,
                    _val("m:zeroWid", "1") if wid else None,
                    _val("m:zeroAsc", "1") if asc else None,
                    _val("m:zeroDesc", "1") if desc else None,
                    _ctrl(),
                )
                return E("m:phant", props, self.arg("m:e", e))
            case Align():
                return self.run(Run("&"))
            case Raw():
                raise TypeError("raw OMML is written by `row`")
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
        if isinstance(node, Run) and pending is not None and same_format(pending, node):
            pending = replace(pending, text=pending.text + node.text)
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
    first = content[0] if content else None
    if first is not None and first.find(f"{qn('w:rPr')}/{qn('w:shd')}") is not None:
        # Word shades the whole equation when its first run is shaded (\colorbox at the
        # start); an invisible run in front keeps the shading to that run.
        content.insert(0, writer.run(Run(SHADING_GUARD)))
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
