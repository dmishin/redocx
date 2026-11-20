"""The math tree as the LaTeX subset (design §3.14): the inverse of the parser, for import.

    to_latex((Frac((Run("a"),), (Run("b"),)),))  ->  "\\frac{a}{b}"

Each object is printed, parsed back and compared with what it came from. An object that does
not come back the same is written as `\\ooxml{...}` instead, so the printed formula always
compiles to the equation it was read from.
"""

from __future__ import annotations

import re
import unicodedata

from redocx.math import symbols as S  # noqa: N812
from redocx.math.nodes import (
    BOX_SIDES,
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
    canonical,
)
from redocx.math.omml import _Writer, omml  # pyright: ignore[reportPrivateUsage]
from redocx.math.parser import CANCELS, PHANTOMS, MathError, parse_math
from redocx.math.read import raw_xml
from redocx.ooxml.xml import Element


def _first_names(*tables: dict[str, str]) -> dict[str, str]:
    """Character -> the first command that gives it."""
    out: dict[str, str] = {}
    for table in tables:
        for name, char in table.items():
            if len(char) == 1 and not char.isascii():
                out.setdefault(char, "\\" + name)
    return out


_CHARS: dict[str, str] = {
    **_first_names(S.GREEK, S.SYMBOLS),
    **{c: "\\" + k for k, c in S.SPACES.items() if len(c) == 1},
    **{c: "\\" + k for k, c in S.ESCAPED.items() if k != "|"},
    "\N{NO-BREAK SPACE}": "~",
    "‖": "\\|",
    "\\": "\\backslash",
    "−": "-",
    "∗": "*",
}
"""Characters of a math run written as a command (or as the ASCII the parser maps)."""

_AS_IS_ASCII = frozenset(c for c in map(chr, range(0x21, 0x7F)) if c not in "\\{}%$#&_^'~*-")
"""ASCII the parser reads back as itself."""

_UPPER_GREEK = {c: "\\" + k for k, c in S.UPPER_GREEK.items()}
_FONTS: dict[tuple[str, str | None], str] = {
    value: name for name, value in reversed(S.FONTS.items())
}
_NARY = {c: name for name, c in reversed(S.NARY.items())}
_ACCENTS = {c: name for name, c in reversed(S.ACCENTS.items())}
_ENVIRONMENTS = {value: name for name, value in S.MATRICES.items() if value != ("", "")}
_TEXT = {None: "text", "i": "textit", "b": "textbf"}
_BOXES: dict[tuple[tuple[str, ...], tuple[str, ...]], str] = {
    ((), ()): "boxed",
    **{(BOX_SIDES, strike): name for name, strike in CANCELS.items()},
}
_PHANTOMS = {
    **{flags: name for name, flags in PHANTOMS.items()},
    (True, False, True, False): "smash[t]",
    (True, False, False, True): "smash[b]",
}


def _delimiters() -> dict[str, str]:
    """Delimiter character -> what to write after \\left or \\right: the character itself
    where it stands for itself, else the first command for it (\\langle rather than <)."""
    out: dict[str, str] = {}
    for key, char in S.DELIMITERS.items():
        rank = 0 if key == char else 1 if key.startswith("\\") else 2
        current = out.get(char)
        if current is None:
            out[char] = key
            continue
        current_rank = 0 if current == char else 1 if current.startswith("\\") else 2
        if rank < current_rank:
            out[char] = key
    return out


_DELIMITERS = _delimiters()
_SIMPLE = (Frac, Rad, Delim, Accent, Bar, Limit, Matrix, EqArr, BorderBox, Phant, Raw)
"""Objects that print as one atom, which a script can follow without braces."""

_COMMAND_END_RE = re.compile(r"\\[A-Za-z]+\*?$")


class _Unprintable(Exception):  # noqa: N818
    """The object has no form in the subset; it is written as raw OMML."""


