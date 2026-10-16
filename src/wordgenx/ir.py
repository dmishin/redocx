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
from typing import TYPE_CHECKING

from wordgenx.math.nodes import Row as MathRow
from wordgenx.math.nodes import linear_text
from wordgenx.props import (
    EMPTY_CELL,
    EMPTY_PARAGRAPH,
    EMPTY_RUN,
    EMPTY_TABLE,
    PropertySet,
    Target,
    para_props,
    run_props,
)
from wordgenx.values import Border, Color, Length

if TYPE_CHECKING:
    from wordgenx.ooxml.template import Template

# Defaults: A4 with 1-inch margins, as in a new Word document outside North America.
_A4_WIDTH = Length.mm(210)
_A4_HEIGHT = Length.mm(297)
_ONE_INCH = Length.cm("2.54")
_HEADER_FOOTER_DISTANCE = Length.cm("1.25")
_NO_GUTTER = Length(0)
_DEFAULT_TAB_STOP = Length.cm("1.27")


CustomValue = str | int | float | bool
"""A custom document property: text, number or yes/no."""


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
    custom: tuple[tuple[str, CustomValue], ...] = ()
    """Custom document properties (File > Info > Properties > Custom), in order."""

    def custom_value(self, name: str) -> CustomValue | None:
        return next((value for key, value in self.custom if key == name), None)


NOTE_NUMBER_FORMATS = (
    "decimal",
    "lowerRoman",
    "upperRoman",
    "lowerLetter",
    "upperLetter",
    "chicago",
)
NOTE_RESTARTS = ("continuous", "eachSect", "eachPage")
NOTE_POSITIONS = {
    "footnote": ("pageBottom", "beneathText"),
    "endnote": ("sectEnd", "docEnd"),
}


@dataclass(frozen=True)
class NoteSettings:
    """Numbering and placement of footnotes or endnotes (w:footnotePr / w:endnotePr).

    None means Word's default: footnotes 1, 2, 3 at the bottom of the page; endnotes i, ii,
    iii at the end of the document; numbering continuous.
    """

    format: str | None = None
    """A value of NOTE_NUMBER_FORMATS."""
    start: int | None = None
    restart: str | None = None
    """A value of NOTE_RESTARTS (eachPage only for footnotes)."""
    position: str | None = None
    """A value of NOTE_POSITIONS[kind]."""

    def number_format(self, kind: str) -> str:
        return self.format or ("decimal" if kind == "footnote" else "lowerRoman")


PROTECTION_MODES = {
    "read-only": "readOnly",
    "comments": "comments",
    "tracked-changes": "trackedChanges",
    "forms": "forms",
}
"""wordgenx protection mode -> w:documentProtection/@w:edit."""


@dataclass(frozen=True)
class Settings:
    """Document-wide settings (settings.xml)."""

    default_tab_stop: Length = _DEFAULT_TAB_STOP
    update_fields_on_open: bool = False
    even_and_odd_headers: bool = False
    footnotes: NoteSettings = field(default_factory=NoteSettings)
    endnotes: NoteSettings = field(default_factory=NoteSettings)
    mirror_margins: bool = False
    """Left and right margins are inside and outside (facing pages); Word sets this for the
    whole document."""
    protection: str | None = None
    """What editing is allowed: a value of PROTECTION_MODES (w:documentProtection)."""
    protection_enforced: bool = False
    page_color: Color | None = None
    """The colour of every page (w:background; Design > Page Color in Word)."""

    def notes(self, kind: str) -> NoteSettings:
        return self.footnotes if kind == "footnote" else self.endnotes


