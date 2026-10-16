"""Tables: pipe tables, \\table, \\row and \\cell (design §3.8)."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from decimal import Decimal
from typing import TYPE_CHECKING, cast

from wordgenx import ir
from wordgenx.commands.bind import Args
from wordgenx.commands.builtin import COMMANDS
from wordgenx.commands.types import ColumnWidth, Fields, RowHeight
from wordgenx.lower.common import BlockHandler, Format, is_blank, markup
from wordgenx.lower.media import floating
from wordgenx.props import EMPTY_CELL, EMPTY_TABLE, PropertySet, Target
from wordgenx.syntax import ast
from wordgenx.syntax.diagnostics import Code
from wordgenx.syntax.source import Span
from wordgenx.values import Length, TabStop

if TYPE_CHECKING:
    from wordgenx.lower.lowering import Lowering

CELL_MARGINS = Length.twips(2 * 108)
"""Word's default left + right cell margins (Normal Table)."""
DECIMAL_ROOM = Length.cm("0.6")
"""Room right of a decimal tab for the decimals."""


@dataclass
class TableSpec:
    """Table options from \\table[...] (defaults for a stand-alone pipe table)."""

    style: str | None = "Table Grid"
    style_span: Span | None = None
    widths: tuple[ColumnWidth, ...] = ()
    header_rows: int = 0
    align: str | None = None
    columns: list[str] = field(default_factory=lambda: [])
    """Paragraph alignment per column (left, center, right, justify, decimal)."""
    props: PropertySet = EMPTY_TABLE
    look: frozenset[str] | None = None
    anchor: ir.Anchor | None = None
    anchor_span: Span | None = None
    decimal_tabs: dict[int, Length] = field(default_factory=lambda: {})
    """Column -> decimal tab position given as (decimal, tab=...)."""


@dataclass
class CellSpec:
    content: list[ir.Block]
    span: Span
    colspan: int = 1
    rowspan: int = 1
    props: PropertySet = EMPTY_CELL
    align: str | None = None


@dataclass
class RowSpec:
    cells: list[CellSpec]
    span: Span
    header: bool = False
    height: RowHeight | None = None
    cant_split: bool = False


def pipe_table(lw: Lowering, block: ast.PipeTable) -> list[ir.Block]:
    """A pipe table on its own: a table with the defaults."""
    table = _make_table(lw, TableSpec(), _pipe_rows(lw, block), block.span)
    return [table] if table is not None else []


def table(lw: Lowering, command: ast.Command, args: Args) -> list[ir.Block]:
    lw.has_content = True
    spec = _table_spec(lw, args)
    rows: list[RowSpec] = []
    for block in markup(command):
        if isinstance(block, ast.PipeTable):
            rows.extend(_pipe_rows(lw, block))
        elif isinstance(block, ast.Paragraph):
            for item in lw.expand(block.content):
                if isinstance(item, ast.Command) and item.name == "row":
                    rows.append(_row(lw, item))
                elif not is_blank(item):
                    _misplaced(lw, item.span)
        else:
            _misplaced(lw, block.span)
    made = _make_table(lw, spec, rows, command.span)
    return [made] if made is not None else []


def _table_anchor(lw: Lowering, anchor: ir.Anchor, span: Span) -> ir.Anchor | None:
    """A floating table's position; what Word cannot do with a table is reported."""
    if anchor.wrap != "square" or anchor.wrap_side != "both":
        lw.report(
            Code.INVALID_ARGUMENT,
            "a floating table always has text around it",
            span,
            help="leave out wrap and wrap-side; use a text box for other wrapping",
        )
        return None
    if anchor.x_from not in ir.TABLE_H_ANCHORS or anchor.y_from not in ir.TABLE_V_ANCHORS:
        lw.report(
            Code.INVALID_ARGUMENT,
            "a floating table is placed against the text, the margin or the page",
            span,
            help="x=(..., from=column|margin|page), y=(..., from=paragraph|margin|page)",
        )
        return None
    return anchor


def _misplaced(lw: Lowering, span: Span) -> None:
    lw.report(
        Code.MISPLACED,
        "a \\table contains pipe rows (| a | b |) or \\row commands",
        span,
        help="put other content inside a \\cell",
    )


