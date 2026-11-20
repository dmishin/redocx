"""Read Office Math (OMML) back into the math tree: the inverse of `redocx.math.omml`.

An object is read as structure only when everything it carries is something the tree
models, or what Word writes by default (the Cambria Math font, m:ctrlPr). Anything else (an
element the tree has no node for, such as m:sPre or m:box, a property such as m:argPr, a
coloured run) is kept as a `Raw` node holding that object's XML, so nothing is lost; the
smallest enclosing object is kept, not the whole formula.
"""

from __future__ import annotations

import copy
import re
from dataclasses import replace

from lxml import etree

from redocx.math.nodes import (
    BOX_SIDES,
    BOX_STRIKES,
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
)
from redocx.math.omml import DEFAULT_ACCENT, MATH_FONT, SHADING_GUARD
from redocx.ooxml.xml import RAW_NAMESPACES, Element

M = RAW_NAMESPACES["m"]
W = RAW_NAMESPACES["w"]
DEFAULT_GROUP_CHR = "⏟"
"""m:groupChr's character when m:chr is left out (a bottom curly bracket)."""
INTEGRALS = frozenset("∫∬∭∮∯∰∱∲∳")
"""Big operators whose limits sit beside them by default (m:intLim, as redocx sets it)."""
_ON = frozenset({"1", "on", "true", ""})
_RUN_TOGGLES = frozenset({"b", "i", "bCs", "iCs"})
_RUN_IGNORED = frozenset({"lang", "noProof", "rtl", "cs"})
"""Run properties that do not change how a math run looks."""


class _UnsupportedError(Exception):
    """The object holds something the math tree does not model."""


def _m(local: str) -> str:
    return f"{{{M}}}{local}"


def _local(element: Element) -> str:
    return etree.QName(element).localname


class MathReader:
    """Reads m:oMath elements; `kept` counts the objects kept as XML, by element name."""

    def __init__(self) -> None:
        self.kept: dict[str, int] = {}

    def equation(self, omath: Element) -> Row | None:
        """The formula of an m:oMath, or None when its top level cannot be read (then the
        whole equation is kept as XML by the caller)."""
        try:
            row = self.row(omath)
        except _UnsupportedError:
            return None
        # the writer's guard in front of a shaded first run (omml.SHADING_GUARD)
        guard = Run(SHADING_GUARD)
        if len(row) > 1 and row[0] == guard and isinstance(row[1], Run) and row[1].shade:
            return row[1:]
        return row

    # --- rows and runs ----------------------------------------------------------------------

    def row(self, container: Element) -> Row:
        out: list[Node] = []
        for child in container:
            if not isinstance(child.tag, str):
                continue
            namespace = etree.QName(child).namespace
            local = _local(child)
            if namespace == W and local in ("proofErr", "permStart", "permEnd"):
                continue
            if namespace != M or local in ("argPr",):
                raise _UnsupportedError(local)  # w: content in math (bookmarks, revisions)
            if local == "ctrlPr":
                continue
            if local == "r":
                try:
                    out.extend(self.run(child))
                except _UnsupportedError:
                    out.append(self.keep(child))
                continue
            reader = _OBJECTS.get(local)
            if reader is None:
                out.append(self.keep(child))
                continue
            try:
                node = reader(self, child)
                color = _ctrl_color(child)
            except _UnsupportedError:
                out.append(self.keep(child))
                continue
            out.append(replace(node, color=color) if color is not None else node)
        return tuple(out)

    def keep(self, element: Element) -> Raw:
        name = f"m:{_local(element)}"
        self.kept[name] = self.kept.get(name, 0) + 1
        return Raw(raw_xml(element))

    def run(self, run: Element) -> list[Node]:
        style = script = None
        normal = align = False
        bold = italic = False
        color = shade = None
        text: list[str] = []
        for child in run:
            local = _local(child)
            namespace = etree.QName(child).namespace
            if namespace == M and local == "rPr":
                for prop in child:
                    name, value = _local(prop), prop.get(_m("val"), "")
                    if name == "sty":
                        style = value
                    elif name == "scr":
                        script = value
                    elif name == "nor":
                        normal = value in _ON
                    elif name == "aln":
                        align = value in _ON
                    else:
                        raise _UnsupportedError(name)  # m:lit, m:brk
            elif namespace == W and local == "rPr":
                for prop in child:
                    name = _local(prop)
                    if name == "rFonts":
                        fonts = {prop.get(f"{{{W}}}{a}") for a in ("ascii", "hAnsi")}
                        if fonts - {None, MATH_FONT}:
                            raise _UnsupportedError("font")
                    elif name in _RUN_TOGGLES:
                        on = prop.get(f"{{{W}}}val", "") in _ON
                        bold = bold or (on and name in ("b", "bCs"))
                        italic = italic or (on and name in ("i", "iCs"))
                    elif name == "color":
                        color = _color(prop.get(f"{{{W}}}val"))
                    elif name == "shd":
                        shade = _shade(prop)
                    elif name not in _RUN_IGNORED:
                        raise _UnsupportedError(name)  # size, highlight, ...
            elif namespace == M and local == "t":
                text.append(child.text or "")
            elif namespace == W and local == "proofErr":
                continue
            else:
                raise _UnsupportedError(local)  # w:br, w:tab, ... in a math run
        joined = "".join(text)
        if normal:
            style = {(True, True): "bi", (True, False): "b", (False, True): "i"}.get((bold, italic))
        nodes: list[Node] = [Align()] if align else []
        if joined or not align:
            nodes.append(Run(joined, style, script, normal, color, shade))
        return nodes

    # --- arguments and properties -----------------------------------------------------------

    def arg(self, element: Element, name: str) -> Row | None:
        """The row of the named argument (m:e, m:num, ...), or None when it is missing."""
        found = element.find(_m(name))
        return None if found is None else self.row(found)

    def args(self, element: Element, name: str) -> Row:
        return self.arg(element, name) or ()