class StyleKind(Enum):
    PARAGRAPH = "paragraph"
    CHARACTER = "character"
    TABLE = "table"


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
    table: PropertySet = EMPTY_TABLE
    """Table styles: borders, cell margins, ... of the whole table (w:tblPr)."""
    cell: PropertySet = EMPTY_CELL
    """Table styles: shading, borders, ... of every cell (w:tcPr)."""
    conditions: tuple[TableCondition, ...] = ()
    """Table styles: formatting of the header row, banded rows, ... (w:tblStylePr)."""
    repeat_header: bool = False
    """Table styles: tables in this style repeat their first row on each page."""

    def __post_init__(self) -> None:
        if (
            self.paragraph.target is not Target.PARAGRAPH
            or self.run.target is not Target.RUN
            or self.table.target is not Target.TABLE
            or self.cell.target is not Target.CELL
        ):
            raise ValueError("Style property sets have the wrong targets")
        if self.kind is StyleKind.CHARACTER and (self.paragraph or self.numbering):
            raise ValueError(f"character style {self.name!r} cannot have paragraph properties")
        if self.kind is not StyleKind.TABLE and (
            self.table or self.cell or self.conditions or self.repeat_header
        ):
            raise ValueError(f"only table styles have table formatting ({self.name!r})")
        kinds = [c.kind for c in self.conditions]
        if len(set(kinds)) != len(kinds):
            raise ValueError(f"table style {self.name!r} formats a table part twice")


TABLE_CONDITIONS = (
    "firstRow lastRow firstCol lastCol band1Vert band2Vert band1Horz band2Horz "
    "neCell nwCell seCell swCell"
).split()
"""ST_TblStyleOverrideType values (wholeTable is the style itself), in the order Word writes."""


@dataclass(frozen=True)
class TableCondition:
    """Formatting a table style gives one part of the table (w:tblStylePr)."""

    kind: str
    """A value of TABLE_CONDITIONS."""
    paragraph: PropertySet = EMPTY_PARAGRAPH
    run: PropertySet = EMPTY_RUN
    cell: PropertySet = EMPTY_CELL

    def __post_init__(self) -> None:
        if self.kind not in TABLE_CONDITIONS:
            raise ValueError(f"unknown table style part {self.kind!r}")


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
    """A hyperlink around runs: to an external `url`, or to a bookmark (`anchor`)."""

    url: str
    content: tuple[Run | Field, ...]
    anchor: str | None = None
    """A bookmark name; when set, `url` is unused (an internal link, e.g. in a TOC)."""


@dataclass(frozen=True)
class BookmarkStart:
    """Start of a named range (w:bookmarkStart); cross-references point at bookmarks."""

    name: str


@dataclass(frozen=True)
class BookmarkEnd:
    name: str


IMAGE_FORMATS = {"png": "png", "jpeg": "jpeg", "gif": "gif", "bmp": "bmp"}
"""Image formats Word embeds directly -> file extension."""

WRAPS = {
    "square": "wrapSquare",
    "tight": "wrapTight",
    "top-bottom": "wrapTopAndBottom",
    "behind": "wrapNone",
    "front": "wrapNone",
}
"""How text flows around a floating picture -> its wp: wrap element."""
WRAP_SIDES = {"both": "bothSides", "left": "left", "right": "right", "largest": "largest"}
H_ANCHORS = {
    "margin": "margin",
    "page": "page",
    "column": "column",
    "character": "character",
    "left-margin": "leftMargin",
    "right-margin": "rightMargin",
    "inside-margin": "insideMargin",
    "outside-margin": "outsideMargin",
}
"""What a floating picture's x is measured from (wp:positionH/@relativeFrom)."""
V_ANCHORS = {
    "margin": "margin",
    "page": "page",
    "paragraph": "paragraph",
    "line": "line",
    "top-margin": "topMargin",
    "bottom-margin": "bottomMargin",
    "inside-margin": "insideMargin",
    "outside-margin": "outsideMargin",
}
H_ALIGNS = ("left", "center", "right", "inside", "outside")
V_ALIGNS = ("top", "center", "bottom", "inside", "outside")
_NO_OFFSET = Length(0)
_WRAP_DISTANCE = Length(114300)
"""Word's distance between a floating picture and the text beside it (0.32cm)."""


