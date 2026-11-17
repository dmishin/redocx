"""The redocx language frontend: source -> CST -> AST, with diagnostics."""

from __future__ import annotations

from dataclasses import dataclass

from redocx.syntax import ast
from redocx.syntax.cst import Node
from redocx.syntax.diagnostics import Diagnostic, render_all
from redocx.syntax.parser import parse_cst
from redocx.syntax.source import Source


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
    """Parse redocx source text."""
    if isinstance(source, str):
        source = Source(name, source)
    cst, diagnostics = parse_cst(source)
    ordered = sorted(diagnostics, key=lambda d: (d.span.start, d.span.end))
    return ParseResult(source, cst, ast.build(cst), tuple(ordered))


__all__ = ["ParseResult", "Source", "parse"]