def to_latex(row: Row) -> str | None:
    """The formula as LaTeX that parses back to it, or None when even with raw OMML for
    some objects it would not (the equation is then written as raw OMML as a whole)."""
    row = canonical(row)
    # Checking each object costs a parse per level of nesting, so first try without.
    for check in (False, True):
        printer = _Printer(check)
        if (
            len(row) == 1
            and isinstance(row[0], EqArr)
            and len(row[0].rows) > 1
            and all(r and not any(isinstance(n, Align) for n in r) for r in row[0].rows)
        ):
            lines = [printer.row(r) for r in row[0].rows]
            text = " \\\\ ".join(t for t, _ in lines)
            expected: Row = (EqArr(tuple(e for _, e in lines)),)
        else:
            text, expected = printer.row(row)
        if _same(text, expected):
            return text
    return None


def _same(text: str, expected: Row) -> bool:
    try:
        parsed = parse_math(text)
    except MathError:
        return False
    return canonical(parsed) == canonical(expected)


def _raw(node: Node) -> tuple[str, Row]:
    """A node as `\\ooxml{...}`."""
    if isinstance(node, Raw):
        return f"\\ooxml{{{node.xml}}}", (node,)
    xml = _source_xml(_Writer().row((node,)))
    return f"\\ooxml{{{xml}}}", (Raw(xml),)


def raw_equation(row: Row, *, display: bool) -> str:
    """A whole equation as raw XML (m:oMath, or m:oMathPara for a display), for `\\raw`."""
    return _source_xml([omml(row, display=display)])


def _source_xml(elements: list[Element]) -> str:
    """Written elements as raw XML source."""
    return "".join(raw_xml(e) for e in elements)


def _join(texts: list[str], *, numbers: bool = True) -> str:
    """Pieces of LaTeX one after another: a space after a command name followed by a letter,
    and (with `numbers`) an empty group between digits of two objects, which would otherwise
    read as one number."""
    out = ""
    for text in texts:
        if not text:
            continue
        if out and _COMMAND_END_RE.search(out) and text[0].isalpha():
            out += " "
        elif numbers and out and out[-1] in _NUMBER and text[0] in _NUMBER:
            out += "{}"
        out += text
    return out


_NUMBER = frozenset("0123456789.")


def _braced(text: str) -> str:
    """A script or argument: bare when it is one character, else in braces."""
    return text if len(text) == 1 and text.isalnum() and text.isascii() else f"{{{text}}}"


def _single_atom(row: Row) -> bool:
    """Whether the row prints as one atom (a script or operand needs no braces around it)."""
    if len(row) != 1:
        return False
    node = row[0]
    if isinstance(node, Run):
        if node.normal:
            return True
        if node.style is not None or node.script is not None:
            return len(node.text) == 1
        return (len(node.text) == 1 and node.text not in S.RELATIONS) or bool(
            re.fullmatch(r"\d+(\.\d+)?", node.text)
        )
    return isinstance(node, _SIMPLE)


def _stops_operand(rest: Row) -> bool:
    """Whether what follows a big operator or function ends its operand: nothing, `&`, or a
    relation, `+` or `-` (math/parser.py `operand`)."""
    if not rest or isinstance(rest[0], Align):
        return True
    first = rest[0]
    return (
        isinstance(first, Run)
        and not first.normal
        and first.style is None
        and first.script is None
        and first.text[:1] in S.RELATIONS
    )