@dataclass(frozen=True)
class Anchor:
    """Where a floating picture sits and how text flows around it (wp:anchor)."""

    wrap: str = "square"
    """A key of WRAPS."""
    wrap_side: str = "both"
    """A key of WRAP_SIDES: where text goes beside a square or tight picture."""
    x: Length | str = _NO_OFFSET
    """An offset, or an alignment of H_ALIGNS."""
    x_from: str = "column"
    y: Length | str = _NO_OFFSET
    """An offset, or an alignment of V_ALIGNS."""
    y_from: str = "paragraph"
    distance: tuple[Length, Length, Length, Length] = (
        Length(0),
        Length(0),
        _WRAP_DISTANCE,
        _WRAP_DISTANCE,
    )
    """Space between the picture and the text: top, bottom, left, right."""

    def __post_init__(self) -> None:
        if self.wrap not in WRAPS:
            raise ValueError(f"wrap must be one of {', '.join(WRAPS)}")
        if self.wrap_side not in WRAP_SIDES:
            raise ValueError(f"the wrap side must be one of {', '.join(WRAP_SIDES)}")
        if self.x_from not in H_ANCHORS or self.y_from not in V_ANCHORS:
            raise ValueError("unknown anchor for the picture's position")
        if isinstance(self.x, str) and self.x not in H_ALIGNS:
            raise ValueError(f"x aligns {', '.join(H_ALIGNS)}")
        if isinstance(self.y, str) and self.y not in V_ALIGNS:
            raise ValueError(f"y aligns {', '.join(V_ALIGNS)}")


@dataclass(frozen=True)
class Crop:
    """How much is cut off each side of a picture, in thousandths of a percent (a:srcRect)."""

    left: int = 0
    top: int = 0
    right: int = 0
    bottom: int = 0

    def __post_init__(self) -> None:
        if min(self.left, self.top, self.right, self.bottom) < 0:
            raise ValueError("crop values cannot be negative")
        if self.left + self.right >= 100_000 or self.top + self.bottom >= 100_000:
            raise ValueError("cropping would leave nothing of the picture")


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
    svg: bytes | None = None
    """An SVG that Word 2016+ shows instead; `data` is then the fallback picture older
    readers show (an a:blip with an asvg:svgBlip extension)."""
    anchor: Anchor | None = None
    """Floating (wp:anchor) rather than inline in the text."""
    crop: Crop | None = None
    rotation: int = 0
    """Degrees clockwise."""
    border: Border | None = None
    """A line around the picture (a:ln)."""

    def __post_init__(self) -> None:
        if self.format not in IMAGE_FORMATS:
            raise ValueError(f"unsupported image format {self.format!r}")
        if self.width.emu <= 0 or self.height.emu <= 0:
            raise ValueError("image size must be positive")


@dataclass(frozen=True)
class Field:
    """A Word field (w:fldChar begin/separate/end), e.g. PAGE or STYLEREF "Heading 1".

    `instruction` alternates text and nested fields: ``IF { MERGEFIELD x } = 1 "y"``.
    """

    instruction: tuple[str | Field, ...]
    result: str = ""
    """The cached result Word shows until it updates the field."""
    props: PropertySet = EMPTY_RUN
    style: str | None = None
    dirty: bool = False
    """Ask Word to update the field when the document opens (result not known in advance)."""
    has_result: bool = True
    """False for fields that never show a result (XE, TC): no separate mark is written."""

    @property
    def code(self) -> str:
        """The instruction as Word displays it (Alt+F9), nested fields in braces."""
        return "".join(
            part if isinstance(part, str) else "{ " + part.code + " }" for part in self.instruction
        )


NOTE_KINDS = ("footnote", "endnote")