def _color(value: str | None) -> str | None:
    """A w:color value as RRGGBB, None for automatic; raises for a theme colour."""
    if value is None or value == "auto":
        return None
    if not re.fullmatch(r"[0-9A-Fa-f]{6}", value):
        raise _UnsupportedError("color")
    return value.upper()


def _shade(shd: Element) -> str | None:
    """A plain w:shd background as RRGGBB; raises for patterns and theme colours."""
    if shd.get(f"{{{W}}}val") not in ("clear", None) or any(
        name.startswith("{") and "theme" in name for name in shd.attrib
    ):
        raise _UnsupportedError("shd")
    return _color(shd.get(f"{{{W}}}fill"))


def _ctrl_color(element: Element) -> str | None:
    """The colour of an object's own marks: w:color in its m:ctrlPr."""
    found = element.find(f"{_m(_local(element) + 'Pr')}/{_m('ctrlPr')}/{{{W}}}rPr/{{{W}}}color")
    return None if found is None else _color(found.get(f"{{{W}}}val"))


def _props(element: Element, allowed: frozenset[str]) -> dict[str, str]:
    """The object's properties (m:fPr, m:naryPr, ...) by name, with their m:val; raises
    when one is not in `allowed`. m:ctrlPr carries no meaning of its own and is skipped."""
    found: dict[str, str] = {}
    pr = element.find(_m(f"{_local(element)}Pr"))
    if pr is None:
        return found
    for prop in pr:
        if not isinstance(prop.tag, str):
            continue
        name = _local(prop)
        if name == "ctrlPr":
            continue
        if name not in allowed:
            raise _UnsupportedError(name)
        found[name] = prop.get(_m("val"), "")
    return found


def _only(element: Element, *names: str) -> None:
    """Raises when the object holds an argument other than `names` (and its properties)."""
    allowed = {_m(f"{_local(element)}Pr"), *(_m(n) for n in names)}
    for child in element:
        if isinstance(child.tag, str) and child.tag not in allowed:
            raise _UnsupportedError(_local(child))


def _frac(r: MathReader, el: Element) -> Node:
    _only(el, "num", "den")
    props = _props(el, frozenset({"type"}))
    return Frac(r.args(el, "num"), r.args(el, "den"), props.get("type") or "bar")


def _rad(r: MathReader, el: Element) -> Node:
    _only(el, "deg", "e")
    props = _props(el, frozenset({"degHide"}))
    hidden = "degHide" in props and props["degHide"] in _ON
    return Rad(r.args(el, "e"), None if hidden else r.args(el, "deg"))


def _scripts(r: MathReader, el: Element) -> Node:
    local = _local(el)
    _only(el, "e", "sub", "sup")
    _props(el, frozenset())
    sub = r.args(el, "sub") if local in ("sSub", "sSubSup") else None
    sup = r.args(el, "sup") if local in ("sSup", "sSubSup") else None
    return Scripts(r.args(el, "e"), sub, sup)


def _nary(r: MathReader, el: Element) -> Node:
    _only(el, "sub", "sup", "e")
    props = _props(el, frozenset({"chr", "limLoc", "subHide", "supHide", "grow"}))
    if props.get("grow", "0") in _ON:
        raise _UnsupportedError("grow")
    char = props.get("chr") or "∫"
    loc = props.get("limLoc") or ("subSup" if char in INTEGRALS else "undOvr")
    sub = None if props.get("subHide", "0") in _ON else r.args(el, "sub")
    sup = None if props.get("supHide", "0") in _ON else r.args(el, "sup")
    return Nary(char, sub or None, sup or None, r.args(el, "e"), loc)


