"""Sections, headers and footers: \\section, \\header, \\footer (design §3.10)."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING, Any

from wordgenx import ir
from wordgenx.commands.bind import Args
from wordgenx.commands.types import Fields, PageBorderSpec, PageSize
from wordgenx.lower.common import BlockHandler, markup
from wordgenx.syntax import ast
from wordgenx.syntax.diagnostics import Code
from wordgenx.syntax.source import Span
from wordgenx.values import Length

if TYPE_CHECKING:
    from wordgenx.lower.lowering import Lowering

MARGINS = {
    "top": ("margin_top",),
    "bottom": ("margin_bottom",),
    "left": ("margin_left",),
    "right": ("margin_right",),
    "inside": ("margin_left",),
    "outside": ("margin_right",),
    "all": ("margin_top", "margin_bottom", "margin_left", "margin_right"),
}
"""margins=(...) field -> ir.PageSetup fields."""


def section(lw: Lowering, command: ast.Command, args: Args) -> list[ir.Block]:
    """Page setup of the current section; after content, also a section break."""
    if lw.container_depth > 0:
        lw.report(
            Code.MISPLACED,
            "\\section cannot appear inside a table, header, footer or note",
            command.span,
        )
        return []
    out: list[ir.Block] = []
    breaking = lw.has_content
    if breaking:
        out.append(ir.SectionBreak(lw.section))
        # A new section keeps the page setup; headers and footers are inherited (not
        # repeated), page numbering continues unless restarted, and a different first
        # page applies only where it is asked for.
        lw.section = replace(
            lw.section,
            headers=(),
            footers=(),
            page_number_start=None,
            start="next-page",
            title_page=False,
        )
    s = lw.section
    page = s.page
    if args.flag("title-page"):
        s = replace(s, title_page=True)
    if args.flag("mirror"):
        lw.settings = replace(lw.settings, mirror_margins=True)
    if args.flag("separator"):
        s = replace(s, column_separator=True)
    if (size := args.get("size", PageSize)) is not None:
        page = replace(page, width=size.width, height=size.height)
    if (margins := args.record("margins")) is not None:
        page = _margins(lw, page, margins)
    for key in ("gutter", "header-distance", "footer-distance"):
        if (length := args.get(key, Length)) is not None:
            page = replace(page, **{key.replace("-", "_"): length})
    if (start := args.text("break")) is not None:
        s = replace(s, start=start)
    if (numbers := args.record("page-numbers")) is not None:
        s = replace(
            s,
            page_number_format=numbers.text("format", s.page_number_format),
            page_number_start=numbers.number("start", s.page_number_start),
        )
    if (columns := args.record("columns")) is not None:
        widths = columns.items("widths", Length)
        count = columns.number("count", len(widths) or s.columns) or s.columns
        if widths and count != len(widths):
            lw.report(
                Code.INVALID_ARGUMENT,
                f"{count} columns but {len(widths)} widths",
                columns.span_of("widths"),
                help="give one width per column, or leave out count",
            )
            widths = ()
        s = replace(
            s,
            columns=count,
            column_gap=columns.get("gap", Length, s.column_gap) or s.column_gap,
            column_separator=s.column_separator or columns.flag("separator"),
            column_widths=widths,
        )
        total = sum(w.emu for w in widths) + s.column_gap.emu * (len(widths) - 1)
        if widths and total > (page.width.emu - page.margin_left.emu - page.margin_right.emu):
            lw.warn(
                Code.INVALID_ARGUMENT,
                "the columns and gaps are wider than the text",
                columns.span_of("widths"),
                help="Word narrows them to fit",
            )
    if (valign := args.text("valign")) is not None:
        s = replace(s, vertical_align=valign)
    if "rtl" in args:
        s = replace(s, rtl=args.flag("rtl"))
    if (direction := args.text("text-direction")) is not None:
        s = replace(s, text_direction=direction)
    if (borders := args.get("borders", PageBorderSpec)) is not None:
        s = replace(s, borders=_page_borders(lw, borders, s.borders, args.span_of("borders")))
    if "line-numbers" in args:
        numbering = args.record("line-numbers")
        s = replace(s, line_numbers=_line_numbers(numbering) if numbering is not None else None)
    orientation = args.text("orientation")
    if orientation is not None and (orientation == "landscape") != page.landscape:
        page = replace(page, width=page.height, height=page.width)
    if breaking and not args.get("link-headers", bool, True):
        # Unlinked: every kind defined so far gets an explicit empty part (unless redefined).
        s = replace(s, headers=_empty_parts(lw, "header"), footers=_empty_parts(lw, "footer"))
    lw.section = replace(s, page=page)
    return out


def _margins(lw: Lowering, page: ir.PageSetup, margins: Fields) -> ir.PageSetup:
    for side, value in margins.values.items():  # in the order given: all=..., then sides
        assert isinstance(value, Length)
        if side in ("inside", "outside"):  # facing pages: Word's mirror margins
            lw.settings = replace(lw.settings, mirror_margins=True)
        page = replace(page, **dict.fromkeys(MARGINS[side], value))
    return page


def _page_borders(
    lw: Lowering, spec: PageBorderSpec, current: ir.PageBorders | None, span: Span
) -> ir.PageBorders | None:
    """The section's page borders, replacing the ones it inherited (`current`). Sides
    that are `none` have no border; with no side left, there are no page borders."""
    sides = {name: b for name, b in spec.sides.items() if b.style != "nil"}
    if not sides:
        return None
    try:
        return ir.PageBorders(
            **sides, offset_from=spec.offset_from or "page", pages=spec.pages or "all"
        )
    except ValueError as e:
        lw.report(Code.INVALID_ARGUMENT, str(e), span)
        return current


def _line_numbers(fields: Fields) -> ir.LineNumbers:
    """line-numbers=on or (count-by=5, start=1, distance=0.5cm, restart=each-page)."""
    values: dict[str, Any] = {
        name.replace("-", "_"): value for name, value in fields.values.items()
    }
    return ir.LineNumbers(**values)


def _empty_parts(lw: Lowering, family: str) -> ir.HeaderFooterParts:
    kinds = [k.split(":")[1] for k in lw.header_kinds if k.startswith(family + ":")]
    return tuple((kind, ()) for kind in ir.HEADER_KINDS if kind in kinds)


def header_footer(lw: Lowering, command: ast.Command, args: Args) -> list[ir.Block]:
    """\\header[kind]{...} and \\footer[kind]{...} for the current section."""
    family = command.name
    kind = args.text("kind", "default")
    assert kind is not None
    with lw.contained(part=family):
        blocks = lw.blocks(markup(command))
    style = "Header" if family == "header" else "Footer"
    lw.use_style(style, ir.StyleKind.PARAGRAPH, command.span)
    content = ir.map_paragraphs(tuple(blocks), lambda p: p if p.style else replace(p, style=style))
    existing = lw.section.headers if family == "header" else lw.section.footers
    parts = dict(existing)
    parts[kind] = content
    ordered = tuple((k, parts[k]) for k in ir.HEADER_KINDS if k in parts)
    s = replace(lw.section, **{f"{family}s": ordered})
    if kind == "first":
        s = replace(s, title_page=True)
    if kind == "even":
        lw.even_headers = True
    lw.header_kinds.add(f"{family}:{kind}")
    lw.section = s
    return []


BLOCKS: dict[str, BlockHandler] = {
    "section": section,
    "header": header_footer,
    "footer": header_footer,
}