class _Printer:
    def __init__(self, check: bool) -> None:
        self.check = check
        """Parse each object back and write it as raw OMML when it does not match."""

    def row(self, row: Row) -> tuple[str, Row]:
        """A row as LaTeX, and the row that LaTeX parses to (with raw OMML for the objects
        that were written as raw OMML)."""
        texts: list[str] = []
        expected: list[Node] = []
        for i, node in enumerate(row):
            if isinstance(node, Align):
                texts.append("&")
                expected.append(node)
                continue
            text, nodes = self.checked(node, wrap=not _stops_operand(row[i + 1 :]))
            texts.append(text)
            expected.extend(nodes)
        return _join(texts), tuple(expected)

    def checked(self, node: Node, *, wrap: bool = False) -> tuple[str, Row]:
        """A node as LaTeX, parsed back and compared; raw OMML when it does not match."""
        if isinstance(node, Raw):
            return _raw(node)
        try:
            text, expected = self.node(node)
        except _Unprintable:
            return _raw(node)
        if isinstance(node, Nary | Func) and (wrap or not node.e):
            text = f"{{{text}}}"
        if self.check and not _same(text, expected):
            return _raw(node)
        return text, expected

    def arg(self, row: Row) -> tuple[str, Row]:
        """A row in braces (or bare when it is one character)."""
        text, expected = self.row(row)
        return _braced(text), expected

    def node(self, node: Node) -> tuple[str, Row]:
        match node:
            case Run():
                return self.run(node)
            case Frac(num=num, den=den, kind="bar"):
                (n, num_e), (d, den_e) = self.row(num), self.row(den)
                return f"\\frac{{{n}}}{{{d}}}", (Frac(num_e, den_e),)
            case Rad(e=e, degree=degree):
                t, e_e = self.row(e)
                if degree is None:
                    return f"\\sqrt{{{t}}}", (Rad(e_e),)
                d, d_e = self.row(degree)
                d = d if "]" not in d else f"{{{d}}}"
                return f"\\sqrt[{d}]{{{t}}}", (Rad(e_e, d_e),)
            case Scripts(base=base, sub=sub, sup=sup):
                return self.scripts(base, sub, sup)
            case Nary(chr=c, sub=sub, sup=sup, e=e, lim_loc=loc) if c in _NARY:
                name = _NARY[c]
                default = "subSup" if name in S.INTEGRALS else "undOvr"
                text = "\\" + name
                if loc != default:
                    text += "\\limits" if loc == "undOvr" else "\\nolimits"
                sub_e = sup_e = None
                if sub:
                    s, sub_e = self.arg(sub)
                    text += f"_{s}"
                if sup:
                    s, sup_e = self.arg(sup)
                    text += f"^{s}"
                operand, e_e = self.operand(e)
                return _join([text, operand]), (Nary(c, sub_e, sup_e, e_e, loc),)
            case Delim(beg="(", end=")", e=(Frac(num=num, den=den, kind="noBar"),)):
                (n, num_e), (d, den_e) = self.row(num), self.row(den)
                return f"\\binom{{{n}}}{{{d}}}", (Delim("(", ")", (Frac(num_e, den_e, "noBar"),)),)
            case Delim(beg=beg, end=end, e=(Matrix(align="left") as m,)) if (beg, end) == ("{", ""):
                text, matrix = self.matrix("cases", m)
                return text, (Delim(beg, end, (matrix,)),)
            case Delim(beg=beg, end=end, e=(Matrix(align="center") as m,)) if (
                beg,
                end,
            ) in _ENVIRONMENTS:
                text, matrix = self.matrix(_ENVIRONMENTS[beg, end], m)
                return text, (Delim(beg, end, (matrix,)),)
            case Delim(beg=beg, end=end, e=e) if beg in _DELIMITERS and end in _DELIMITERS:
                t, e_e = self.row(e)
                text = _join([f"\\left{_DELIMITERS[beg]}", t, f"\\right{_DELIMITERS[end]}"])
                return text, (Delim(beg, end, e_e),)
            case Func(name=name, e=e):
                text, name_e = self.function_name(name)
                operand, e_e = self.operand(e)
                return _join([text, operand]), (Func(name_e, e_e),)
            case Limit(kind=kind, e=(GroupChr(pos=pos) as brace,), lim=lim) if (
                kind == "upp" and pos == "top"
            ) or (kind == "low" and pos == "bot"):
                b, b_e = self.checked(brace)
                if not b.startswith("\\over") and not b.startswith("\\under"):
                    raise _Unprintable
                s, lim_e = self.arg(lim)
                mark = "^" if kind == "upp" else "_"
                return f"{b}{mark}{s}", (Limit(kind, b_e, lim_e),)
            case Limit(kind=kind, e=e, lim=lim):
                (t, e_e), (s, lim_e) = self.row(e), self.row(lim)
                command = "overset" if kind == "upp" else "underset"
                return f"\\{command}{{{s}}}{{{t}}}", (Limit(kind, e_e, lim_e),)
            case Accent(chr=c, e=e) if c in _ACCENTS:
                t, e_e = self.row(e)
                return f"\\{_ACCENTS[c]}{{{t}}}", (Accent(c, e_e),)
            case Bar(pos=pos, e=e) if pos in ("top", "bot"):
                t, e_e = self.row(e)
                command = "overline" if pos == "top" else "underline"
                return f"\\{command}{{{t}}}", (Bar(pos, e_e),)
            case GroupChr(chr=c, pos=pos, e=e) if (c, pos) in (("⏞", "top"), ("⏟", "bot")):
                t, e_e = self.row(e)
                command = "overbrace" if pos == "top" else "underbrace"
                return f"\\{command}{{{t}}}", (GroupChr(c, pos, e_e),)
            case BorderBox(e=e, hide=hide, strike=strike):
                command = _BOXES.get((hide, strike))
                if command is None:
                    raise _Unprintable
                t, e_e = self.row(e)
                return f"\\{command}{{{t}}}", (BorderBox(e_e, hide, strike),)
            case Phant(e=e, show=show, zero_width=wid, zero_ascent=asc, zero_descent=desc):
                command = _PHANTOMS.get((show, wid, asc, desc))
                if command is None:
                    raise _Unprintable
                t, e_e = self.row(e)
                return f"\\{command}{{{t}}}", (Phant(e_e, show, wid, asc, desc),)
            case Matrix(align="center"):
                text, matrix = self.matrix("matrix", node)
                return text, (matrix,)
            case EqArr(rows=rows):
                lines = [self.row(r) for r in rows]
                body = " \\\\ ".join(t for t, _ in lines)
                return (
                    f"\\begin{{aligned}}{body}\\end{{aligned}}",
                    (EqArr(tuple(e for _, e in lines)),),
                )
            case _:
                raise _Unprintable

    def scripts(self, base: Row, sub: Row | None, sup: Row | None) -> tuple[str, Row]:
        b, base_e = self.row(base)
        if not _single_atom(base) or (base and isinstance(base[0], GroupChr)):
            b = f"{{{b}}}"
        text = b
        sub_e = sup_e = None
        if sub is not None:  # an empty script is kept: it is written as an empty argument
            s, sub_e = self.arg(sub)
            text += f"_{s}"
        if sup and sub is None and len(sup) == 1 and _primes(sup[0]):
            text += "'" * len(sup[0].text)  # type: ignore[union-attr]
            sup_e = sup
        elif sup is not None:
            s, sup_e = self.arg(sup)
            text += f"^{s}"
        return text, (Scripts(base_e, sub_e, sup_e),)

    def operand(self, e: Row) -> tuple[str, Row]:
        """What a big operator or function applies to: bare when it is one atom (or holds no
        relation that would end it early), else in braces."""
        text, expected = self.row(e)
        if not e:
            return "", ()
        if _single_atom(e):
            return text, expected
        return f"{{{text}}}", expected

    def function_name(self, name: Row) -> tuple[str, Row]:
        """\\sin, \\lim_{x\\to 0}, \\sin^2, \\operatorname{argmax}, \\operatorname*{argmin}_x."""
        match name:
            case (Run(style="p", script=None, normal=False) as run,):
                return _function(run.text, "plain"), name
            case (Limit(kind="low", e=(Run(style="p", script=None, normal=False) as run,)) as lim,):
                s, lim_e = self.arg(lim.lim)
                return f"{_function(run.text, 'under')}_{s}", (Limit("low", lim.e, lim_e),)
            case (Scripts(base=(inner,), sub=sub, sup=sup),) if sub or sup:
                if isinstance(inner, Limit):
                    if sub:
                        raise _Unprintable
                    head, head_e = self.function_name((inner,))
                elif isinstance(inner, Run) and inner.style == "p" and not inner.normal:
                    if inner.script is not None:
                        raise _Unprintable
                    head = _function(inner.text, "beside" if sub else "plain")
                    head_e = (inner,)
                else:
                    raise _Unprintable
                text = head
                sub_e = sup_e = None
                if sub:
                    s, sub_e = self.arg(sub)
                    text += f"_{s}"
                if sup:
                    s, sup_e = self.arg(sup)
                    text += f"^{s}"
                return text, (Scripts(head_e, sub_e, sup_e),)
            case _:
                raise _Unprintable

    def matrix(self, environment: str, matrix: Matrix) -> tuple[str, Matrix]:
        rows: list[str] = []
        rows_e: list[tuple[Row, ...]] = []
        for r in matrix.rows:
            cells = [self.row(c) for c in r]
            rows.append(" & ".join(t for t, _ in cells))
            rows_e.append(tuple(e for _, e in cells))
        body = " \\\\ ".join(rows)
        return (
            f"\\begin{{{environment}}}{body}\\end{{{environment}}}",
            Matrix(tuple(rows_e), matrix.align),
        )

    def run(self, run: Run) -> tuple[str, Row]:
        """A run as characters and commands; characters with no form become raw OMML."""
        if run.normal:
            command = _TEXT.get(run.style) if run.script is None else None
            if command is None or "\\" in run.text:
                return _raw(run)
            text = run.text.replace("{", "\\{").replace("}", "\\}")
            return f"\\{command}{{{text}}}", (run,)
        if run.style == "p" and run.script is None and all(c in _UPPER_GREEK for c in run.text):
            return _join([_UPPER_GREEK[c] for c in run.text]), (run,)
        font = None
        if run.style is not None or run.script is not None:
            font = _FONTS.get((run.style or "", run.script))
            if font is None:
                return _raw(run)
        texts: list[str] = []
        expected: list[Node] = []
        written: list[str] = []
        chars: list[str] = []

        def flush() -> None:
            if written:
                inner = _join(written, numbers=False)
                texts.append(f"\\{font}{{{inner}}}" if font else inner)
                expected.append(Run("".join(chars), run.style, run.script))
                written.clear()
                chars.clear()

        for c in run.text:
            form = _char(c)
            if form is None:
                flush()
                text, nodes = _raw(Run(c, run.style, run.script))
                texts.append(text)
                expected.extend(nodes)
            else:
                written.append(form)
                chars.append(c)
        flush()
        return _join(texts), tuple(expected)


def _char(c: str) -> str | None:
    """A math character as the subset writes it, or None when it has no form."""
    if c in _CHARS:
        return _CHARS[c]
    if c in _AS_IS_ASCII:
        return c
    if c.isascii() or c.isspace() or unicodedata.category(c).startswith("C"):
        return None
    return c


def _primes(node: Node) -> bool:
    return (
        isinstance(node, Run)
        and node.style is None
        and not node.normal
        and (bool(node.text) and set(node.text) == {"′"})
    )


def _function(name: str, place: str) -> str:
    """A function name as a command: \\sin, else \\operatorname{...}. `place` is where its
    subscript goes: under (\\lim_{x\\to 0}, \\operatorname*), beside (\\log_2), or plain
    (it has none)."""
    known = {
        "plain": S.FUNCTIONS,
        "under": S.LIMIT_FUNCTIONS,
        "beside": S.FUNCTIONS - S.LIMIT_FUNCTIONS,
    }[place]
    if name in known:
        return "\\" + name
    if not name or name != name.strip() or any(c in name for c in "{}\\"):
        raise _Unprintable
    return f"\\operatorname{'*' if place == 'under' else ''}{{{name}}}"


__all__ = ["raw_equation", "to_latex"]
