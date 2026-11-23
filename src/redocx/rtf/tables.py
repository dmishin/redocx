r"""RTF tables: row definitions, borders, and the table built from rows of cells.

An RTF table is not a group. A row is a definition (`\trowd`, then each cell's properties
ended by `\cellxN`, its right edge) and paragraphs marked `\intbl`, each cell ended by
`\cell` and the row by `\row`. The grid comes from the right edges of all rows.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from redocx import ir
from redocx.props import PropertySet, Target
from redocx.values import Border, Color, InvalidValueError, Length

BORDER_STYLES = {
    "brdrs": "single",
    "brdrth": "thick",
    "brdrsh": "single",
    "brdrdb": "double",
    "brdrdot": "dotted",
    "brdrdash": "dashed",
    "brdrhair": "single",
    "brdrdashsm": "dashSmallGap",
    "brdrdashd": "dotDash",
    "brdrdashdd": "dotDotDash",
    "brdrdashdotstr": "dashDotStroked",
    "brdrtriple": "triple",
    "brdrtnthsg": "thinThickSmallGap",
    "brdrthtnsg": "thickThinSmallGap",
    "brdrtnthtnsg": "thinThickThinSmallGap",
    "brdrtnthmg": "thinThickMediumGap",
    "brdrthtnmg": "thickThinMediumGap",
    "brdrtnthtnmg": "thinThickThinMediumGap",
    "brdrtnthlg": "thinThickLargeGap",
    "brdrthtnlg": "thickThinLargeGap",
    "brdrtnthtnlg": "thinThickThinLargeGap",
    "brdrwavy": "wave",
    "brdrwavydb": "doubleWave",
    "brdremboss": "threeDEmboss",
    "brdrengrave": "threeDEngrave",
    "brdroutset": "outset",
    "brdrinset": "inset",
    "brdrnone": "nil",
    "brdrnil": "nil",
}
r"""RTF border line words -> ST_Border styles."""

CELL_BORDERS = {"clbrdrt": "top", "clbrdrl": "left", "clbrdrb": "bottom", "clbrdrr": "right"}
ROW_BORDERS = {
    "trbrdrt": "top",
    "trbrdrl": "left",
    "trbrdrb": "bottom",
    "trbrdrr": "right",
    "trbrdrh": "inside-h",
    "trbrdrv": "inside-v",
}
_PADDING = {"trpaddl": "left", "trpaddr": "right", "trpaddt": "top", "trpaddb": "bottom"}
_CELL_VALIGN = {"clvertalt": "top", "clvertalc": "center", "clvertalb": "bottom"}


@dataclass
class BorderSpec:
    r"""A border being read: the `\brdr*` words after `\clbrdrt`, `\brdrb`, ..."""

    style: str = "single"
    width: int = 10
    """Twips (`\\brdrwN`)."""
    color: int = 0
    """Index into the colour table (`\\brdrcfN`); 0 is auto."""
    space: int = 0
    """Twips (`\\brspN`)."""

    def word(self, name: str, param: int | None) -> bool:
        if name in BORDER_STYLES:
            self.style = BORDER_STYLES[name]
        elif name == "brdrw" and param is not None:
            self.width = param
        elif name == "brdrcf" and param is not None:
            self.color = param
        elif name == "brsp" and param is not None:
            self.space = param
        else:
            return False
        return True

    def border(self, colors: list[Color]) -> Border:
        if self.style == "nil":
            return Border("nil")
        color = colors[self.color] if 0 < self.color < len(colors) else Color()
        width = max(5, min(240, self.width))  # Word's 1/4 pt to 12 pt
        try:
            return Border(self.style, Length.twips(width), color, Length.twips(max(0, self.space)))
        except InvalidValueError:
            return Border()


@dataclass
class CellDef:
    r"""A cell's properties from a row definition, up to its `\cellx`."""

    right: int = 0
    h_merge: Literal["first", "next"] | None = None
    v_merge: Literal["first", "next"] | None = None
    borders: dict[str, BorderSpec] = field(default_factory=dict[str, BorderSpec])
    shading: int = 0
    """Colour table index (`\\clcbpatN`); 0 is none."""
    valign: str | None = None


