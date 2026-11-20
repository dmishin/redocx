"""What each LaTeX math command makes (design §3.14): one table from name to handler.

A handler reads its own arguments from the parser (`core.Parser`) and returns the nodes it
makes. The symbol tables are in `symbols.py`; this module turns them into handlers. A group
switch (\\color, \\bf, \\over) acts on the rest of its group and is in `SWITCHES`.
`KNOWN_COMMANDS` is every name the parser accepts, made from these two tables.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable

from redocx.math import symbols as S  # noqa: N812
from redocx.math.core import Call, Combine, Handler, Item, Parser, PendingFunc, PendingNary, Switch
from redocx.math.nodes import (
    BOX_SIDES,
    Accent,
    Bar,
    BorderBox,
    Delim,
    EqArr,
    Frac,
    GroupChr,
    Limit,
    Matrix,
    Node,
    Phant,
    Rad,
    Raw,
    Row,
    Run,
    Scripts,
    SPre,
    colored,
    styled,
)

PASS_THROUGH = frozenset(
    "mathord mathbin mathrel mathopen mathclose mathpunct mathinner mathnormal "
    "shoveleft shoveright".split()
)
"""Commands whose argument is written as it is: they set TeX spacing classes, which Word
decides itself, the default math font, or the side of a row in multline."""

CANCELS = {"cancel": ("bltr",), "bcancel": ("tlbr",), "xcancel": ("bltr", "tlbr")}
"""Strike-outs: a box with its sides hidden and a diagonal through it."""

PHANTOMS = {
    "phantom": (False, False, False, False),
    "hphantom": (False, False, True, True),
    "vphantom": (False, True, False, False),
    "smash": (True, False, True, True),
    "mathclap": (True, True, False, False),
}
"""(show, zero width, zero ascent, zero descent) of m:phant."""

OPERATORS: dict[str, Row] = {
    "varlimsup": (Bar("top", (Run("lim", style="p"),)),),
    "varliminf": (Bar("bot", (Run("lim", style="p"),)),),
    "varinjlim": (Limit("low", (Run("lim", style="p"),), (Run("→"),)),),
    "varprojlim": (Limit("low", (Run("lim", style="p"),), (Run("←"),)),),
    "injlim": (Run("inj lim", style="p"),),
    "projlim": (Run("proj lim", style="p"),),
}
"""Operator names built from other objects (amsmath); their subscript goes underneath."""

LENGTHS = frozenset({"hspace", "hspace*", "mspace", "mkern", "kern", "hskip", "mskip"})
"""Spaces with a length; braces are optional after the TeX primitives (\\mkern3mu)."""

NEGATED = {"=": "≠", "∈": "∉", "≡": "≢", "⊂": "⊄", "⊆": "⊈", "<": "≮", ">": "≯", "∃": "∄"}
"""\\not before a symbol that has a negated character of its own."""

COLOR_HINT = (
    "a name (red, blue, ...), a mix (red!30, red!50!blue), or [HTML]{FF8000}, "
    "[rgb]{1,0.5,0}, [RGB]{255,128,0}, [cmyk]{0,0.5,1,0}, [gray]{0.5}"
)

OOXML_HINT = "write OMML elements, e.g. \\ooxml{<m:sPre>...</m:sPre>}; see design §3.14"

TAG_HINT = "number a display equation with `\\math[numbered]{…}` or `$$ … $$ {#eq:label}`"

QUAD, THICK = "\N{EM SPACE}", "\N{THREE-PER-EM SPACE}"

MOD = Run("mod", style="p")


# --- handlers made from tables ------------------------------------------------------------


def _nodes(*nodes: Item) -> Handler:
    """A command that is always the same nodes (a symbol, a space)."""
    return lambda p, c: list(nodes)


def _wrap(make: Callable[[Row], Node]) -> Handler:
    """A command with one math argument that becomes one object around it."""
    return lambda p, c: [make(p.math_arg())]


def _nary(char: str, integral: bool) -> Handler:
    def handler(p: Parser, c: Call) -> list[Item]:
        lim_loc = "subSup" if integral else "undOvr"
        modifier = p.peek_command()
        if modifier in ("limits", "nolimits"):
            p.take_command()
            lim_loc = "undOvr" if modifier == "limits" else "subSup"
        return [PendingNary(char, lim_loc)]

    return handler


def _text(style: str | None, warn: bool = False) -> Handler:
    def handler(p: Parser, c: Call) -> list[Item]:
        text = p.text_arg(style)
        if warn:
            p.warn(f"`\\{c.name}` is plain text in Word; the font is not kept", c.start, c.end)
        return list(text)

    return handler


def _x_arrow(char: str) -> Handler:
    def handler(p: Parser, c: Call) -> list[Item]:
        below = p.optional_math()
        arrow = GroupChr(char, "bot", p.math_arg(), "bot")
        return [Limit("low", (arrow,), below)] if below else [arrow]

    return handler


def _phantom(show: bool, wid: bool, asc: bool, desc: bool) -> Handler:
    return lambda p, c: [Phant(p.math_arg(), show, wid, asc, desc)]


# --- handlers with their own rules --------------------------------------------------------


def _sqrt(p: Parser, c: Call) -> list[Item]:
    degree = p.optional_math()
    return [Rad(p.math_arg(), degree)]


def _operatorname(p: Parser, c: Call) -> list[Item]:
    name = (Run(p.raw_arg().strip(), style="p"),)
    return [PendingFunc(name, c.name.endswith("*"))]


def _overset(p: Parser, c: Call) -> list[Item]:
    lim, e = p.math_arg(), p.math_arg()
    return [Limit("low" if c.name == "underset" else "upp", e, lim)]


def _prescript(p: Parser, c: Call) -> list[Item]:
    sup, sub = p.math_arg(), p.math_arg()
    return [SPre(sub, sup, p.math_arg())]


def _genfrac(p: Parser, c: Call) -> list[Item]:
    def delimiter() -> str:
        text = p.raw_arg().strip()
        if text and text not in S.DELIMITERS:
            raise p.error(f"`{text}` is not a delimiter", c.start, p.i)
        return S.DELIMITERS.get(text, "")

    beg, end = delimiter(), delimiter()
    thickness = p.raw_arg().strip()
    p.raw_arg()  # the style: Word sizes fractions itself
    kind = "noBar" if thickness and not any(d in "123456789" for d in thickness) else "bar"
    fraction = Frac(p.math_arg(), p.math_arg(), kind)
    return [Delim(beg, end, (fraction,)) if beg or end else fraction]


def _dropped(p: Parser, c: Call) -> list[Item]:
    """\\leftroot, \\uproot: they move the degree of a root a little; Word places it."""
    p.math_arg()
    return []


def _textcolor(p: Parser, c: Call) -> list[Item]:
    color = p.color_arg(COLOR_HINT)
    return list(colored(p.math_arg(), color))


def _colorbox(p: Parser, c: Call) -> list[Item]:
    return list(p.text_arg(shade=p.color_arg(COLOR_HINT)))


def _fcolorbox(p: Parser, c: Call) -> list[Item]:
    frame, shade = p.color_arg(COLOR_HINT), p.color_arg(COLOR_HINT)
    return [BorderBox(p.text_arg(shade=shade), color=frame)]


def _cancelto(p: Parser, c: Call) -> list[Item]:
    value = p.math_arg()
    p.warn(
        "`\\cancelto` has no arrow in Word; the value is written as a superscript", c.start, c.end
    )
    return [Scripts((BorderBox(p.math_arg(), BOX_SIDES, ("bltr",)),), None, value)]


def _smash(p: Parser, c: Call) -> list[Item]:
    where = p.optional_raw()
    if where not in (None, "t", "b"):
        raise p.error("`\\smash` takes [t] or [b]", c.start, p.i)
    return [Phant(p.math_arg(), True, False, where != "b", where != "t")]


def _pmod(p: Parser, c: Call) -> list[Item]:
    return [Run(QUAD), Delim("(", ")", (MOD, Run(THICK), *p.math_arg()))]


def _pod(p: Parser, c: Call) -> list[Item]:
    return [Run(QUAD), Delim("(", ")", p.math_arg())]


def _space(p: Parser, c: Call) -> list[Item]:
    return [Run(char) for char in p.length_arg(c)]


def _tag(p: Parser, c: Call) -> list[Item]:
    p.raw_arg()
    p.warn("`\\tag` is not supported; it is ignored", c.start, p.i, TAG_HINT)
    return []


def _rule(p: Parser, c: Call) -> list[Item]:
    """\\hline, \\cline{2-3}, \\hdotsfor[1.5]{3}: Word's matrices have no rules."""
    if c.name == "hdotsfor":
        p.optional_raw()
    if c.name != "hline":
        p.raw_arg()
    p.warn(f"`\\{c.name}` is not supported in Word's math; it is ignored", c.start, p.i)
    return []


