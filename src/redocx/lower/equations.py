r"""Equations: \m, \math and $$ display math (design §3.14)."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from redocx import ir
from redocx.commands.bind import Args
from redocx.lower.common import BlockHandler, Format, InlineHandler, Piece
from redocx.math.nodes import Raw, walk
from redocx.math.nodes import Row as MathRow
from redocx.math.parser import MathError, parse_math
from redocx.syntax import ast
from redocx.syntax.diagnostics import Code
from redocx.syntax.source import Span

if TYPE_CHECKING:
    from redocx.lower.lowering import Lowering


def inline_math(lw: Lowering, command: ast.Command, args: Args, fmt: Format) -> list[Piece]:
    text, start = _formula(lw, command)
    expr = _parse(lw, text, start, command.span)
    return [Piece("", fmt, "field", math=ir.Math(expr))] if expr is not None else []


def display_command(lw: Lowering, command: ast.Command, args: Args) -> list[ir.Block]:
    r"""\math[id=eq:x, numbered|nonumber]{formula}: a display equation."""
    label = args.text("id")
    numbered = args.get("numbered", bool)
    if numbered is None and args.flag("nonumber"):
        numbered = False
    text, start = _formula(lw, command)
    labelled = (label, args.span_of("id")) if label is not None else None
    return _display(lw, text, start, command.span, labelled, numbered)


def display_block(lw: Lowering, block: ast.DisplayMath) -> list[ir.Block]:
    """$$ ... $$, labelled ones with {#eq:x}."""
    start = lw.source_of(block.span).text.find(block.source, block.span.start)
    label = (block.label.name, block.label.span) if block.label is not None else None
    return _display(lw, block.source, start, block.span, label, None)


def _display(
    lw: Lowering,
    text: str,
    start: int,
    span: Span,
    label: tuple[str, Span] | None,
    numbered: bool | None,
) -> list[ir.Block]:
    """A display equation in a paragraph of its own; labelled ones are numbered."""
    lw.has_content = True
    expr = _parse(lw, text, start, span)
    if expr is None:
        return []
    numbered = label is not None if numbered is None else numbered
    bookmark = None
    if label is not None:
        if not numbered:
            lw.report(Code.MATH, "a labelled equation must be numbered", label[1])
        bookmark = lw.register_label(label[0], label[1], "equation").bookmark
    math = ir.Math(expr, True, numbered, None, bookmark, lw.caption_chapters)
    return [ir.Paragraph((math,))]


def _formula(lw: Lowering, command: ast.Command) -> tuple[str, int]:
    """The formula of \\m{...} or \\math{...} and where it starts in the source."""
    if not isinstance(command.body, ast.VerbatimBody) or not command.body.text.strip():
        return "", -1
    text = command.body.text
    return text, lw.source_of(command.body.span).text.find(text, command.body.span.start)


def _parse(lw: Lowering, text: str, start: int, span: Span) -> MathRow | None:
    if not text.strip():
        return None
    try:
        expr = parse_math(text)
    except MathError as e:
        where = replace(span, start=start + e.start, end=start + e.end) if start >= 0 else span
        lw.report(Code.MATH, e.message, where, e.hint)
        return None
    ok = True
    for node in walk(expr):
        if isinstance(node, Raw) and (problem := _raw_problem(node.xml)) is not None:
            where = replace(span, start=start + node.start, end=start + node.end)
            lw.report(Code.MATH, problem, where if start >= 0 else span, help=RAW_HELP)
            ok = False
    return expr if ok else None


RAW_HELP = (
    "\\ooxml holds OMML (m:) elements and is checked against the ECMA-376 schemas; "
    "elements of other namespaces are not checked"
)


def _raw_problem(xml: str) -> str | None:
    """Why the XML of `\\ooxml{...}` does not fit in an equation, or None."""
    from redocx.ooxml.validate import fragment_problems  # loads the schemas: only when used
    from redocx.ooxml.xml import parse_fragment

    try:
        parse_fragment(xml, math=True)
    except ValueError as e:
        return str(e)
    problems = fragment_problems(xml, "math")
    if not problems:
        return None
    more = f" (and {len(problems) - 1} more)" if len(problems) > 1 else ""
    return f"OMML that Word would reject: {problems[0]}{more}"


BLOCKS: dict[str, BlockHandler] = {"math": display_command}
INLINES: dict[str, InlineHandler] = {"m": inline_math}
