"""The wordgenx language frontend: source -> CST -> AST, with diagnostics."""

from __future__ import annotations

from dataclasses import dataclass

from wordgenx.syntax import ast
from wordgenx.syntax.cst import Node
from wordgenx.syntax.diagnostics import Diagnostic, render_all
from wordgenx.syntax.parser import parse_cst
from wordgenx.syntax.source import Source


@dataclass(frozen=True)
class ParseResult:
    source: Source
    cst: Node
    ast: ast.Document
    diagnostics: tuple[Diagnostic, ...]

    @property
    def ok(self) -> bool:
        """True when there are no errors (warnings are allowed)."""
        return not any(d.is_error for d in self.diagnostics)

    def render_diagnostics(self) -> str:
        return render_all(self.diagnostics, self.source)


def parse(source: Source | str, name: str = "<input>") -> ParseResult:
    """Parse wordgenx source text."""
    if isinstance(source, str):
        source = Source(name, source)
    cst, diagnostics = parse_cst(source)
    ordered = sorted(diagnostics, key=lambda d: (d.span.start, d.span.end))
    return ParseResult(source, cst, ast.build(cst), tuple(ordered))


__all__ = ["ParseResult", "Source", "parse"]