def _left(p: Parser, c: Call) -> list[Item]:
    beg = p.delimiter(c.start)
    parts: list[Row] = []
    separators: set[str] = set()
    while True:
        nodes, stop = p.sequence(
            frozenset({"right", "middle"}), opening=(c.start, c.end, "`\\left`")
        )
        parts.append(tuple(nodes))
        if stop != "middle":
            break
        middle = p.i
        p.take_command()
        separators.add(p.delimiter(middle))
        if "" in separators or len(separators) > 1:
            raise p.error(
                "one `\\left` ... `\\right` has one separator character in Word",
                middle,
                p.i,
                "use the same `\\middle` delimiter throughout, and not `.`",
            )
    if stop != "right":
        raise p.error("`\\left` without `\\right`", c.start, c.end)
    p.take_command()
    end = p.delimiter(c.start)
    if not separators:
        return [Delim(beg, end, parts[0])]
    return [Delim(beg, end, parts[0], sep=separators.pop(), rest=tuple(parts[1:]))]


def _sized(p: Parser, c: Call) -> list[Item]:
    return [Run(p.delimiter(c.start))]


def _not(p: Parser, c: Call) -> list[Item]:
    nxt = p.primary()
    if len(nxt) == 1 and isinstance(nxt[0], Run):
        text = nxt[0].text
        return [Run(NEGATED.get(text, text + "\N{COMBINING LONG SOLIDUS OVERLAY}"))]
    raise p.error("`\\not` applies to a symbol", c.start, p.i)