@dataclass(frozen=True)
class Note:
    """A footnote or endnote: the reference mark in the text, and the note's own content.

    Word numbers notes itself (w:footnoteReference has no cached text). The first paragraph
    of `content` starts with a NoteMark, as in a note Word inserts.
    """

    kind: str
    """footnote | endnote"""
    content: tuple[Block, ...]
    props: PropertySet = EMPTY_RUN
    style: str | None = None
    """Character style of the reference mark (Footnote Reference / Endnote Reference)."""

    def __post_init__(self) -> None:
        if self.kind not in NOTE_KINDS:
            raise ValueError(f"note kind must be footnote or endnote, got {self.kind!r}")


@dataclass(frozen=True)
class NoteMark:
    """The note's own number at the start of its text (w:footnoteRef / w:endnoteRef)."""

    kind: str
    style: str | None = None


@dataclass(frozen=True)
class RawXml:
    """Run-level WordprocessingML written by hand (\\raw; the escape hatch)."""

    xml: str


@dataclass(frozen=True)
class Math:
    """A formula (Office Math). Inline, or a display equation alone in its paragraph."""

    expr: MathRow
    display: bool = False
    numbered: bool = False
    number: str | None = None
    """The equation number, as resolved ("3.2"); Word shows it as (3.2)."""
    bookmark: str | None = None
    """Around the number, for references to the equation."""
    chapter: tuple[str, str] | None = None
    """(chapter style, separator) when equations are numbered per chapter."""


Inline = (
    Run
    | Break
    | Hyperlink
    | Image
    | Field
    | BookmarkStart
    | BookmarkEnd
    | Note
    | NoteMark
    | RawXml
    | Math
)


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
                out.extend(r for r in item.content if isinstance(r, Run))
        return tuple(out)

    def text(self) -> str:
        """The visible text: runs, and cached field results."""
        parts: list[str] = []
        for item in self.content:
            if isinstance(item, Run):
                parts.append(item.text)
            elif isinstance(item, Field):
                parts.append(item.result)
            elif isinstance(item, Math):
                parts.append(linear_text(item.expr))
            elif isinstance(item, Hyperlink):
                parts.extend(r.text if isinstance(r, Run) else r.result for r in item.content)
        return "".join(parts)


# --- tables ------------------------------------------------------------------------------


TABLE_LOOK = (
    "header-row",
    "last-row",
    "first-column",
    "last-column",
    "banded-rows",
    "banded-columns",
)
"""Table style options shown in Word's Table Design tab (w:tblLook)."""
DEFAULT_TABLE_LOOK = frozenset({"header-row", "first-column", "banded-rows"})
"""What Word turns on for a new table."""


@dataclass(frozen=True)
class Cell:
    content: tuple[Block, ...] = ()
    colspan: int = 1
    rowspan: int = 1
    props: PropertySet = EMPTY_CELL
    """Shading, borders, vertical alignment (registry target CELL)."""

    def __post_init__(self) -> None:
        if self.colspan < 1 or self.rowspan < 1:
            raise ValueError("colspan and rowspan must be at least 1")
        if self.props.target is not Target.CELL:
            raise ValueError("Cell.props must hold cell properties")


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
    props: PropertySet = EMPTY_TABLE
    """Borders and cell margins that override the table style (registry target TABLE)."""
    look: frozenset[str] = DEFAULT_TABLE_LOOK
    """Which parts of the table style apply (values of TABLE_LOOK)."""

    def __post_init__(self) -> None:
        if not self.widths or any(w.emu <= 0 for w in self.widths):
            raise ValueError("a table needs positive column widths")
        if self.align not in (None, "left", "center", "right"):
            raise ValueError(f"table align must be left, center or right, got {self.align!r}")
        if self.props.target is not Target.TABLE:
            raise ValueError("Table.props must hold table properties")
        if not self.look <= set(TABLE_LOOK):
            raise ValueError(f"unknown table look flags: {sorted(self.look - set(TABLE_LOOK))}")
        table_layout(self)  # raises ValueError when spans do not fit the grid


# --- sections ------------------------------------------------------------------------------


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

    @property
    def text_width(self) -> Length:
        return Length(
            self.width.emu - self.margin_left.emu - self.margin_right.emu - self.gutter.emu
        )