def _table_spec(lw: Lowering, args: Args) -> TableSpec:
    look = cast("frozenset[str] | None", args.get("look", frozenset))
    spec = TableSpec(props=args.props or EMPTY_TABLE, look=look)
    if (style := args.text("style")) is not None:
        spec.style = None if style == "none" else style
        spec.style_span = args.span_of("style")
    spec.header_rows = args.number("header-rows", 0) or 0
    spec.widths = args.items("widths", ColumnWidth)
    spec.align = args.text("align")
    spec.anchor = floating(args)
    spec.anchor_span = args.span
    for index, column in enumerate(args.items("columns", Fields)):
        alignment = column.text("align") or "left"
        if (tab := column.get("tab", Length)) is not None:
            if alignment != "decimal":
                lw.report(
                    Code.INVALID_ARGUMENT,
                    "a tab position is only for decimal columns: (decimal, tab=1.8cm)",
                    column.span_of("tab"),
                )
            spec.decimal_tabs[index] = tab
        spec.columns.append(alignment)
    return spec


def _pipe_rows(lw: Lowering, block: ast.PipeTable) -> list[RowSpec]:
    rows: list[RowSpec] = []
    for row in block.rows:
        cells: list[CellSpec] = []
        covered = 0
        for pipe_cell in row.cells:
            content = lw.expand(pipe_cell.content)
            text = [c for c in content if not is_blank(c)]
            if covered:
                covered -= 1
                if text:
                    lw.report(
                        Code.INVALID_ARGUMENT,
                        "this cell is covered by a colspan and must be empty",
                        pipe_cell.span,
                    )
                continue
            if len(text) == 1 and isinstance(text[0], ast.Command) and text[0].name == "cell":
                cell = _cell(lw, text[0])
                covered = cell.colspan - 1
            elif text:
                pieces = lw.spliced_inlines(content, Format(), pipe_cell.span)
                cell = CellSpec([ir.Paragraph(lw.assemble(pieces))], pipe_cell.span)
            else:
                cell = CellSpec([], pipe_cell.span)
            cells.append(cell)
        rows.append(RowSpec(cells, row.span))
    return rows


def _row(lw: Lowering, command: ast.Command) -> RowSpec:
    args = lw.bind(command, COMMANDS["row"])
    row = RowSpec(
        [],
        command.span,
        header=args.flag("header"),
        height=args.get("height", RowHeight),
        cant_split=args.flag("cant-split"),
    )
    for block in markup(command):
        if not isinstance(block, ast.Paragraph):
            lw.report(Code.MISPLACED, "a \\row contains \\cell commands", block.span)
            continue
        for item in lw.expand(block.content):
            if isinstance(item, ast.Command) and item.name == "cell":
                row.cells.append(_cell(lw, item))
            elif not is_blank(item):
                lw.report(
                    Code.MISPLACED,
                    "a \\row contains \\cell commands",
                    item.span,
                    help="put the text inside \\cell{...}",
                )
    return row


def _cell(lw: Lowering, command: ast.Command) -> CellSpec:
    args = lw.bind(command, COMMANDS["cell"])
    props = args.props or EMPTY_CELL
    for key, prop in (("shade", "shading"), ("valign", "valign")):
        if key in args:
            props = props.merged(PropertySet(Target.CELL, {prop: args.values[key]}))
    cell = CellSpec(
        [],
        command.span,
        colspan=args.number("colspan", 1) or 1,
        rowspan=args.number("rowspan", 1) or 1,
        props=props,
        align=args.text("align"),
    )
    if command.body is not None:
        with lw.contained():
            cell.content = lw.blocks(markup(command))
    return cell