def _ooxml(p: Parser, c: Call) -> list[Item]:
    xml = p.raw_arg().strip()
    if not xml:
        raise p.error("`\\ooxml` needs OMML elements", c.start, p.i, OOXML_HINT)
    return [Raw(xml, c.start, p.i)]


# --- environments -------------------------------------------------------------------------

COLUMN_ALIGNMENTS = {"l": "left", "c": "center", "r": "right"}

ENVIRONMENTS_HINT = (
    "supported: aligned, alignedat, gathered, multlined, cases, dcases, rcases, matrix, "
    "pmatrix, bmatrix, Bmatrix, vmatrix, Vmatrix, array, subarray"
)


def _begin(p: Parser, c: Call) -> list[Item]:
    name = p.raw_arg().strip()
    if name in ("array", "subarray", "alignedat", "alignat", "alignat*"):
        p.raw_arg()  # the column spec or count: Word sizes columns itself
    if name == "multlined":
        while p.optional_raw() is not None:  # position and width
            pass
    if name.endswith("*") and name[:-1] in S.MATRICES:  # pmatrix*[r] (mathtools)
        column = p.optional_raw() or "c"
        if column not in COLUMN_ALIGNMENTS:
            raise p.error(f"`{column}` is not a column alignment", c.start, p.i, "use l, c or r")
        return [_matrix(p, c, name, name[:-1], COLUMN_ALIGNMENTS[column])]
    if name in S.MATRICES or name in S.CASES:
        return [_matrix(p, c, name, name, "center")]
    if name in S.ARRAYS or name == "subarray":
        return [_equation_rows(p, c, name)]
    raise p.error(f"unknown environment `{name}`", c.start, p.i, ENVIRONMENTS_HINT)


