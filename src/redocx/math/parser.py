"""Parse the LaTeX math subset (design §3.14) into the math tree.

    parse_math(r"\\frac{a}{b} + \\sqrt{x}")  ->  (Frac(...), Run("+"), Rad(...))

Errors are MathError with the offending text's offsets, so the compiler can point at
them inside the document. The parser's machinery is in `core.py`, what each command
makes in `commands.py`.
"""

from __future__ import annotations

from redocx.math.commands import COMMANDS, KNOWN_COMMANDS, SWITCHES
from redocx.math.core import MathError, Parser
from redocx.math.nodes import EqArr, Row


def parse_math(text: str) -> Row:
    """The math tree of a formula. Rows separated by \\\\ at the top level become an
    equation array (as in a gathered display)."""
    return parse_formula(text)[0]


def parse_formula(text: str) -> tuple[Row, tuple[MathError, ...]]:
    """The math tree of a formula and the warnings about it (`parse_math`)."""
    parser = Parser(text, COMMANDS, SWITCHES, KNOWN_COMMANDS)
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


__all__ = ["KNOWN_COMMANDS", "MathError", "parse_formula", "parse_math"]
