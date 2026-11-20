"""The LaTeX math parser's machinery: scanning, arguments, sequences and atoms (design §3.14).

What each command means is in `commands.py`; the parser is given that table, so this module
knows no command by name except the few that shape a formula: `\\left`'s partners, `\\end`,
`\\\\` and the scripts.
"""

from __future__ import annotations

import difflib
import re
from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass, replace
from typing import Literal

from redocx.math import symbols as S  # noqa: N812
from redocx.math.nodes import (
    Align,
    Func,
    GroupChr,
    Limit,
    Nary,
    Node,
    Row,
    Run,
    Scripts,
    shaded,
)

Stop = Literal["}", "]", "$", "&", "\\\\", "right", "middle", "end", "eof"]
"""Where a sequence ends: a closing character, a new row, a partner command, the end."""

_CLOSERS = ("right", "middle", "end")
"""Commands that end a sequence; each closes what another command opened."""

TEXT_ESCAPES = frozenset("$%&#_{} ")
"""What follows a backslash in text to stand for itself: \\$ is a dollar sign."""

_LENGTH_RE = re.compile(r"\s*(-?\d*\.?\d+)\s*([a-z]{2})")


class MathError(ValueError):
    """A problem in a formula, at offsets into its text; also used for warnings."""

    def __init__(self, message: str, start: int, end: int, hint: str = "") -> None:
        super().__init__(message)
        self.message = message
        self.start = start
        self.end = end
        self.hint = hint


@dataclass(frozen=True)
class Call:
    """A command where it is written: its name and the span of the name."""

    name: str
    start: int
    end: int


@dataclass(frozen=True)
class PendingNary:
    """A big operator still collecting its limits and operand."""

    chr: str
    lim_loc: str
    sub: Row | None = None
    sup: Row | None = None


@dataclass(frozen=True)
class PendingFunc:
    """A function name still collecting a limit (\\lim_{x\\to 0}) and its argument."""

    name: Row
    limits: bool
    lim: Row | None = None


type Item = Node | PendingNary | PendingFunc
type Handler = Callable[[Parser, Call], list[Item]]
"""What a command makes, reading its own arguments from the parser."""

type Combine = Callable[[list[Node], Row], list[Node]]
"""How a group switch joins what came before it in the group with what comes after."""

type Switch = Callable[[Parser, Call], Combine]
"""A command that acts on the rest of its group (\\color, \\bf, \\over): it reads its
arguments, then says how to combine the group."""