def _matrix(p: Parser, c: Call, environment: str, kind: str, align: str) -> Node:
    """A matrix or cases environment: cells split by & and rows by \\\\."""
    rows: list[list[Row]] = [[]]
    while True:
        nodes, stop = p.sequence(
            frozenset({"&", "\\\\", "end"}), opening=(c.start, p.i, f"`\\begin{{{environment}}}`")
        )
        rows[-1].append(tuple(nodes))
        if stop == "&":
            continue
        if stop == "\\\\":
            rows.append([])
            continue
        break
    p.end_environment(environment, c.start)
    if len(rows) > 1 and rows[-1] == [()]:  # a final \\
        rows.pop()
    width = max(len(r) for r in rows)
    cells = tuple(tuple(r) + ((),) * (width - len(r)) for r in rows)
    if kind in S.CASES:
        return Delim(*S.CASES[kind], (Matrix(cells, "left"),))
    beg, end = S.MATRICES[kind]
    matrix = Matrix(cells, align)
    return Delim(beg, end, (matrix,)) if beg or end else matrix


def _equation_rows(p: Parser, c: Call, environment: str) -> Node:
    """An aligned, gathered or subarray environment: rows of an equation array."""
    lines: list[Row] = []
    while True:
        nodes, stop = p.sequence(
            frozenset({"\\\\", "end"}),
            align=environment not in (*S.UNALIGNED, "subarray"),
            opening=(c.start, p.i, f"`\\begin{{{environment}}}`"),
        )
        lines.append(tuple(nodes))
        if stop != "\\\\":
            break
    p.end_environment(environment, c.start)
    if len(lines) > 1 and not lines[-1]:
        lines.pop()
    return EqArr(tuple(lines))


# --- group switches -----------------------------------------------------------------------


def _font_switch(style: str | None, script: str | None) -> Switch:
    """{\\bf x}: the rest of the group in a font, as \\mathbf{x}."""
    return lambda p, c: lambda before, rest: [*before, *styled(rest, style, script)]


def _infix(beg: str, end: str, kind: str) -> Switch:
    """{a \\over b}: what comes before in the group is the numerator."""

    def combine(before: list[Node], rest: Row) -> list[Node]:
        fraction = Frac(tuple(before), rest, kind)
        return [Delim(beg, end, (fraction,)) if beg else fraction]

    return lambda p, c: combine


def _color_switch(p: Parser, c: Call) -> Combine:
    """{\\color{red} x}: the rest of the group in a colour."""
    color = p.color_arg(COLOR_HINT)
    return lambda before, rest: [*before, *colored(rest, color)]


SWITCHES: dict[str, Switch] = {
    **{name: _font_switch(*font) for name, font in S.SWITCHES.items()},
    **{name: _infix(*parts) for name, parts in S.INFIX.items()},
    "color": _color_switch,
}

# --- the table ----------------------------------------------------------------------------


