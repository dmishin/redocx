"""The document intermediate representation (IR).

A Word-shaped, immutable model of a document. The language frontend (later) and
the Python API both produce it; the OOXML writer consumes it. It covers metadata,
document settings, styles (incl. linked styles), multilevel numbering definitions,
a single section and paragraphs of text runs.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import Enum

from wordgenx.props import EMPTY_PARAGRAPH, EMPTY_RUN, PropertySet, Target, para_props, run_props
from wordgenx.values import Length

# Defaults: A4 with 1-inch margins, as in a new Word document outside North America.
_A4_WIDTH = Length.mm(210)
_A4_HEIGHT = Length.mm(297)
_ONE_INCH = Length.cm("2.54")
_HEADER_FOOTER_DISTANCE = Length.cm("1.25")
_NO_GUTTER = Length(0)
_DEFAULT_TAB_STOP = Length.cm("1.27")


@dataclass(frozen=True)
class Meta:
    """Core document properties (File > Info in Word)."""

    title: str | None = None
    subject: str | None = None
    author: str | None = None
    keywords: tuple[str, ...] = ()
    description: str | None = None
    last_modified_by: str | None = None
    created: datetime | None = None
    """Written only when given, so builds stay reproducible."""
    modified: datetime | None = None
    language: str | None = None


@dataclass(frozen=True)
class Settings:
    """Document-wide settings (settings.xml)."""

    default_tab_stop: Length = _DEFAULT_TAB_STOP
    update_fields_on_open: bool = False
    even_and_odd_headers: bool = False


class StyleKind(Enum):
    PARAGRAPH = "paragraph"
    CHARACTER = "character"
    TABLE = "table"
    """Only Word's built-in "Table Grid" so far; custom table styles arrive in M2."""


@dataclass(frozen=True)
class Style:
    kind: StyleKind
    name: str
    """Display name, e.g. ``Heading 1`` or ``Body Text``."""
    based_on: str | None = None
    next: str | None = None
    paragraph: PropertySet = EMPTY_PARAGRAPH
    run: PropertySet = EMPTY_RUN
    quick_format: bool = True
    """Show in the Styles gallery."""
    ui_priority: int | None = None
    link: str | None = None
    """The linked style of the other kind (a paragraph style and its "... Char" style)."""
    numbering: NumberingRef | None = None
    """Paragraphs in this style are numbered by this list level (heading numbering)."""

    def __post_init__(self) -> None:
        if self.paragraph.target is not Target.PARAGRAPH or self.run.target is not Target.RUN:
            raise ValueError("Style.paragraph / Style.run have the wrong property targets")
        if self.kind is StyleKind.CHARACTER and (self.paragraph or self.numbering):
            raise ValueError(f"character style {self.name!r} cannot have paragraph properties")
        if self.kind is StyleKind.TABLE and self.name != "Table Grid":
            raise ValueError("only the built-in table style 'Table Grid' is supported so far")


# --- numbering (lists) -------------------------------------------------------------

NUMBER_FORMATS = frozenset(
    "decimal upperRoman lowerRoman upperLetter lowerLetter ordinal cardinalText ordinalText "
    "decimalZero bullet none hex chicago ideographDigital japaneseCounting aiueo iroha "
    "decimalFullWidth decimalHalfWidth japaneseLegal decimalEnclosedCircle decimalEnclosedFullstop "
    "decimalEnclosedParen numberInDash russianLower russianUpper hebrew1 hebrew2 arabicAlpha "
    "arabicAbjad hindiVowels hindiConsonants hindiNumbers thaiLetters thaiNumbers "
    "chineseCounting koreanCounting vietnameseCounting".split()
)
"""ST_NumberFormat values supported (Word's common formats)."""

_LEVEL_PLACEHOLDER = re.compile(r"%(\d)")


