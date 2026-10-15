"""The document intermediate representation (IR).

A Word-shaped, immutable model of a document. The language frontend (later) and
the Python API both produce it; the OOXML writer consumes it. M0 covers metadata,
document settings, styles, a single section and paragraphs of text runs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
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

    def __post_init__(self) -> None:
        if self.paragraph.target is not Target.PARAGRAPH or self.run.target is not Target.RUN:
            raise ValueError("Style.paragraph / Style.run have the wrong property targets")
        if self.kind is StyleKind.CHARACTER and self.paragraph:
            raise ValueError(f"character style {self.name!r} cannot have paragraph properties")


@dataclass(frozen=True)
class Run:
    text: str
    """Text; ``\\t`` becomes a tab and ``\\n`` a line break."""
    props: PropertySet = EMPTY_RUN
    style: str | None = None


@dataclass(frozen=True)
class Paragraph:
    content: tuple[Run, ...] = ()
    style: str | None = None
    props: PropertySet = EMPTY_PARAGRAPH


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
    body: tuple[Paragraph, ...] = ()
    meta: Meta = field(default_factory=Meta)
    settings: Settings = field(default_factory=Settings)
    styles: tuple[Style, ...] = ()
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