HEADER_KINDS = ("default", "first", "even")
SECTION_STARTS = {
    "next-page": "nextPage",
    "continuous": "continuous",
    "even-page": "evenPage",
    "odd-page": "oddPage",
    "next-column": "nextColumn",
}
PAGE_NUMBER_FORMATS = {
    "decimal": "decimal",
    "lower-roman": "lowerRoman",
    "upper-roman": "upperRoman",
    "lower-letter": "lowerLetter",
    "upper-letter": "upperLetter",
}
_DEFAULT_COLUMN_GAP = Length.cm("1.25")

HeaderFooterParts = tuple[tuple[str, tuple["Block", ...]], ...]
"""(kind, blocks) pairs; kinds a section does not list are inherited from the previous one."""


@dataclass(frozen=True)
class Section:
    page: PageSetup = field(default_factory=PageSetup)
    start: str = "next-page"
    """How the section starts (a key of SECTION_STARTS); ignored for the first section."""
    headers: HeaderFooterParts = ()
    footers: HeaderFooterParts = ()
    title_page: bool = False
    """Use the "first" header and footer on the first page (w:titlePg)."""
    page_number_format: str | None = None
    page_number_start: int | None = None
    columns: int = 1
    column_gap: Length = _DEFAULT_COLUMN_GAP
    column_separator: bool = False
    line_numbers: LineNumbers | None = None
    vertical_align: str | None = None
    """top | center | bottom | justify: where the text sits between the top and bottom margins."""
    borders: PageBorders | None = None
    """Lines around the pages of the section (w:pgBorders)."""

    def __post_init__(self) -> None:
        if self.vertical_align not in (None, *VERTICAL_ALIGNMENTS):
            raise ValueError(f"vertical alignment must be one of {', '.join(VERTICAL_ALIGNMENTS)}")
        if self.start not in SECTION_STARTS:
            raise ValueError(f"section start must be one of {', '.join(SECTION_STARTS)}")
        if self.page_number_format not in (None, *PAGE_NUMBER_FORMATS):
            raise ValueError(f"page number format must be one of {', '.join(PAGE_NUMBER_FORMATS)}")
        for kind, _ in (*self.headers, *self.footers):
            if kind not in HEADER_KINDS:
                raise ValueError(f"header/footer kind must be one of {', '.join(HEADER_KINDS)}")
        if not 1 <= self.columns <= 45:
            raise ValueError("columns must be 1-45")


VERTICAL_ALIGNMENTS = {"top": "top", "center": "center", "bottom": "bottom", "justify": "both"}
PAGE_BORDER_SIDES = ("top", "left", "bottom", "right")
PAGE_BORDER_PAGES = {"all": "allPages", "first": "firstPage", "not-first": "notFirstPage"}
PAGE_BORDER_OFFSETS = ("page", "text")
MAX_PAGE_BORDER_SPACE = Length.pt(31)
"""The largest distance Word allows between a page border and what it is measured from."""


@dataclass(frozen=True)
class PageBorders:
    """Page borders of a section: a line per side, measured from the page edge or the text."""

    top: Border | None = None
    left: Border | None = None
    bottom: Border | None = None
    right: Border | None = None
    offset_from: str = "page"
    """page | text: what each border's space is measured from."""
    pages: str = "all"
    """all | first | not-first (a key of PAGE_BORDER_PAGES)."""

    def __post_init__(self) -> None:
        if self.offset_from not in PAGE_BORDER_OFFSETS:
            raise ValueError("page borders are measured from the page or the text")
        if self.pages not in PAGE_BORDER_PAGES:
            raise ValueError(f"page borders apply to {', '.join(PAGE_BORDER_PAGES)} pages")
        for side in self.sides():
            if side[1].space.emu > MAX_PAGE_BORDER_SPACE.emu:
                raise ValueError("a page border is at most 31pt from the page edge or the text")

    def sides(self) -> list[tuple[str, Border]]:
        return [
            (name, border)
            for name in PAGE_BORDER_SIDES
            if (border := getattr(self, name)) is not None
        ]