def _make_table(lw: Lowering, spec: TableSpec, rows: list[RowSpec], span: Span) -> ir.Table | None:
    if not rows:
        lw.report(Code.INVALID_ARGUMENT, "a table needs at least one row", span)
        return None
    repeat_header = False
    if spec.style is not None:
        lw.use_style(spec.style, ir.StyleKind.TABLE, spec.style_span or span)
        style = lw.compiled_style(spec.style)
        repeat_header = style is not None and style.repeat_header
    # Each row's extent on an unbounded grid (rowspans from rows above take positions).
    covered: set[tuple[int, int]] = set()
    extents: list[int] = []
    for r, row in enumerate(rows):
        column = 0
        for cell in row.cells:
            while (r, column) in covered:
                column += 1
            for below in range(r + 1, r + cell.rowspan):
                covered.update((below, c) for c in range(column, column + cell.colspan))
            column += cell.colspan
        while (r, column) in covered:
            column += 1
        extents.append(column)
    columns = len(spec.widths) if spec.widths else max(extents)
    if spec.widths and max(extents) > columns:
        lw.report(
            Code.INVALID_ARGUMENT,
            f"the table has {max(extents)} columns but widths lists {columns}",
            span,
        )
        return None
    for row, extent in zip(rows, extents, strict=True):  # short rows get empty cells
        row.cells.extend(CellSpec([], row.span) for _ in range(columns - extent))

    widths = _resolve_widths(lw, spec, columns, span)
    if widths is None:
        return None
    ir_rows: list[ir.Row] = []
    for r, row in enumerate(rows):
        column = 0
        cells: list[ir.Cell] = []
        header = row.header or r < spec.header_rows or (repeat_header and r == 0)
        for cell in row.cells:
            align = cell.align or (spec.columns[column] if column < len(spec.columns) else None)
            content = tuple(cell.content)
            if align == "decimal" and (header or cell.colspan > 1):
                align = "right"  # headings and merged cells have no figures to line up
            if align == "decimal":
                tab = spec.decimal_tabs.get(column) or _decimal_tab(widths[column])
                content = _decimal_paragraphs(content, tab)
            elif align is not None:
                content = _align_paragraphs(content, align)
            cells.append(ir.Cell(content, cell.colspan, cell.rowspan, cell.props))
            column += cell.colspan
        height, exact = (row.height.height, row.height.exact) if row.height else (None, False)
        ir_rows.append(ir.Row(tuple(cells), header, height, exact, row.cant_split))
    try:
        look = ir.DEFAULT_TABLE_LOOK if spec.look is None else spec.look
        if spec.anchor is not None:
            spec.anchor = _table_anchor(lw, spec.anchor, spec.anchor_span or span)
        return ir.Table(
            tuple(ir_rows), widths, spec.style, spec.align, spec.props, look, spec.anchor
        )
    except ValueError as e:
        lw.report(Code.INVALID_ARGUMENT, str(e), span, help="check colspan and rowspan")
        return None


def _resolve_widths(
    lw: Lowering, spec: TableSpec, columns: int, span: Span
) -> tuple[Length, ...] | None:
    available = lw.text_width().emu
    entries = spec.widths or (ColumnWidth("fraction", Decimal(1)),) * columns
    fixed = 0
    fractions = Decimal(0)
    for width in entries:
        if isinstance(width.amount, Length):
            fixed += width.amount.emu
        elif width.kind == "percent":
            fixed += int(available * width.amount / 100)
        else:
            fractions += width.amount
    remaining = available - fixed
    if fractions and remaining <= 0:
        lw.report(
            Code.INVALID_ARGUMENT, "fixed column widths leave no room for the 1fr columns", span
        )
        return None
    widths: list[Length] = []
    for width in entries:
        if isinstance(width.amount, Length):
            widths.append(width.amount)
        elif width.kind == "percent":
            widths.append(Length(int(available * width.amount / 100)))
        else:
            widths.append(Length(int(remaining * width.amount / fractions)))
    if any(w.emu <= 0 for w in widths):
        lw.report(Code.INVALID_ARGUMENT, "column widths must be positive", span)
        return None
    return tuple(widths)


def _decimal_tab(column: Length) -> Length:
    """Where the decimal points of a column line up: near the right, leaving room for
    two or three decimals."""
    inner = column.emu - CELL_MARGINS.emu
    return Length(max(inner - DECIMAL_ROOM.emu, inner // 2))


def _decimal_paragraphs(blocks: tuple[ir.Block, ...], tab: Length) -> tuple[ir.Block, ...]:
    """A decimal tab stop on each paragraph; in a table cell Word lines text up on it
    without a tab character."""
    props = PropertySet(Target.PARAGRAPH, {"align": "left", "tabs": (TabStop(tab, "decimal"),)})

    def apply(paragraph: ir.Paragraph) -> ir.Paragraph:
        if "align" in paragraph.props or "tabs" in paragraph.props:
            return paragraph
        return replace(paragraph, props=props.merged(paragraph.props))

    return ir.map_paragraphs(blocks, apply)


def _align_paragraphs(blocks: tuple[ir.Block, ...], align: str) -> tuple[ir.Block, ...]:
    """Apply a column/cell alignment to paragraphs that do not set their own."""
    aligned = PropertySet(Target.PARAGRAPH, {"align": align})

    def apply(paragraph: ir.Paragraph) -> ir.Paragraph:
        if "align" in paragraph.props:
            return paragraph
        return replace(paragraph, props=aligned.merged(paragraph.props))

    return ir.map_paragraphs(blocks, apply)


BLOCKS: dict[str, BlockHandler] = {"table": table}
