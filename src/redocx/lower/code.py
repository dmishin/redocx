"""Fenced code blocks: highlighting, line numbers and listing captions (design §3.15)."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from redocx import ir
from redocx.commands.builtin import CODE_BLOCK
from redocx.highlight import highlight, suggest_language, theme_formatting
from redocx.lower.common import Format, Piece
from redocx.lower.xrefs import make_caption
from redocx.ooxml.style_names import style_id
from redocx.props import EMPTY_PARAGRAPH, PropertySet, Target
from redocx.syntax import ast
from redocx.syntax.diagnostics import Code
from redocx.syntax.source import Span
from redocx.values import Length

if TYPE_CHECKING:
    from redocx.lower.lowering import Lowering


def code_block(lw: Lowering, block: ast.CodeBlock) -> list[ir.Block]:
    """A fenced code block, highlighted for its language. Attributes:
    ```` ```python {#lst:x, caption="...", line-numbers, first-line=10, highlight=off} ````;
    a caption (or id) adds a Listing caption above it."""
    args = lw.bind(ast.Command(CODE_BLOCK.name, block.options, None, span=block.span), CODE_BLOCK)
    out: list[ir.Block] = []
    caption = args.text("caption")
    label = args.text("id")
    first_line = args.number("first-line")
    numbers = args.flag("line-numbers") or first_line is not None
    first_line = first_line if first_line is not None else 1
    if label is not None and caption is None:
        caption = ""
    if caption is not None:
        text = lw.assemble([Piece(caption, Format())])
        span = args.span_of("id") if label is not None else block.span
        out.append(make_caption(lw, "Listing", label, span, text, keep_with_next=True))
    lines: list[list[tuple[str, str | None]]] | None = None
    if block.language and args.values.get("highlight") is not False:
        lines = highlight(block.code, block.language)
        if lines is None:
            close = suggest_language(block.language)
            lw.warn(
                Code.INVALID_ARGUMENT,
                f"no highlighting for the language `{block.language}`; the code is shown plain",
                block.span,
                help=f"did you mean `{close}`?" if close else None,
            )
    if lines is None:
        lines = [[(line, None)] if line else [] for line in block.code.split("\n")]
    lw.use_style("Code Block", ir.StyleKind.PARAGRAPH, block.span)
    last = first_line + len(lines) - 1
    hang = _line_number_indent(lw, len(str(last))) if numbers else None
    for n, line in enumerate(lines, start=first_line):
        runs: list[ir.Run] = []
        for text, name in line:  # tokens left plain by the theme merge with their neighbours
            style = _code_style(lw, name, block.span)
            if runs and runs[-1].style == style:
                runs[-1] = replace(runs[-1], text=runs[-1].text + text)
            else:
                runs.append(ir.Run(text, style=style))
        props = EMPTY_PARAGRAPH
        if hang is not None:
            lw.use_style("Code Line Number", ir.StyleKind.CHARACTER, block.span)
            number = f"{n:>{len(str(last))}}"
            runs = [ir.Run(number, style="Code Line Number"), ir.Run("\t"), *runs]
            props = PropertySet(Target.PARAGRAPH, {"indent-left": hang, "hanging-indent": hang})
        out.append(ir.Paragraph(tuple(runs), "Code Block", props))
    return out


def _code_style(lw: Lowering, name: str | None, span: Span) -> str | None:
    """The style for a token: only one that formats something (in the theme, a style
    sheet or the template), so plain tokens stay plain runs."""
    if name is None:
        return None
    defined = style_id(name) in {style_id(s) for s in lw.stylesheets._styles} or (  # pyright: ignore[reportPrivateUsage]
        lw.template is not None and lw.template.style(name) is not None
    )
    if not defined and not theme_formatting(lw.code_theme, name):
        return None
    lw.use_style(name, ir.StyleKind.CHARACTER, span)
    return name


def _line_number_indent(lw: Lowering, digits: int) -> Length:
    """Where code starts after line numbers: room for the digits and a gap, in the
    code font (monospaced, about 0.6em per character)."""
    size = 9.5
    style = lw.compiled_style("Code Block")
    if style is not None and isinstance(value := style.run.get("size"), Length):
        size = value.emu / 12700
    return Length.pt(round((digits + 1.5) * 0.6 * size, 1))