class Parser:
    def __init__(
        self,
        text: str,
        commands: Mapping[str, Handler],
        switches: Mapping[str, Switch],
        known: Collection[str],
    ) -> None:
        self.s = text
        self.i: int = 0
        self.commands = commands
        self.switches = switches
        self.known = known
        """Every command name, for the "did you mean" hint."""
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

    def warn(self, message: str, start: int, end: int, hint: str = "") -> None:
        self.warnings.append(self.error(message, start, end, hint))

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
                and self.s[i + 1 : j] in ("operatorname", "tag", "hspace")
            ):
                j += 1
            return self.s[i + 1 : j], j
        return self.s[i + 1 : j + 1], j + 1

    def peek_command(self) -> str | None:
        self.skip_space()
        if self.peek() != "\\":
            return None
        return self.command_at(self.i)[0]

    def take_command(self) -> None:
        """Moves past the command at the current position."""
        self.i = self.command_at(self.i)[1]

    # --- arguments --------------------------------------------------------------------------

    def math_arg(self) -> Row:
        """A {...} argument in math, or a single token."""
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
            return tuple(n for n in self.primary() if not isinstance(n, PendingNary | PendingFunc))
        if c in "}&^_":
            raise self.error("missing argument")
        self.i += 1
        return (Run("−" if c == "-" else c),)

    def optional_math(self) -> Row | None:
        """An optional [...] argument in math, or None when there is none."""
        self.skip_space()
        if self.peek() != "[":
            return None
        opening = self.i
        self.i += 1
        nodes, stop = self.sequence(frozenset({"]"}), opening=(opening, opening + 1, "`[`"))
        if stop != "]":
            raise self.error("unclosed `[`", opening)
        self.i += 1
        return tuple(nodes)

    def raw_arg(self) -> str:
        """The text of a {...} argument, braces balanced (\\begin, \\operatorname, \\ooxml)."""
        self.skip_space()
        if self.peek() != "{":
            raise self.error("expected `{`")
        return self._balanced("{", "}")

    def optional_raw(self) -> str | None:
        """The text of an optional [...] argument, stripped, or None when there is none.
        Brackets inside braces do not end it: [{]}] is `{]}`."""
        self.skip_space()
        if self.peek() != "[":
            return None
        return self._balanced("[", "]").strip()

    def _balanced(self, opening: str, closing: str) -> str:
        """The text from the bracket at the current position to its partner; braces and
        backslash escapes inside are skipped over."""
        start, depth = self.i, 0
        self.i += 1
        while self.i < len(self.s):
            c = self.s[self.i]
            if c == "\\":
                self.i += 2
                continue
            if c == closing and depth == 0:
                self.i += 1
                return self.s[start + 1 : self.i - 1]
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
            self.i += 1
        raise self.error(f"unclosed `{opening}`", start)

    def text_arg(self, style: str | None = None, shade: str | None = None) -> Row:
        """A text-mode argument (\\text{...}, \\colorbox{...}{...}): plain text runs, and math
        between dollar signs as in LaTeX (\\text{if $n$ is even}). An escape such as \\$ or
        \\% is its character; braces only group. A shade covers the math too."""
        self.skip_space()
        if self.peek() != "{":
            raise self.error("expected `{`")
        opening = self.i
        self.i += 1
        out: list[Node] = []
        text: list[str] = []

        def flush() -> None:
            if text:
                out.append(Run("".join(text), style=style, normal=True, shade=shade))
                text.clear()

        depth = 0
        while True:
            if self.i >= len(self.s):
                raise self.error("`{` is not closed", opening, opening + 1)
            c = self.s[self.i]
            if c == "\\":
                if self.s[self.i + 1 : self.i + 2] in TEXT_ESCAPES:
                    text.append(self.s[self.i + 1])
                    self.i += 2
                    continue
                after = self.command_at(self.i)[1]
                text.append(self.s[self.i : after])  # other commands are kept as they are
                self.i = after
                continue
            if c == "$":
                flush()
                dollar = self.i
                self.i += 1
                nodes, stop = self.sequence(
                    frozenset({"$", "}"}), opening=(dollar, dollar + 1, "`$`")
                )
                if stop != "$":
                    raise self.error("`$` is not closed", dollar, dollar + 1)
                self.i += 1
                out.extend(shaded(tuple(nodes), shade) if shade else nodes)
                continue
            self.i += 1
            if c == "{":
                depth += 1
            elif c == "}":
                if not depth:
                    break
                depth -= 1
            else:
                text.append(c)
        flush()
        if not out:  # an empty argument is an empty text run, as Word stores \text{}
            out.append(Run("", style=style, normal=True, shade=shade))
        return tuple(out)

    def rows_arg(self) -> tuple[Row, ...]:
        """A {...} argument of rows separated by \\\\ (\\substack)."""
        self.skip_space()
        if self.peek() != "{":
            raise self.error("expected `{`")
        opening = self.i
        self.i += 1
        lines: list[Row] = []
        while True:
            nodes, stop = self.sequence(
                frozenset({"\\\\", "}"}), opening=(opening, opening + 1, "`{`")
            )
            lines.append(tuple(nodes))
            if stop == "}":
                self.i += 1
                break
        if len(lines) > 1 and not lines[-1]:
            lines.pop()
        return tuple(lines)

    def color_arg(self, hint: str) -> str:
        """A colour argument, [model]{value}, as RRGGBB (xcolor names and mixes, HTML, rgb,
        RGB, cmyk, gray)."""
        model = self.optional_raw()
        value_start = self.i
        value = self.raw_arg().strip()
        try:
            return S.color(value, model)
        except ValueError as e:
            raise self.error(str(e), value_start, self.i, hint) from None

    def length_arg(self, call: Call) -> str:
        """Unicode spaces for a length, {1em} or 3mu after a TeX primitive; nothing when it
        is negative."""
        self.skip_space()
        if self.peek() == "{":
            text = self.raw_arg()
        else:
            match = _LENGTH_RE.match(self.s, self.i)
            text = match.group(0) if match else ""
            self.i = match.end() if match else self.i
        length = _LENGTH_RE.fullmatch(text.strip())
        if length is None or length.group(2) not in S.LENGTH_UNITS:
            raise self.error(
                f"`{text.strip()}` is not a length",
                call.start,
                self.i,
                "write a number and a unit: 1em, 3mu",
            )
        ems = float(length.group(1)) * S.LENGTH_UNITS[length.group(2)]
        out = ""
        for width, char in S.UNICODE_SPACES:
            while ems >= width - 1 / 36:
                out += char
                ems -= width
        return out

    def delimiter(self, start: int) -> str:
        """The delimiter after \\left, \\middle, \\right or \\big: ( [ \\{ | . and friends."""
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

    # --- sequences ------------------------------------------------------------------------

    def sequence(
        self,
        stops: frozenset[Stop],
        *,
        align: bool = False,
        opening: tuple[int, int, str] | None = None,
    ) -> tuple[list[Node], Stop]:
        """Nodes until one of `stops`. Returns the stop found, not consumed except & and
        \\\\. With `align`, & is an alignment point. A group switch (\\color, \\over) takes
        the rest of the sequence."""
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
            if c == "}" and "}" in stops:
                return out, "}"
            if c == "]" and "]" in stops:
                return out, "]"
            if c == "$" and "$" in stops:
                return out, "$"
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
                if name in _CLOSERS:
                    for stop in stops:
                        if stop == name:
                            return out, stop
                    raise self.unmatched(name, self.i, after)
                if name in self.switches:
                    start, self.i = self.i, after
                    combine = self.switches[name](self, Call(name, start, after))
                    rest, stop = self.sequence(stops, align=align, opening=opening)
                    return combine(out, tuple(rest)), stop
            out.extend(self.atom(stops))

    def unmatched(self, name: str, start: int, end: int) -> MathError:
        opener = "begin" if name == "end" else "left"
        return self.error(f"`\\{name}` without a matching `\\{opener}`", start, end)

    # --- atoms -----------------------------------------------------------------------------

    def atom(self, stops: frozenset[Stop] = frozenset()) -> list[Node]:
        """A primary with its scripts; big operators and functions take their operand."""
        self.skip_space()
        grouped = self.peek() == "{"  # {...}^2: the whole group is the base, as in LaTeX
        if self.peek() and self.peek() in "^_'":
            base: list[Item] = [Run("")]
        else:
            base = self.primary()
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
            arg = self.math_arg()
            if c == "^":
                if sup is not None and not primed:
                    raise self.error("double superscript: use braces, x^{a^b}", start)
                sup = (*(sup or ()), *arg)
                primed = False
            else:
                if sub is not None:
                    raise self.error("double subscript: use braces, x_{a_b}", start)
                sub = arg
        if isinstance(head, PendingNary):
            return [self.nary(replace(head, sub=sub, sup=sup), stops)]
        if isinstance(head, PendingFunc):
            return [self.func(head, sub, sup, stops)]
        nodes = [n for n in base if not isinstance(n, PendingNary | PendingFunc)]
        if sub is None and sup is None:
            return nodes
        if grouped:
            return [Scripts(tuple(nodes), sub, sup)]
        if isinstance(head, GroupChr) and (
            (head.pos == "top" and sup) or (head.pos == "bot" and sub)
        ):
            label = sup if head.pos == "top" else sub
            limit = Limit("upp" if head.pos == "top" else "low", (head,), label or ())
            rest_sub, rest_sup = (sub, None) if head.pos == "top" else (None, sup)
            return (
                [limit] if not (rest_sub or rest_sup) else [Scripts((limit,), rest_sub, rest_sup)]
            )
        return [*nodes[:-1], Scripts((nodes[-1],), sub, sup)]

    def operand(self, stops: frozenset[Stop]) -> Row:
        """What a big operator or function applies to: atoms up to a relation, + or -, or
        a group switch, which takes the rest of the group."""
        nodes: list[Node] = []
        while True:
            self.skip_space()
            c = self.peek()
            if (
                not c
                or c in "}]&"
                or (c == "$" and "$" in stops)
                or (c == "\\" and self.command_at(self.i)[0] in ("\\", *_CLOSERS, *self.switches))
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
        return tuple(nodes)

    def nary(self, pending: PendingNary, stops: frozenset[Stop]) -> Nary:
        return Nary(pending.chr, pending.sub, pending.sup, self.operand(stops), pending.lim_loc)

    def func(
        self, pending: PendingFunc, sub: Row | None, sup: Row | None, stops: frozenset[Stop]
    ) -> Node:
        name: Row = pending.name
        if sub is not None and pending.limits:
            name = (Limit("low", name, sub),)
            sub = None
        if sub is not None or sup is not None:
            name = (Scripts(name, sub, sup),)
        return Func(name, self.operand(stops))

    def primary(self) -> list[Item]:
        """One letter, number, character or command, without its scripts."""
        self.skip_space()
        c = self.peek()
        if c == "{":
            return list(self.math_arg())
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
            return [Run({"-": "−", "~": "\N{NO-BREAK SPACE}", "*": "∗"}.get(c, c))]
        start = self.i
        name, self.i = self.command_at(start)
        handler = self.commands.get(name)
        if handler is not None:
            return handler(self, Call(name, start, self.i))
        if name in self.switches:
            raise self.error(
                f"`\\{name}` applies to the rest of a group: put it inside `{{...}}`",
                start,
                self.i,
            )
        if name in _CLOSERS:
            raise self.unmatched(name, start, self.i)
        close = difflib.get_close_matches(name, self.known, n=1)
        raise self.error(
            f"unknown math command `\\{name}`",
            start,
            self.i,
            f"did you mean `\\{close[0]}`?" if close else "",
        )

    def end_environment(self, name: str, start: int) -> None:
        if self.peek_command() != "end":
            raise self.error(f"`\\begin{{{name}}}` is not closed", start)
        self.take_command()
        closing = self.raw_arg().strip()
        if closing != name:
            raise self.error(
                f"`\\begin{{{name}}}` is closed by `\\end{{{closing}}}`", start, self.i
            )