def _delim(r: MathReader, el: Element) -> Node:
    _only(el, "e")
    props = _props(el, frozenset({"begChr", "endChr"}))
    parts = el.findall(_m("e"))
    if len(parts) != 1:
        raise _UnsupportedError("separators")  # (a, b) with m:sepChr: one m:e per part
    return Delim(props.get("begChr", "("), props.get("endChr", ")"), r.row(parts[0]))


def _func(r: MathReader, el: Element) -> Node:
    _only(el, "fName", "e")
    _props(el, frozenset())
    return Func(r.args(el, "fName"), r.args(el, "e"))


def _limit(r: MathReader, el: Element) -> Node:
    _only(el, "e", "lim")
    _props(el, frozenset())
    return Limit("low" if _local(el) == "limLow" else "upp", r.args(el, "e"), r.args(el, "lim"))


def _accent(r: MathReader, el: Element) -> Node:
    _only(el, "e")
    props = _props(el, frozenset({"chr"}))
    return Accent(props.get("chr") or DEFAULT_ACCENT, r.args(el, "e"))


def _bar(r: MathReader, el: Element) -> Node:
    _only(el, "e")
    props = _props(el, frozenset({"pos"}))
    return Bar(props.get("pos") or "bot", r.args(el, "e"))


def _group_chr(r: MathReader, el: Element) -> Node:
    _only(el, "e")
    props = _props(el, frozenset({"chr", "pos", "vertJc"}))
    pos = props.get("pos") or "bot"
    # Word puts a brace above with its base at the bottom, and one below with its base at
    # the top; other combinations change the layout.
    if props.get("vertJc", "") not in ("", "bot" if pos == "top" else "top"):
        raise _UnsupportedError("vertJc")
    return GroupChr(props.get("chr") or DEFAULT_GROUP_CHR, pos, r.args(el, "e"))


def _matrix(r: MathReader, el: Element) -> Node:
    _only(el, "mr")
    justs: set[str] = set()
    pr = el.find(_m("mPr"))
    for prop in pr if pr is not None else ():
        name = _local(prop)
        if name == "ctrlPr":
            continue
        if name != "mcs":
            raise _UnsupportedError(name)  # spacing, base justification, placeholders
        for column in prop.iter(_m("mcJc")):
            justs.add(column.get(_m("val"), "center"))
    if len(justs) > 1 or justs - {"center", "left"}:
        raise _UnsupportedError("mcJc")
    rows = tuple(
        tuple(r.row(cell) for cell in row.findall(_m("e"))) for row in el.findall(_m("mr"))
    )
    return Matrix(rows, justs.pop() if justs else "center")


def _eq_arr(r: MathReader, el: Element) -> Node:
    _only(el, "e")
    _props(el, frozenset())
    return EqArr(tuple(_alignments(r.row(row)) for row in el.findall(_m("e"))))


_BOX_PROPS = {
    **{f"hide{side.capitalize()}": ("hide", side) for side in BOX_SIDES},
    **{f"strike{line.upper()}": ("strike", line) for line in BOX_STRIKES},
}


def _border_box(r: MathReader, el: Element) -> Node:
    _only(el, "e")
    props = _props(el, frozenset(_BOX_PROPS))
    on = {_BOX_PROPS[name] for name, value in props.items() if value in _ON}
    return BorderBox(
        r.args(el, "e"),
        tuple(side for side in BOX_SIDES if ("hide", side) in on),
        tuple(line for line in BOX_STRIKES if ("strike", line) in on),
    )


def _phant(r: MathReader, el: Element) -> Node:
    _only(el, "e")
    props = _props(el, frozenset({"show", "zeroWid", "zeroAsc", "zeroDesc"}))

    def flag(name: str, default: str) -> bool:
        return props.get(name, default) in _ON

    return Phant(
        r.args(el, "e"),
        flag("show", "1"),
        flag("zeroWid", "0"),
        flag("zeroAsc", "0"),
        flag("zeroDesc", "0"),
    )


def _alignments(row: Row) -> Row:
    """An equation array row with Word's alignment marks, `&` in its math text, as Align."""
    out: list[Node] = []
    for node in row:
        if not isinstance(node, Run) or node.normal or "&" not in node.text:
            out.append(node)
            continue
        for i, part in enumerate(node.text.split("&")):
            if i:
                out.append(Align())
            if part:
                out.append(replace(node, text=part))
    return tuple(out)


_OBJECTS = {
    "f": _frac,
    "rad": _rad,
    "sSub": _scripts,
    "sSup": _scripts,
    "sSubSup": _scripts,
    "nary": _nary,
    "d": _delim,
    "func": _func,
    "limLow": _limit,
    "limUpp": _limit,
    "acc": _accent,
    "bar": _bar,
    "groupChr": _group_chr,
    "m": _matrix,
    "eqArr": _eq_arr,
    "borderBox": _border_box,
    "phant": _phant,
}