@dataclass
class RowDef:
    r"""A row definition (`\trowd ...`), with the cell being defined."""

    cells: list[CellDef] = field(default_factory=list[CellDef])
    pending: CellDef = field(default_factory=CellDef)
    left: int = 0
    gap: int | None = None
    header: bool = False
    height: int = 0
    keep: bool = False
    align: str | None = None
    borders: dict[str, BorderSpec] = field(default_factory=dict[str, BorderSpec])
    padding: dict[str, int] = field(default_factory=dict[str, int])

    def word(self, name: str, param: int | None) -> BorderSpec | bool:
        """Take a control word of the row definition. A border word returns the border
        that the `\\brdr*` words after it describe."""
        value = param if param is not None else 0
        cell = self.pending
        if name == "cellx":
            cell.right = value
            self.cells.append(cell)
            self.pending = CellDef()
        elif name in CELL_BORDERS:
            return cell.borders.setdefault(CELL_BORDERS[name], BorderSpec())
        elif name in ROW_BORDERS:
            return self.borders.setdefault(ROW_BORDERS[name], BorderSpec())
        elif name == "clmgf":
            cell.h_merge = "first"
        elif name == "clmrg":
            cell.h_merge = "next"
        elif name == "clvmgf":
            cell.v_merge = "first"
        elif name == "clvmrg":
            cell.v_merge = "next"
        elif name == "clcbpat":
            cell.shading = value
        elif name in _CELL_VALIGN:
            cell.valign = _CELL_VALIGN[name]
        elif name == "trleft":
            self.left = value
        elif name == "trgaph":
            self.gap = value
        elif name == "trhdr":
            self.header = True
        elif name == "trrh":
            self.height = value
        elif name == "trkeep":
            self.keep = True
        elif name in ("trql", "trqc", "trqr"):
            self.align = {"trql": "left", "trqc": "center", "trqr": "right"}[name]
        elif name in _PADDING:
            self.padding[_PADDING[name]] = value
        else:
            return False
        return True


@dataclass
class _Cell:
    content: list[ir.Block]
    definition: CellDef
    start: int = 0
    span: int = 1
    rowspan: int = 1
    covered: bool = False
    """Continues the cell above (vertical merge): not written."""


@dataclass
class TableBuilder:
    """The rows of a table being read."""

    rows: list[tuple[RowDef, list[list[ir.Block]]]] = field(
        default_factory=list[tuple[RowDef, list[list[ir.Block]]]]
    )
    cells: list[list[ir.Block]] = field(default_factory=list[list[ir.Block]])
    content: list[ir.Block] = field(default_factory=list[ir.Block])

    def end_cell(self) -> None:
        self.cells.append(self.content)
        self.content = []

    def end_row(self, definition: RowDef) -> None:
        if self.content:
            self.end_cell()
        self.rows.append((definition, self.cells))
        self.cells = []

    @property
    def empty(self) -> bool:
        return not self.rows and not self.cells and not self.content

    def build(self, colors: list[Color], default: RowDef) -> ir.Table | None:
        """The table; None when it has no cells."""
        if self.cells or self.content:
            self.end_row(self.rows[-1][0] if self.rows else default)
        pairs = [(d, _cells(d, contents)) for d, contents in self.rows]
        pairs = [(d, cells) for d, cells in pairs if cells]
        if not pairs:
            return None
        rows = [cells for _, cells in pairs]
        left = min(d.left for d, _ in pairs)
        edges = sorted({left} | {c.definition.right for row in rows for c in row})
        column = {edge: i for i, edge in enumerate(edges)}
        columns = len(edges) - 1
        for row in rows:
            start = 0
            for cell in row:
                end = column[cell.definition.right]
                cell.start, cell.span = start, max(1, end - start)
                start = cell.start + cell.span
            if row and start < columns:  # a shorter row: its last cell reaches the edge
                row[-1].span += columns - start
        _merge(rows)
        widths = tuple(Length.twips(edges[i + 1] - edges[i]) for i in range(columns))
        first = pairs[0][0]
        table_props: dict[str, object] = {
            f"border-{side}": spec.border(colors) for side, spec in first.borders.items()
        }
        gap = first.gap or 0  # without \trgaph, text starts at the cell's edge
        for side in ("left", "right"):
            margin = first.padding.get(side, gap)
            table_props[f"cell-margin-{side}"] = Length.twips(max(0, margin))
        for side in ("top", "bottom"):
            if side in first.padding:
                table_props[f"cell-margin-{side}"] = Length.twips(max(0, first.padding[side]))
        indent = left + gap
        if indent:
            table_props["table-indent"] = Length.twips(indent)
        ir_rows = tuple(_row(definition, cells, colors) for definition, cells in pairs)
        return ir.Table(
            ir_rows,
            widths,
            style=None,
            align=first.align,
            props=PropertySet(Target.TABLE, table_props),
            look=frozenset(),
        )