@dataclass(frozen=True)
class NumberingLevel:
    """One level of a multilevel list (w:lvl)."""

    level: int
    """1-9."""
    format: str = "decimal"
    """An ST_NumberFormat value, e.g. ``decimal``, ``lowerLetter``, ``bullet``."""
    text: str = ""
    """The marker template: ``%1.%2`` for numbers, the character itself for bullets."""
    start: int = 1
    style: str | None = None
    """Paragraph style linked to this level."""
    legal: bool = False
    """Show all levels as decimal numbers (legal numbering, w:isLgl)."""
    suffix: str = "tab"
    """What follows the marker: ``tab``, ``space`` or ``nothing``."""
    restart_after: int | None = None
    """Restart after this higher level (w:lvlRestart); None restarts after any higher level."""
    marker_align: str = "left"
    paragraph: PropertySet = EMPTY_PARAGRAPH
    """Paragraph properties of numbered paragraphs (mostly indents)."""
    run: PropertySet = EMPTY_RUN
    """Formatting of the marker."""

    def __post_init__(self) -> None:
        if not 1 <= self.level <= 9:
            raise ValueError(f"list level must be 1-9, got {self.level}")
        if self.format not in NUMBER_FORMATS:
            raise ValueError(f"unknown number format {self.format!r}")
        for placeholder in _LEVEL_PLACEHOLDER.findall(self.text):
            if not 1 <= int(placeholder) <= self.level:
                raise ValueError(
                    f"level {self.level}: %{placeholder} in {self.text!r} refers to a deeper level"
                )
        if self.suffix not in ("tab", "space", "nothing"):
            raise ValueError(f"suffix must be tab, space or nothing, got {self.suffix!r}")
        if self.marker_align not in ("left", "center", "right"):
            raise ValueError(
                f"marker alignment must be left, center or right: {self.marker_align!r}"
            )
        if self.restart_after is not None and not 0 <= self.restart_after < self.level:
            raise ValueError(
                f"level {self.level}: restart-after must be a higher level (0-{self.level - 1})"
            )
        if self.start < 0:
            raise ValueError(f"start must not be negative, got {self.start}")


@dataclass(frozen=True)
class Numbering:
    """A named multilevel list definition (w:abstractNum)."""

    name: str
    levels: tuple[NumberingLevel, ...]
    """Defined levels; the writer fills the remaining levels of the nine with defaults."""

    def __post_init__(self) -> None:
        numbers = [lvl.level for lvl in self.levels]
        if len(numbers) != len(set(numbers)):
            raise ValueError(f"numbering {self.name!r} defines a level twice")

    def level(self, number: int) -> NumberingLevel | None:
        return next((lvl for lvl in self.levels if lvl.level == number), None)


@dataclass(frozen=True)
class NumberingRef:
    name: str
    level: int = 1


@dataclass(frozen=True)
class Run:
    text: str
    """Text; ``\\t`` becomes a tab and ``\\n`` a line break."""
    props: PropertySet = EMPTY_RUN
    style: str | None = None


@dataclass(frozen=True)
class Break:
    """A page or column break inside a paragraph (line breaks are ``\\n`` in run text)."""

    kind: str
    """``page`` or ``column``."""

    def __post_init__(self) -> None:
        if self.kind not in ("page", "column"):
            raise ValueError(f"break kind must be page or column, got {self.kind!r}")


@dataclass(frozen=True)
class Hyperlink:
    """An external hyperlink around runs."""

    url: str
    content: tuple[Run, ...]


IMAGE_FORMATS = {"png": "png", "jpeg": "jpeg", "gif": "gif", "bmp": "bmp"}
"""Image formats Word embeds directly -> file extension."""


@dataclass(frozen=True)
class Image:
    """An inline picture (w:drawing/wp:inline)."""

    data: bytes
    format: str
    """A key of IMAGE_FORMATS."""
    width: Length
    height: Length
    description: str = ""
    """Alternative text."""
    name: str = ""
    """Original file name (shown in Word's selection pane)."""

    def __post_init__(self) -> None:
        if self.format not in IMAGE_FORMATS:
            raise ValueError(f"unsupported image format {self.format!r}")
        if self.width.emu <= 0 or self.height.emu <= 0:
            raise ValueError("image size must be positive")


Inline = Run | Break | Hyperlink | Image


