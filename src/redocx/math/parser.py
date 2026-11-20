"""Parse the LaTeX math subset (design §3.14) into the math tree.

    parse_math(r"\\frac{a}{b} + \\sqrt{x}")  ->  (Frac(...), Run("+"), Rad(...))

Errors are MathError with the offending text's offsets, so the compiler can point at
them inside the document.
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass, replace

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
)

PASS_THROUGH = frozenset(
    "mathord mathbin mathrel mathopen mathclose mathpunct mathinner mathnormal".split()
)
"""Commands whose argument is written as it is: they set TeX spacing classes, which Word
decides itself, or the default math font."""

CANCELS = {"cancel": ("bltr",), "bcancel": ("tlbr",), "xcancel": ("bltr", "tlbr")}
"""Strike-outs: a box with its sides hidden and a diagonal through it."""

PHANTOMS = {
    "phantom": (False, False, False, False),
    "hphantom": (False, False, True, True),
    "vphantom": (False, True, False, False),
    "smash": (True, False, True, True),
}
"""(show, zero width, zero ascent, zero descent) of m:phant."""

QUAD, THICK = "\N{EM SPACE}", "\N{THREE-PER-EM SPACE}"

KNOWN_COMMANDS = sorted(
    {
        *S.GREEK, *S.UPPER_GREEK, *S.SYMBOLS, *S.NARY, *S.FUNCTIONS, *S.ACCENTS, *S.FONTS, *S.TEXT,
        *S.SIZED, *S.IGNORED, *(k for k in S.SPACES if k.isalpha()),
        "frac", "dfrac", "tfrac", "cfrac", "binom", "dbinom", "tbinom", "sqrt", "operatorname",
        "mathop", "overline", "underline", "overbrace", "underbrace", "overset", "underset",
        "stackrel", "left", "right", "begin", "end", "not", "ooxml", "bmod", "pmod", "mod", "pod",
        "boxed", "fbox", "cancelto", *CANCELS, *PHANTOMS,
        *PASS_THROUGH,
    }
)  # fmt: skip

OOXML_HINT = "write OMML elements, e.g. \\ooxml{<m:sPre>...</m:sPre>}; see design §3.14"


class MathError(ValueError):
    def __init__(self, message: str, start: int, end: int, hint: str = "") -> None:
        super().__init__(message)
        self.message = message
        self.start = start
        self.end = end
        self.hint = hint


@dataclass(frozen=True)
class _PendingNary:
    """A big operator still collecting its limits and operand."""

    chr: str
    lim_loc: str
    sub: Row | None = None
    sup: Row | None = None


@dataclass(frozen=True)
class _PendingFunc:
    """A function name still collecting a limit (\\lim_{x\\to 0}) and its argument."""

    name: Row
    limits: bool
    lim: Row | None = None


class _Parser:
    def __init__(self, text: str) -> None:
        self.s = text
        self.i = 0
        self.warnings: list[MathError] = []
        """What is written only approximately; the formula is still made."""

    # --- scanning ---------------------------------------------------------------------------

    def error(
        self, message: str, start: int | None = None, end: int | None = None, hint: str = ""
    ) -> MathError:
        start = self.i if start is None else start
        return MathError(
            message, start, max(end if end is not None else start + 1, start + 1), hint
        )

    def peek(self) -> str:
        return self.s[self.i] if self.i < len(self.s) else ""

    def skip_space(self) -> None:
        while self.i < len(self.s) and self.s[self.i] in " \t\r\n":
            self.i += 1

    def command_at(self, i: int) -> tuple[str, int]:
        """The command starting at the backslash at i, and the index after it."""
        j = i + 1
        if j < len(self.s) and self.s[j].isalpha():
            while j < len(self.s) and self.s[j].isalpha():
                j += 1
            if (
                j < len(self.s)
                and self.s[j] == "*"
                and self.s[i + 1 : j]
                in ("operatorname", "align", "gather", "multline", "alignat", "eqnarray")
            ):
                j += 1
            return self.s[i + 1 : j], j
        return self.s[i + 1 : j + 1], j + 1

    def peek_command(self) -> str | None:
        self.skip_space()
        if self.peek() != "\\":
            return None
        return self.command_at(self.i)[0]

    def raw_group(self) -> str:
        """The text of a {...} argument, braces balanced (for \\text, \\begin, \\operatorname)."""
        self.skip_space()
        if self.peek() != "{":
            raise self.error("expected `{`")
        depth, start = 0, self.i
        while self.i < len(self.s):
            c = self.s[self.i]
            if c == "\\":
                self.i += 2
                continue
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    self.i += 1
                    return self.s[start + 1 : self.i - 1]
            self.i += 1
        raise self.error("unclosed `{`", start)

    def text_group(self) -> str:
        """The text of a text-mode argument (\\text{...}, \\fbox{...})."""
        return self.raw_group().replace("\\{", "{").replace("\\}", "}").replace("\\ ", " ")

    def optional(self, start: int) -> str | None:
        """The text of an optional [...] argument, or None when there is none."""
        self.skip_space()
        if self.peek() != "[":
            return None
        end = self.s.find("]", self.i)
        if end < 0:
            raise self.error("unclosed `[`", start, self.i + 1)
        text, self.i = self.s[self.i + 1 : end].strip(), end + 1
        return text

    # --- sequences ------------------------------------------------------------------------

    def sequence(
        self,
        stops: frozenset[str],
        *,
        align: bool = False,
        opening: tuple[int, int, str] | None = None,
    ) -> tuple[list[Node], str]:
        """Nodes until one of `stops`: } ] & \\\\ \\right \\end eof. Returns the stop found
        (not consumed, except & and \\\\). With `align`, & is an alignment point."""
        out: list[Node] = []
        while True:
            self.skip_space()
            c = self.peek()
            if not c:
                if "eof" not in stops:
                    if opening is not None:
                        start, end, what = opening
                        raise self.error(f"{what} is not closed", start, end)
                    raise self.error("unexpected end of the formula: something is not closed")
                return out, "eof"
            if c in "}]" and c in stops:
                return out, c
            if c == "}":
                raise self.error("unexpected `}`")
            if c == "&":
                if align:
                    self.i += 1
                    out.append(Align())
                    continue
                if "&" in stops:
                    self.i += 1
                    return out, "&"
                raise self.error("`&` belongs in an environment such as aligned, matrix or cases")
            if c == "\\":
                name, after = self.command_at(self.i)
                if name == "\\":
                    if "\\\\" in stops:
                        self.i = after
                        return out, "\\\\"
                    raise self.error(
                        "`\\\\` (a new row) belongs in an environment or a display equation",
                        end=after,
                    )
                if name in ("right", "end"):
                    if name in stops:
                        return out, name
                    raise self.error(
                        f"`\\{name}` without a matching "
                        f"`\\{'left' if name == 'right' else 'begin'}`",
                        end=after,
                    )
            out.extend(self.atom(stops))

    def group(self) -> Row:
        """A {...} argument or a single token."""
        self.skip_space()
        c = self.peek()
        if c == "{":
            start = self.i
            self.i += 1
            nodes, stop = self.sequence(frozenset({"}"}), opening=(start, start + 1, "`{`"))
            if stop != "}":
                raise self.error("unclosed `{`", start)
            self.i += 1
            return tuple(nodes)
        if not c:
            raise self.error("missing argument")
        if c == "\\":
            return tuple(
                n for n in self.primary() if not isinstance(n, _PendingNary | _PendingFunc)
            )
        if c in "}&^_":
            raise self.error("missing argument")
        self.i += 1
        return (Run("−" if c == "-" else c),)

    # --- atoms -----------------------------------------------------------------------------

    def atom(self, stops: frozenset[str] = frozenset()) -> list[Node]:
        """A primary with its scripts; big operators and functions take their operand."""
        self.skip_space()
        grouped = self.peek() == "{"  # {...}^2: the whole group is the base, as in LaTeX
        if self.peek() and self.peek() in "^_'":
            base: list[Node | _PendingNary | _PendingFunc] = [Run("")]
        else:
            base = list(self.primary())
        self.skip_space()
        if not base and not (grouped and self.peek() and self.peek() in "^_'"):
            return []
        head = base[-1] if len(base) == 1 and not grouped else None
        sub = sup = None
        primed = False  # the superscript so far is primes only: f'^2 is f^{\prime 2}
        while True:
            self.skip_space()
            c = self.peek()
            if c == "'":
                primes = ""
                while self.peek() == "'":
                    primes += "′"
                    self.i += 1
                primed = sup is None or primed
                sup = (*(sup or ()), Run(primes))
                continue
            if not c or c not in "^_":
                break
            start = self.i
            self.i += 1
            arg = self.group()
            if c == "^":
                if sup is not None and not primed:
                    raise self.error("double superscript: use braces, x^{a^b}", start)
                sup = (*(sup or ()), *arg)
                primed = False
            else:
                if sub is not None:
                    raise self.error("double subscript: use braces, x_{a_b}", start)
                sub = arg
        if isinstance(head, _PendingNary):
            return [self.nary(replace(head, sub=sub, sup=sup), stops)]
        if isinstance(head, _PendingFunc):
            return [self.func(head, sub, sup, stops)]
        if sub is None and sup is None:
            return base  # type: ignore[return-value]
        if grouped:
            return [Scripts(tuple(base), sub, sup)]  # type: ignore[arg-type]
        if isinstance(head, GroupChr) and (
            (head.pos == "top" and sup) or (head.pos == "bot" and sub)
        ):
            label = sup if head.pos == "top" else sub
            limit = Limit("upp" if head.pos == "top" else "low", (head,), label or ())
            rest_sub, rest_sup = (sub, None) if head.pos == "top" else (None, sup)
            return (
                [limit] if not (rest_sub or rest_sup) else [Scripts((limit,), rest_sub, rest_sup)]
            )
        last = base[-1]
        before = base[:-1]
        return [*before, Scripts((last,), sub, sup)]  # type: ignore[list-item]

    def operand(self, stops: frozenset[str]) -> Row:
        """What a big operator or function applies to: atoms up to a relation, + or -."""
        nodes: list[Node] = []
        while True:
            self.skip_space()
            c = self.peek()
            if (
                not c
                or c in "}]&"
                or (c == "\\" and self.command_at(self.i)[0] in ("\\", "right", "end"))
            ):
                break
            start = self.i
            atom = self.atom(stops)
            if (
                nodes
                and len(atom) == 1
                and isinstance(atom[0], Run)
                and atom[0].text in S.RELATIONS
            ):
                self.i = start
                break
            nodes.extend(atom)
            if not nodes:
                continue
        return tuple(nodes)

    def nary(self, pending: _PendingNary, stops: frozenset[str]) -> Nary:
        return Nary(pending.chr, pending.sub, pending.sup, self.operand(stops), pending.lim_loc)

    def func(
        self, pending: _PendingFunc, sub: Row | None, sup: Row | None, stops: frozenset[str]
    ) -> Node:
        name: Row = pending.name
        if sub is not None and pending.limits:
            name = (Limit("low", name, sub),)
            sub = None
        if sub is not None or sup is not None:
            name = (Scripts(name, sub, sup),)
        return Func(name, self.operand(stops))

    def primary(self) -> list[Node | _PendingNary | _PendingFunc]:
        self.skip_space()
        c = self.peek()
        start = self.i
        if c == "{":
            return list(self.group())
        if c.isalpha():
            self.i += 1
            return [Run(c)]
        if c.isdigit() or (c == "." and self.s[self.i + 1 : self.i + 2].isdigit()):
            j = self.i
            while j < len(self.s) and (
                self.s[j].isdigit() or (self.s[j] == "." and self.s[j + 1 : j + 2].isdigit())
            ):
                j += 1
            text, self.i = self.s[self.i : j], j
            return [Run(text)]
        if c != "\\":
            self.i += 1
            return [Run({"-": "−", "~": " ", "*": "∗"}.get(c, c))]
        name, after = self.command_at(self.i)
        self.i = after
        if name in S.GREEK:
            return [Run(S.GREEK[name])]
        if name in S.UPPER_GREEK:
            return [Run(S.UPPER_GREEK[name], style="p")]
        if name in S.SYMBOLS:
            return [Run(S.SYMBOLS[name])]
        if name in S.ESCAPED:
            return [Run(S.ESCAPED[name])]
        if name in S.SPACES:
            return [Run(S.SPACES[name])] if S.SPACES[name] else []
        if name in ("frac", "dfrac", "tfrac", "cfrac"):
            return [Frac(self.group(), self.group())]
        if name in ("binom", "dbinom", "tbinom"):
            return [Delim("(", ")", (Frac(self.group(), self.group(), "noBar"),))]
        if name == "sqrt":
            degree = None
            self.skip_space()
            if self.peek() == "[":
                self.i += 1
                nodes, stop = self.sequence(frozenset({"]"}), opening=(self.i - 1, self.i, "`[`"))
                if stop != "]":
                    raise self.error("unclosed `[`", start)
                self.i += 1
                degree = tuple(nodes)
            return [Rad(self.group(), degree)]
        if name in S.NARY:
            lim_loc = "subSup" if name in S.INTEGRALS else "undOvr"
            modifier = self.peek_command()
            if modifier in ("limits", "nolimits"):
                self.i = self.command_at(self.i)[1]
                lim_loc = "undOvr" if modifier == "limits" else "subSup"
            return [_PendingNary(S.NARY[name], lim_loc)]
        if name in S.FUNCTIONS:
            return [_PendingFunc((Run(name, style="p"),), name in S.LIMIT_FUNCTIONS)]
        if name in ("operatorname", "operatorname*"):
            return [_PendingFunc((Run(self.raw_group().strip(), style="p"),), name.endswith("*"))]
        if name == "mathop":
            return [_PendingFunc(_styled(self.group(), "p", None), True)]
        if name in S.ACCENTS:
            return [Accent(S.ACCENTS[name], self.group())]
        if name in ("overline", "underline"):
            return [Bar("top" if name == "overline" else "bot", self.group())]
        if name in ("overbrace", "underbrace"):
            return [
                GroupChr(
                    "⏞" if name == "overbrace" else "⏟",
                    "top" if name == "overbrace" else "bot",
                    self.group(),
                )
            ]
        if name in ("overset", "stackrel", "underset"):
            lim, e = self.group(), self.group()
            return [Limit("low" if name == "underset" else "upp", e, lim)]
        if name in PASS_THROUGH:
            return list(self.group())
        if name == "boxed":
            return [BorderBox(self.group())]
        if name == "fbox":
            return [BorderBox((Run(self.text_group(), normal=True),))]
        if name in CANCELS:
            return [BorderBox(self.group(), BOX_SIDES, CANCELS[name])]
        if name == "cancelto":
            value = self.group()
            self.warnings.append(
                self.error(
                    "`\\cancelto` has no arrow in Word; the value is written as a superscript",
                    start,
                    after,
                )
            )
            return [Scripts((BorderBox(self.group(), BOX_SIDES, ("bltr",)),), None, value)]
        if name in PHANTOMS:
            show, wid, asc, desc = PHANTOMS[name]
            if name == "smash":
                where = self.optional(start)
                if where not in (None, "t", "b"):
                    raise self.error("`\\smash` takes [t] or [b]", start, self.i)
                asc, desc = where != "b", where != "t"
            return [Phant(self.group(), show, wid, asc, desc)]
        if name == "bmod":
            return [Run(THICK), Run("mod", style="p"), Run(THICK)]
        if name == "mod":
            return [Run(QUAD), Run("mod", style="p"), Run(THICK)]
        if name == "pmod":
            return [Run(QUAD), Delim("(", ")", (Run("mod", style="p"), Run(THICK), *self.group()))]
        if name == "pod":
            return [Run(QUAD), Delim("(", ")", self.group())]
        if name in S.FONTS:
            style, script = S.FONTS[name]
            return list(_styled(self.group(), style, script))
        if name in S.TEXT:
            style = {"textit": "i", "textbf": "b"}.get(name)
            return [Run(self.text_group(), style=style, normal=True)]
        if name == "left":
            beg = self.delimiter(start)
            nodes, stop = self.sequence(frozenset({"right"}), opening=(start, after, "`\\left`"))
            if stop != "right":
                raise self.error("`\\left` without `\\right`", start, after)
            self.i = self.command_at(self.i)[1]
            end = self.delimiter(start)
            return [Delim(beg, end, tuple(nodes))]
        if name in S.SIZED:
            return [Run(self.delimiter(start))]
        if name == "begin":
            return [self.environment(start)]
        if name == "not":
            nxt = self.primary()
            if len(nxt) == 1 and isinstance(nxt[0], Run):
                negated = {
                    "=": "≠",
                    "∈": "∉",
                    "≡": "≢",
                    "⊂": "⊄",
                    "⊆": "⊈",
                    "<": "≮",
                    ">": "≯",
                    "∃": "∄",
                }
                return [Run(negated.get(nxt[0].text, nxt[0].text + "̸"))]
            raise self.error("`\\not` applies to a symbol", start, self.i)
        if name == "ooxml":
            xml = self.raw_group().strip()
            if not xml:
                raise self.error("`\\ooxml` needs OMML elements", start, self.i, OOXML_HINT)
            return [Raw(xml, start, self.i)]
        if name in S.IGNORED and name not in ("left", "right"):
            return []
        close = difflib.get_close_matches(name, KNOWN_COMMANDS, n=1)
        raise self.error(
            f"unknown math command `\\{name}`",
            start,
            after,
            f"did you mean `\\{close[0]}`?" if close else "",
        )

    def delimiter(self, start: int) -> str:
        self.skip_space()
        c = self.peek()
        if c == "\\":
            name, after = self.command_at(self.i)
            key = "\\" + name
            if key in S.DELIMITERS:
                self.i = after
                return S.DELIMITERS[key]
            raise self.error(f"`\\{name}` is not a delimiter", self.i, after)
        if c in S.DELIMITERS:
            self.i += 1
            return S.DELIMITERS[c]
        raise self.error("expected a delimiter such as ( [ \\{ | or .", start, self.i + 1)

    def environment(self, start: int) -> Node:
        name = self.raw_group().strip()
        if name == "array":
            self.raw_group()  # the column spec: Word sizes columns itself
        if name in S.MATRICES or name == "cases":
            rows: list[list[Row]] = [[]]
            while True:
                nodes, stop = self.sequence(
                    frozenset({"&", "\\\\", "end"}), opening=(start, self.i, f"`\\begin{{{name}}}`")
                )
                rows[-1].append(tuple(nodes))
                if stop == "&":
                    continue
                if stop == "\\\\":
                    rows.append([])
                    continue
                break
            self.end_environment(name, start)
            if len(rows) > 1 and rows[-1] == [()]:  # a final \\
                rows.pop()
            width = max(len(r) for r in rows)
            matrix_rows = tuple(tuple(r) + ((),) * (width - len(r)) for r in rows)
            if name == "cases":
                return Delim("{", "", (Matrix(matrix_rows, "left"),))
            beg, end = S.MATRICES[name]
            matrix = Matrix(matrix_rows)
            return Delim(beg, end, (matrix,)) if beg or end else matrix
        if name in S.ARRAYS:
            lines: list[Row] = []
            while True:
                nodes, stop = self.sequence(
                    frozenset({"\\\\", "end"}),
                    align=name not in ("gathered", "gather", "gather*", "multline", "multline*"),
                    opening=(start, self.i, f"`\\begin{{{name}}}`"),
                )
                lines.append(tuple(nodes))
                if stop == "\\\\":
                    continue
                break
            self.end_environment(name, start)
            if len(lines) > 1 and not lines[-1]:
                lines.pop()
            return EqArr(tuple(lines))
        raise self.error(
            f"unknown environment `{name}`",
            start,
            self.i,
            "supported: aligned, gathered, cases, matrix, pmatrix, bmatrix, Bmatrix, vmatrix, "
            "Vmatrix, array",
        )

    def end_environment(self, name: str, start: int) -> None:
        if self.peek_command() != "end":
            raise self.error(f"`\\begin{{{name}}}` is not closed", start)
        self.i = self.command_at(self.i)[1]
        closing = self.raw_group().strip()
        if closing != name:
            raise self.error(
                f"`\\begin{{{name}}}` is closed by `\\end{{{closing}}}`", start, self.i
            )


def _styled(row: Row, style: str | None, script: str | None) -> Row:
    """`row` with a font style applied to its text (\\mathbf{...}, \\mathbb{...})."""

    def apply(node: Node) -> Node:
        if isinstance(node, Run) and not node.normal:
            return replace(node, style=style, script=script or node.script)
        if isinstance(node, Scripts):
            return replace(node, base=_styled(node.base, style, script))
        return node

    return tuple(apply(n) for n in row)


def parse_math(text: str) -> Row:
    """The math tree of a formula. Rows separated by \\\\ at the top level become an
    equation array (as in a gathered display)."""
    return parse_formula(text)[0]


def parse_formula(text: str) -> tuple[Row, tuple[MathError, ...]]:
    """The math tree of a formula and the warnings about it (`parse_math`)."""
    parser = _Parser(text)
    rows: list[Row] = []
    while True:
        nodes, stop = parser.sequence(frozenset({"\\\\", "eof"}))
        rows.append(tuple(nodes))
        if stop == "eof":
            break
    warnings = tuple(parser.warnings)
    if len(rows) > 1:
        if not rows[-1]:
            rows.pop()
        return (EqArr(tuple(rows)),), warnings
    return rows[0], warnings


__all__ = ["MathError", "parse_formula", "parse_math"]