def _cells(definition: RowDef, contents: list[list[ir.Block]]) -> list[_Cell]:
    """The row's cells, each with its definition. Extra cells get a column of their own;
    cells merged into the one before them (`\\clmrg`) join it."""
    defs = list(definition.cells)
    right = defs[-1].right if defs else definition.left
    while len(defs) < len(contents):
        right += 1440
        defs.append(CellDef(right=right))
    cells: list[_Cell] = []
    previous = definition.left
    for i, cell_def in enumerate(defs):
        if cell_def.right <= previous:  # edges must grow from left to right
            cell_def.right = previous + 1
        previous = cell_def.right
        content = contents[i] if i < len(contents) else []
        if cell_def.h_merge == "next" and cells:
            cells[-1].definition = _with_right(cells[-1].definition, cell_def.right)
            continue
        cells.append(_Cell(content, cell_def))
    return cells


def _with_right(cell: CellDef, right: int) -> CellDef:
    return CellDef(right, cell.h_merge, cell.v_merge, cell.borders, cell.shading, cell.valign)


def _merge(rows: list[list[_Cell]]) -> None:
    r"""Vertical merges: a `\clvmrg` cell below a cell of the same columns continues it."""
    above: dict[tuple[int, int], _Cell] = {}
    for row in rows:
        current: dict[tuple[int, int], _Cell] = {}
        for cell in row:
            key = (cell.start, cell.span)
            origin = above.get(key)
            if cell.definition.v_merge == "next" and origin is not None:
                origin.rowspan += 1
                cell.covered = True
                current[key] = origin
            elif cell.definition.v_merge is not None:
                current[key] = cell
        above = current


def _row(definition: RowDef, cells: list[_Cell], colors: list[Color]) -> ir.Row:
    out: list[ir.Cell] = []
    for cell in cells:
        if cell.covered:
            continue
        cell_def = cell.definition
        values: dict[str, object] = {
            f"border-{side}": spec.border(colors) for side, spec in cell_def.borders.items()
        }
        if 0 < cell_def.shading < len(colors) and colors[cell_def.shading].rgb:
            values["shading"] = colors[cell_def.shading]
        if cell_def.valign:
            values["valign"] = cell_def.valign
        content = tuple(cell.content) or (ir.Paragraph(),)
        out.append(ir.Cell(content, cell.span, cell.rowspan, PropertySet(Target.CELL, values)))
    height = definition.height
    return ir.Row(
        tuple(out),
        header=definition.header,
        height=Length.twips(abs(height)) if height else None,
        exact_height=height < 0,
        cant_split=definition.keep,
    )