@dataclass(frozen=True)
class ListItemRef:
    """A paragraph's place in a list: which list (instance) and which level."""

    numbering: str
    """The numbering definition's name."""
    instance: int
    """Separate lists of one definition count separately; equal instances continue."""
    level: int = 1
    start: int | None = None
    """Where the instance starts counting (its first level); None uses the definition."""
    continuation: bool = False
    """A further paragraph of the item: indented like the item's text, not numbered.
    Resolved to plain indentation before writing."""


@dataclass(frozen=True)
class Paragraph:
    content: tuple[Inline, ...] = ()
    style: str | None = None
    props: PropertySet = EMPTY_PARAGRAPH
    list_item: ListItemRef | None = None
    unnumbered: bool = False
    """Suppress numbering that the paragraph style would apply (``=*`` headings)."""

    def runs(self) -> tuple[Run, ...]:
        """All runs, including those inside hyperlinks."""
        out: list[Run] = []
        for item in self.content:
            if isinstance(item, Run):
                out.append(item)
            elif isinstance(item, Hyperlink):
                out.extend(item.content)
        return tuple(out)


# --- tables ------------------------------------------------------------------------------


@dataclass(frozen=True)
class Cell:
    content: tuple[Block, ...] = ()
    colspan: int = 1
    rowspan: int = 1
    shading: str | None = None
    """Background as RRGGBB."""
    valign: str | None = None
    """``top``, ``center`` or ``bottom``."""

    def __post_init__(self) -> None:
        if self.colspan < 1 or self.rowspan < 1:
            raise ValueError("colspan and rowspan must be at least 1")
        if self.valign not in (None, "top", "center", "bottom"):
            raise ValueError(f"valign must be top, center or bottom, got {self.valign!r}")


@dataclass(frozen=True)
class Row:
    cells: tuple[Cell, ...]
    header: bool = False
    """Repeat at the top of each page (w:tblHeader)."""
    height: Length | None = None
    exact_height: bool = False
    """Exactly `height` (otherwise at least `height`)."""
    cant_split: bool = False


@dataclass(frozen=True)
class Table:
    rows: tuple[Row, ...]
    widths: tuple[Length, ...]
    """One width per grid column."""
    style: str | None = "Table Grid"
    align: str | None = None
    """``left``, ``center`` or ``right`` (the table's position)."""

    def __post_init__(self) -> None:
        if not self.widths or any(w.emu <= 0 for w in self.widths):
            raise ValueError("a table needs positive column widths")
        if self.align not in (None, "left", "center", "right"):
            raise ValueError(f"table align must be left, center or right, got {self.align!r}")
        table_layout(self)  # raises ValueError when spans do not fit the grid


Block = Paragraph | Table


@dataclass(frozen=True)
class Slot:
    """One grid position's cell in a row: the cell itself, or the continuation of a rowspan."""

    cell: Cell
    column: int
    continues: bool
    """True for positions covered by a cell spanning from a row above (w:vMerge continue)."""


def table_layout(table: Table) -> list[list[Slot]]:
    """Place cells on the grid, resolving colspans and rowspans.

    Each row must cover every grid column exactly once.
    """
    columns = len(table.widths)
    covered: dict[tuple[int, int], Cell] = {}
    layout: list[list[Slot]] = []

    def take_covered(r: int, column: int, slots: list[Slot]) -> int:
        """Emit continuation slots for positions covered from above; return the next free column."""
        while (r, column) in covered:
            origin = covered[(r, column)]
            slots.append(Slot(origin, column, continues=True))
            column += origin.colspan
        return column

    for r, row in enumerate(table.rows):
        slots: list[Slot] = []
        column = 0
        for cell in row.cells:
            column = take_covered(r, column, slots)
            if column + cell.colspan > columns:
                raise ValueError(
                    f"row {r + 1} has more columns than the table's {columns}"
                    + (" (check colspan)" if cell.colspan > 1 else "")
                )
            if r + cell.rowspan > len(table.rows):
                raise ValueError(f"row {r + 1}: rowspan {cell.rowspan} goes past the last row")
            slots.append(Slot(cell, column, continues=False))
            for below in range(r + 1, r + cell.rowspan):
                for c in range(column, column + cell.colspan):
                    if (below, c) in covered:
                        raise ValueError(f"row {below + 1}: overlapping rowspans")
                covered[(below, column)] = cell
                for c in range(column + 1, column + cell.colspan):
                    covered[(below, c)] = cell  # marks the span; only the first column emits a slot
            column += cell.colspan
        column = take_covered(r, column, slots)
        if column != columns:
            raise ValueError(f"row {r + 1} covers {column} of the table's {columns} columns")
        layout.append(slots)
    return layout