LINE_NUMBER_RESTARTS = {
    "each-page": "newPage",
    "each-section": "newSection",
    "continuous": "continuous",
}


@dataclass(frozen=True)
class LineNumbers:
    """Line numbers in the margin (w:lnNumType)."""

    count_by: int = 1
    """Show every n-th number."""
    start: int = 1
    distance: Length | None = None
    """From the text; Word chooses when None."""
    restart: str = "each-page"
    """A key of LINE_NUMBER_RESTARTS."""

    def __post_init__(self) -> None:
        if self.restart not in LINE_NUMBER_RESTARTS:
            raise ValueError(f"line numbers restart {', '.join(LINE_NUMBER_RESTARTS)}")
        if self.count_by < 1 or self.start < 1:
            raise ValueError("line numbers count by and start at whole numbers of at least 1")


@dataclass(frozen=True)
class SectionBreak:
    """Ends a section in the body; `section` describes the content before it."""

    section: Section


@dataclass(frozen=True)
class FieldBlock:
    """A field whose result is whole paragraphs, such as a table of contents.

    The field starts in the first paragraph and ends in the last one.
    """

    field: Field
    """Instruction and flags; its `result` is unused."""
    content: tuple[Paragraph, ...]


@dataclass(frozen=True)
class RawBlock:
    """Block-level WordprocessingML (w:p, w:tbl, w:sdt) written by hand (\\raw)."""

    xml: str
    ends_with_table: bool = False
    """Word needs a paragraph between it and a following table."""


@dataclass(frozen=True)
class EmbeddedDocx:
    """Another .docx that Word merges into the body when it opens the file (w:altChunk)."""

    data: bytes
    name: str = ""
    """The file name, for messages."""


Block = Paragraph | Table | SectionBreak | FieldBlock | RawBlock | EmbeddedDocx


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


def _with_notes(paragraph: Paragraph) -> Iterator[Paragraph]:
    yield paragraph
    for item in paragraph.content:
        if isinstance(item, Note):
            yield from iter_paragraphs(item.content)


def iter_paragraphs(blocks: tuple[Block, ...]) -> Iterator[Paragraph]:
    """Every paragraph, including those inside table cells and notes (after the paragraph
    that refers to the note)."""
    for block in blocks:
        if isinstance(block, Paragraph):
            yield from _with_notes(block)
        elif isinstance(block, FieldBlock):
            for paragraph in block.content:
                yield from _with_notes(paragraph)
        elif isinstance(block, Table):
            for row in block.rows:
                for cell in row.cells:
                    yield from iter_paragraphs(cell.content)


def iter_notes(blocks: tuple[Block, ...]) -> Iterator[Note]:
    """Every note, in document order."""
    for paragraph in iter_paragraphs(blocks):
        yield from (item for item in paragraph.content if isinstance(item, Note))


def iter_tables(blocks: tuple[Block, ...]) -> Iterator[Table]:
    """Every table, including nested ones and those inside notes."""
    for block in blocks:
        if isinstance(block, Table):
            yield block
            for row in block.rows:
                for cell in row.cells:
                    yield from iter_tables(cell.content)
        elif isinstance(block, Paragraph):
            for item in block.content:
                if isinstance(item, Note):
                    yield from iter_tables(item.content)


def map_paragraphs(
    blocks: tuple[Block, ...], fn: Callable[[Paragraph], Paragraph]
) -> tuple[Block, ...]:
    """`blocks` with `fn` applied to every paragraph, including those inside tables."""
    out: list[Block] = []
    for block in blocks:
        if isinstance(block, Paragraph):
            out.append(_map_paragraph(block, fn))
        elif isinstance(block, SectionBreak):
            out.append(SectionBreak(map_section(block.section, fn)))
        elif isinstance(block, FieldBlock):
            out.append(replace(block, content=tuple(_map_paragraph(p, fn) for p in block.content)))
        elif isinstance(block, RawBlock | EmbeddedDocx):
            out.append(block)
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