# --- XML kept as it is ------------------------------------------------------------------------

_DECLARATION_RE = re.compile(r'\s+xmlns:(\w+)="([^"]*)"')
_BRACES = {"{": "&#x7B;", "}": "&#x7D;", "\\": "&#x5C;"}


def split_number(omath: Element) -> tuple[Element, str, str | None] | None:
    """Word's numbered display equation, an equation array whose last row ends with `#` and
    the number in parentheses (math/omml.py `numbered`): a copy of the equation without the
    number, the number, and the bookmark around it. None for any other equation."""
    omath = copy.deepcopy(omath)
    parts = [c for c in omath if c.tag != _m("ctrlPr")]
    if len(parts) != 1 or parts[0].tag != _m("eqArr"):
        return None
    array = parts[0]
    rows = array.findall(_m("e"))
    tail = [c for c in rows[-1] if c.tag != _m("ctrlPr")] if rows else []
    if len(tail) < 2 or tail[-1].tag != _m("d") or tail[-2].tag != _m("r"):
        return None
    mark, delim = tail[-2], tail[-1]
    text = mark.find(_m("t"))
    if text is None or not (text.text or "").endswith("#"):
        return None
    props = delim.find(_m("dPr"))
    if props is not None and any(
        p.tag != _m("ctrlPr")
        and not (_local(p) in ("begChr", "endChr") and p.get(_m("val")) in ("(", ")"))
        for p in props
    ):
        return None
    (inside, *more) = delim.findall(_m("e")) or [None]
    allowed = {_m("r"), _m("ctrlPr"), f"{{{W}}}bookmarkStart", f"{{{W}}}bookmarkEnd"}
    if inside is None or more or any(c.tag not in allowed for c in inside):
        return None
    number = "".join(t.text or "" for t in inside.iter(_m("t"))).strip()
    if not number:
        return None
    start = inside.find(f"{{{W}}}bookmarkStart")
    bookmark = start.get(f"{{{W}}}name") if start is not None else None
    rows[-1].remove(delim)
    text.text = (text.text or "")[:-1]
    if not text.text:
        rows[-1].remove(mark)
    for distance in array.findall(f"{_m('eqArrPr')}/{_m('maxDist')}"):
        distance.getparent().remove(distance)  # type: ignore[union-attr]
    if len(rows) == 1:  # one row: the array only held the number
        index = list(omath).index(array)
        omath.remove(array)
        for offset, child in enumerate(c for c in rows[0] if c.tag != _m("ctrlPr")):
            omath.insert(index + offset, child)
    return omath, number, bookmark


def raw_xml(element: Element) -> str:
    """An element as source XML: the usual prefixes (m:, w:, ...) whatever the document used,
    undeclared (raw XML may use them as they are), the Cambria Math font and empty control
    properties left out (Word applies them anyway), and braces and backslashes as character
    references, so the text can sit inside a command's braces."""
    tidy = copy.deepcopy(element)
    for fonts in list(tidy.iter(f"{{{W}}}rFonts")):
        values = {v for k, v in fonts.attrib.items() if not k.endswith("}hint")}
        if values <= {MATH_FONT}:
            _remove(fonts)
    for props in list(tidy.iter(f"{{{W}}}rPr")):
        if len(props) == 1 and props[0].tag == f"{{{W}}}i" and _parent_local(props) == "ctrlPr":
            _remove(props[0])
        if not len(props):
            _remove(props)
    for ctrl in list(tidy.iter(_m("ctrlPr"))):
        if not len(ctrl):
            _remove(ctrl)
    for pr in list(tidy.iter()):
        empty = isinstance(pr.tag, str) and _local(pr).endswith("Pr") and not len(pr)
        if empty and _local(pr) not in ("rPr", "mPr"):
            _remove(pr)
    holder = etree.Element("holder", nsmap=RAW_NAMESPACES)  # m:, w:, ... whatever the source used
    holder.append(tidy)
    etree.cleanup_namespaces(holder, top_nsmap=RAW_NAMESPACES)
    text = etree.tostring(tidy, encoding="unicode")
    known = {uri: prefix for prefix, uri in RAW_NAMESPACES.items()}
    text = _DECLARATION_RE.sub(lambda m: "" if known.get(m.group(2)) == m.group(1) else m[0], text)
    return re.sub(r"[{}\\]", lambda m: _BRACES[m.group(0)], text)


def _remove(element: Element) -> None:
    parent = element.getparent()
    if parent is not None:
        parent.remove(element)


def _parent_local(element: Element) -> str:
    parent = element.getparent()
    return _local(parent) if parent is not None else ""


__all__ = ["MathReader", "raw_xml"]