def iter_paragraphs(blocks: tuple[Block, ...]) -> Iterator[Paragraph]:
    """Every paragraph, including those inside table cells."""
    for block in blocks:
        if isinstance(block, Paragraph):
            yield block
        else:
            for row in block.rows:
                for cell in row.cells:
                    yield from iter_paragraphs(cell.content)


def iter_tables(blocks: tuple[Block, ...]) -> Iterator[Table]:
    """Every table, including nested ones."""
    for block in blocks:
        if isinstance(block, Table):
            yield block
            for row in block.rows:
                for cell in row.cells:
                    yield from iter_tables(cell.content)


def map_paragraphs(
    blocks: tuple[Block, ...], fn: Callable[[Paragraph], Paragraph]
) -> tuple[Block, ...]:
    """`blocks` with `fn` applied to every paragraph, including those inside tables."""
    out: list[Block] = []
    for block in blocks:
        if isinstance(block, Paragraph):
            out.append(fn(block))
        else:
            rows = tuple(
                replace(
                    row,
                    cells=tuple(
                        replace(c, content=map_paragraphs(c.content, fn)) for c in row.cells
                    ),
                )
                for row in block.rows
            )
            out.append(replace(block, rows=rows))
    return tuple(out)


@dataclass(frozen=True)
class PageSetup:
    width: Length = _A4_WIDTH
    height: Length = _A4_HEIGHT
    margin_top: Length = _ONE_INCH
    margin_bottom: Length = _ONE_INCH
    margin_left: Length = _ONE_INCH
    margin_right: Length = _ONE_INCH
    header_distance: Length = _HEADER_FOOTER_DISTANCE
    footer_distance: Length = _HEADER_FOOTER_DISTANCE
    gutter: Length = _NO_GUTTER

    @property
    def landscape(self) -> bool:
        return self.width > self.height


@dataclass(frozen=True)
class Document:
    body: tuple[Block, ...] = ()
    meta: Meta = field(default_factory=Meta)
    settings: Settings = field(default_factory=Settings)
    styles: tuple[Style, ...] = ()
    numbering: tuple[Numbering, ...] = ()
    default_run: PropertySet = EMPTY_RUN
    """Document defaults for runs (docDefaults/rPrDefault)."""
    default_paragraph: PropertySet = EMPTY_PARAGRAPH
    """Document defaults for paragraphs (docDefaults/pPrDefault)."""
    page: PageSetup = field(default_factory=PageSetup)


# --- convenience constructors for the Python API ----------------------------------


def run(text: str, *, style: str | None = None, **props: object) -> Run:
    """A run of text: ``run("bold", bold=True)``."""
    return Run(text, run_props(**props), style)


def paragraph(*content: str | Run, style: str | None = None, **props: object) -> Paragraph:
    """A paragraph: ``paragraph("Hello ", run("world", bold=True), align="center")``."""
    runs = tuple(Run(item) if isinstance(item, str) else item for item in content)
    return Paragraph(runs, style, para_props(**props))


def paragraph_style(
    name: str,
    *,
    based_on: str | None = "Normal",
    next: str | None = None,
    paragraph: PropertySet = EMPTY_PARAGRAPH,
    run: PropertySet = EMPTY_RUN,
) -> Style:
    if name == "Normal":
        based_on = None
    return Style(StyleKind.PARAGRAPH, name, based_on, next, paragraph, run)


def character_style(name: str, *, based_on: str | None = None, **props: object) -> Style:
    return Style(StyleKind.CHARACTER, name, based_on, run=run_props(**props))