def _table() -> dict[str, Handler]:
    """Every command, by name. Where a name is in two families the first one listed wins."""
    out: dict[str, Handler] = {}

    def add(names: Iterable[str], make: Callable[[str], Handler]) -> None:
        for name in names:
            out.setdefault(name, make(name))

    add(S.GREEK, lambda n: _nodes(Run(S.GREEK[n])))
    add(S.UPPER_GREEK, lambda n: _nodes(Run(S.UPPER_GREEK[n], style="p")))
    add(S.SYMBOLS, lambda n: _nodes(Run(S.SYMBOLS[n])))
    add(S.ESCAPED, lambda n: _nodes(Run(S.ESCAPED[n])))
    add(S.SPACES, lambda n: _nodes(Run(S.SPACES[n])) if S.SPACES[n] else _nodes())
    add(
        ("frac", "dfrac", "tfrac", "cfrac"),
        lambda n: lambda p, c: [Frac(p.math_arg(), p.math_arg())],
    )
    add(
        ("binom", "dbinom", "tbinom"),
        lambda n: lambda p, c: [Delim("(", ")", (Frac(p.math_arg(), p.math_arg(), "noBar"),))],
    )
    add(("sqrt",), lambda n: _sqrt)
    add(S.NARY, lambda n: _nary(S.NARY[n], n in S.INTEGRALS))
    add(
        S.FUNCTIONS,
        lambda n: _nodes(PendingFunc((Run(n, style="p"),), n in S.LIMIT_FUNCTIONS)),
    )
    add(("operatorname", "operatorname*"), lambda n: _operatorname)
    add(("mathop",), lambda n: lambda p, c: [PendingFunc(styled(p.math_arg(), "p", None), True)])
    add(S.ACCENTS, lambda n: _wrap(lambda e, char=S.ACCENTS[n]: Accent(char, e)))
    add(("overline",), lambda n: _wrap(lambda e: Bar("top", e)))
    add(("underline",), lambda n: _wrap(lambda e: Bar("bot", e)))
    add(("overbrace",), lambda n: _wrap(lambda e: GroupChr("⏞", "top", e)))
    add(("underbrace",), lambda n: _wrap(lambda e: GroupChr("⏟", "bot", e)))
    add(("overset", "stackrel", "underset"), lambda n: _overset)
    add(PASS_THROUGH, lambda n: lambda p, c: list(p.math_arg()))
    add(S.OVER_UNDER, lambda n: _wrap(lambda e, spec=S.OVER_UNDER[n]: GroupChr(*spec, e)))
    add(S.X_ARROWS, lambda n: _x_arrow(S.X_ARROWS[n]))
    add(("prescript",), lambda n: _prescript)
    add(("substack",), lambda n: lambda p, c: [EqArr(p.rows_arg())])
    add(("genfrac",), lambda n: _genfrac)
    add(("leftroot", "uproot"), lambda n: _dropped)
    add(("mathllap", "mathrlap"), lambda n: _phantom(True, True, False, False))
    add(OPERATORS, lambda n: _nodes(PendingFunc(OPERATORS[n], True)))
    add(("textcolor",), lambda n: _textcolor)
    add(("colorbox",), lambda n: _colorbox)
    add(("fcolorbox",), lambda n: _fcolorbox)
    add(("boxed",), lambda n: _wrap(BorderBox))
    add(("fbox",), lambda n: lambda p, c: [BorderBox(p.text_arg())])
    add(CANCELS, lambda n: _wrap(lambda e, strike=CANCELS[n]: BorderBox(e, BOX_SIDES, strike)))
    add(("cancelto",), lambda n: _cancelto)
    add(("smash",), lambda n: _smash)
    add(PHANTOMS, lambda n: _phantom(*PHANTOMS[n]))
    add(("bmod",), lambda n: _nodes(Run(THICK), MOD, Run(THICK)))
    add(("mod",), lambda n: _nodes(Run(QUAD), MOD, Run(THICK)))
    add(("pmod",), lambda n: _pmod)
    add(("pod",), lambda n: _pod)
    add(S.FONTS, lambda n: lambda p, c, font=S.FONTS[n]: list(styled(p.math_arg(), *font)))
    add(S.TEXT, lambda n: _text({"textit": "i", "textbf": "b"}.get(n)))
    add(S.TEXT_STYLES, lambda n: _text(S.TEXT_STYLES[n], n in ("textsf", "texttt", "textsc")))
    add(LENGTHS, lambda n: _space)
    add(("tag", "tag*"), lambda n: _tag)
    add(("hline", "cline", "hdotsfor"), lambda n: _rule)
    add(("left",), lambda n: _left)
    add(S.SIZED, lambda n: _sized)
    add(("begin",), lambda n: _begin)
    add(("not",), lambda n: _not)
    add(("ooxml",), lambda n: _ooxml)
    add(S.IGNORED - {"left", "right"}, lambda n: _nodes())
    return out


COMMANDS = _table()
"""Every command that makes something, by name."""

KNOWN_COMMANDS = sorted({*COMMANDS, *SWITCHES, "right", "middle", "end"})
"""Every command name the parser accepts."""