def _map_paragraph(paragraph: Paragraph, fn: Callable[[Paragraph], Paragraph]) -> Paragraph:
    """`fn` applied to the paragraph, then to the paragraphs of its notes."""
    paragraph = fn(paragraph)
    if not any(isinstance(item, Note) for item in paragraph.content):
        return paragraph
    content = tuple(
        replace(item, content=map_paragraphs(item.content, fn)) if isinstance(item, Note) else item
        for item in paragraph.content
    )
    return replace(paragraph, content=content)


def map_section(section: Section, fn: Callable[[Paragraph], Paragraph]) -> Section:
    """`section` with `fn` applied to the paragraphs of its headers and footers."""
    return replace(
        section,
        headers=tuple((kind, map_paragraphs(blocks, fn)) for kind, blocks in section.headers),
        footers=tuple((kind, map_paragraphs(blocks, fn)) for kind, blocks in section.footers),
    )


THEME_COLOR_NAMES = {
    "dark1": "dk1",
    "light1": "lt1",
    "dark2": "dk2",
    "light2": "lt2",
    "text1": "dk1",
    "background1": "lt1",
    "text2": "dk2",
    "background2": "lt2",
    **{f"accent{i}": f"accent{i}" for i in range(1, 7)},
    "hyperlink": "hlink",
    "followed-hyperlink": "folHlink",
}
"""Theme colour names in style sheets -> colour scheme slots (text1 is dark1, ...)."""


@dataclass(frozen=True)
class ThemeChoice:
    """The document's own theme colours and fonts, over the template's or wordgenx's theme."""

    name: str | None = None
    colors: tuple[tuple[str, str], ...] = ()
    """(scheme slot such as accent1, RRGGBB) pairs."""
    major_font: str | None = None
    minor_font: str | None = None

    def __post_init__(self) -> None:
        slots = set(THEME_COLOR_NAMES.values())
        for slot, rgb in self.colors:
            if slot not in slots:
                raise ValueError(f"unknown theme colour slot {slot!r}")
            if not re.fullmatch(r"[0-9A-F]{6}", rgb):
                raise ValueError(f"theme colours are RRGGBB, got {rgb!r}")

    def merged(self, later: ThemeChoice) -> ThemeChoice:
        """This choice with `later`'s entries on top (a later style sheet wins)."""
        colors = dict(self.colors)
        colors.update(later.colors)
        return ThemeChoice(
            later.name or self.name,
            tuple(colors.items()),
            later.major_font or self.major_font,
            later.minor_font or self.minor_font,
        )


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
    section: Section = field(default_factory=lambda: Section())
    """The last (or only) section; earlier sections end at SectionBreak blocks in the body."""
    template: Template | None = None
    """The .dotx or .docx this document is compiled into (its styles, theme, headers, ...)."""
    theme: ThemeChoice | None = None
    """Theme colours and fonts the document sets (`theme { ... }` in a style sheet)."""

    @property
    def page(self) -> PageSetup:
        return self.section.page

    def sections(self) -> list[Section]:
        """All sections in order."""
        return [b.section for b in self.body if isinstance(b, SectionBreak)] + [self.section]

    def all_paragraphs(self) -> Iterator[Paragraph]:
        """Paragraphs of the body, table cells, and every header and footer."""
        yield from iter_paragraphs(self.body)
        for section in self.sections():
            for _, blocks in (*section.headers, *section.footers):
                yield from iter_paragraphs(blocks)

    def notes(self, kind: str) -> list[Note]:
        """The document's footnotes or endnotes, in order."""
        return [note for note in iter_notes(self.body) if note.kind == kind]

    def all_tables(self) -> Iterator[Table]:
        yield from iter_tables(self.body)
        for section in self.sections():
            for _, blocks in (*section.headers, *section.footers):
                yield from iter_tables(blocks)


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
