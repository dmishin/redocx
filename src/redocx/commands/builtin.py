"""The built-in commands of the language: every command, its options and its body.

This table is the single definition of what the language accepts (language design §3).
The parser takes body kinds from it, the lowering binds every command against it, and
documentation or editor support can be generated from it.
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal
from typing import Any

from redocx import ir
from redocx.commands.spec import POSTPONED, CommandSpec, OptionSpec, Placement
from redocx.commands.types import (
    Bool,
    Choice,
    ColumnWidthValue,
    CustomPropertyValue,
    FlagSet,
    Int,
    LengthValue,
    LevelRange,
    ListOf,
    MarkupValue,
    PageBordersValue,
    PageSize,
    PageSizeValue,
    PercentValue,
    PositionValue,
    PropValue,
    Record,
    RowHeightValue,
    SeqRestart,
    SizeValue,
    TabStopValue,
    Text,
    Toggle,
    ValueType,
)
from redocx.props import Target
from redocx.syntax.commands import ArgKind
from redocx.values import Length

BLOCK, INLINE, BOTH = Placement.BLOCK, Placement.INLINE, Placement.BOTH
STRING, RAW, MATH = ArgKind.STRING, ArgKind.RAW, ArgKind.MATH


def _themes() -> Sequence[str]:
    from redocx.highlight import themes  # Pygments is loaded only when a theme is checked

    return themes()


# --- value vocabularies ----------------------------------------------------------------

ALIGNMENTS = ("left", "center", "right", "justify", "distribute")
PAGE_SIZES = {
    "A3": PageSize(Length.mm(297), Length.mm(420)),
    "A4": PageSize(Length.mm(210), Length.mm(297)),
    "A5": PageSize(Length.mm(148), Length.mm(210)),
    "Letter": PageSize(Length.inch("8.5"), Length.inch(11)),
    "Legal": PageSize(Length.inch("8.5"), Length.inch(14)),
}
NOTE_FORMATS = {**ir.PAGE_NUMBER_FORMATS, "symbols": "chicago"}
"""Note number format -> w:numFmt."""
NOTE_RESTARTS = {"continuous": "continuous", "each-section": "eachSect", "each-page": "eachPage"}
NOTE_POSITIONS = {
    "footnote": {"page-bottom": "pageBottom", "beneath-text": "beneathText"},
    "endnote": {"section-end": "sectEnd", "document-end": "docEnd"},
}
SEQ_FORMATS = {
    "decimal": "ARABIC",
    "lower-letter": "alphabetic",
    "upper-letter": "ALPHABETIC",
    "lower-roman": "roman",
    "upper-roman": "ROMAN",
}
"""Counter format -> SEQ \\* switch."""
REF_FORMS = ("default", "number", "text", "full", "page", "above-below", "relative")
CITE_LOCATORS = {
    "page": "p.", "pages": "pp.", "chapter": "ch.", "section": "sec.", "figure": "fig.",
    "table": "tab.", "paragraph": "para.", "line": "l.", "volume": "vol.", "note": "n.",
    "equation": "eq.", "verse": "v.", "column": "col.", "part": "pt.", "book": "bk.",
}  # fmt: skip
"""CSL locator labels -> the abbreviation narrative citations use."""
DATE_FIELDS = {"today": "DATE", "saved": "SAVEDATE", "created": "CREATEDATE"}
MARGIN_SIDES = ("top", "bottom", "left", "right", "inside", "outside")
CELL_VALIGN = Choice.of("top", "center", "bottom", aliases={"middle": "center"})
CAPTION_SEPARATORS = (".", "-", ":", chr(0x2013), chr(0x2014))  # en and em dash


def _note_settings(kind: str) -> Record:
    restarts = {k: v for k, v in NOTE_RESTARTS.items() if kind == "footnote" or k != "each-page"}
    return Record(
        {
            "format": Choice(NOTE_FORMATS),
            "restart": Choice(restarts),
            "position": Choice(NOTE_POSITIONS[kind]),
            "start": Int(min=1),
        },
        shorthand="format",
    )


def _o(name: str, kind: ValueType, doc: str = "", *, required: bool = False) -> OptionSpec:
    return OptionSpec(name, kind, doc, required)


TEXT = Text()
FLAG = Bool()
ID = _o("id", TEXT, "a label to refer to this with @label")

_SPECS: list[CommandSpec] = []


def _command(name: str, placement: Placement, doc: str, *options: OptionSpec, **kw: Any) -> None:
    _SPECS.append(CommandSpec(name, placement, doc, options, **kw))


# --- the preamble (design §3.3) ----------------------------------------------------------

_command(
    "document", BLOCK, "Document-wide choices: language, smart quotes, template.",
    _o("lang", PropValue(Target.RUN, "lang"), "the document language (en-US)"),
    _o("smart-quotes", FLAG, "curly quotes and dashes"),
    _o("template", TEXT, "a .dotx or .docx to compile into"),
    _o("page-color", PropValue(Target.RUN, "color"), "the colour of every page"),
    body=None,
)  # fmt: skip
_command(
    "meta", BLOCK, "Document properties (File > Info).",
    _o("title", TEXT, "the document title"),
    _o("subject", TEXT, "its subject"),
    _o("author", TEXT, "its author (also the default author of comments)"),
    _o("description", TEXT, "a summary (Comments in File > Info)"),
    _o("last-modified-by", TEXT, "who saved it last"),
    _o("keywords", ListOf(TEXT, split_text=True), "tags: (a, b) or \"a, b\""),
    patterns={"custom.": CustomPropertyValue()},  # custom.name=value: a custom property
    body=None,
)  # fmt: skip
_command(
    "settings",
    BLOCK,
    "Document settings (settings.xml).",
    _o("even-odd-headers", FLAG, "different headers and footers on even pages"),
    _o("mirror-margins", FLAG, "facing pages: inside and outside margins"),
    _o("update-fields-on-open", FLAG, "Word updates all fields when it opens the document"),
    _o("default-tab-stop", LengthValue(nonnegative=True), "the distance between default tab stops"),
    _o("footnotes", _note_settings("footnote"), "footnote numbering and position"),
    _o("endnotes", _note_settings("endnote"), "endnote numbering and position"),
    _o(
        "protection",
        Record(
            {"mode": Choice.of(*ir.PROTECTION_MODES), "enforce": FLAG},
            shorthand="mode",
            required=("mode",),
        ),
        "restrict editing in Word: forms, read-only, comments or tracked-changes",
    ),
    _o("code-theme", Choice(_themes), "the Pygments style for code blocks"),
    _o(
        "captions",
        Record(
            {
                "chapter-numbers": FLAG,
                "chapter-style": TEXT,
                "separator": Choice.of(*CAPTION_SEPARATORS),
            }
        ),
        "caption numbers with the chapter number: 2-1, 2.1, ...",
    ),
    _o("track-changes", FLAG, "Word's Track Changes on for edits made in Word"),
    _o("hyphenation", Choice.of("auto", "off"), "hyphenate the document automatically"),
    body=None,
)
_command(
    "use",
    BLOCK,
    "A style sheet (.rdxs), macro file or std:module.",
    body=STRING,
    body_required=True,
    body_doc="a file name",
)
_command(
    "include", BLOCK, "The blocks of another .rdx file.",
    body=STRING, body_required=True, body_doc="a file name",
)  # fmt: skip
_command(
    "styles", BLOCK, "Style-sheet definitions in the document.",
    body=ArgKind.STYLESHEET, body_required=True, body_doc="style-sheet rules",
)  # fmt: skip

# --- paragraphs and lists (design §3.6-3.7) ---------------------------------------------

_command(
    "p", BLOCK, "A paragraph with a style or direct paragraph formatting.",
    _o("style", TEXT, "a paragraph style"), ID,
    _o("drop-cap", Toggle(Record({"lines": Int(2, 10), "in-margin": FLAG}, shorthand="lines")),
       "a large first letter over the first lines"),
    props=Target.PARAGRAPH, body_required=True,
)  # fmt: skip
_command(
    "align", BLOCK, "Aligns the paragraphs inside.",
    _o("align", Choice.of(*ALIGNMENTS), "the alignment", required=True),
    positional="align", body_required=True,
)  # fmt: skip
_command(
    "list", BLOCK, "A list with a named numbering definition.",
    _o("numbering", TEXT, "the numbering definition", required=True),
    _o("continue", FLAG, "continue the previous list of this numbering"),
    _o("restart", Int(min=0), "the number to start at"),
    positional="numbering", body_required=True,
)  # fmt: skip
_command(
    "tabs", BLOCK, "Tab stops for the paragraphs inside.",
    _o("stops", TabStopValue(), "the tab stops, one value each"),
    positional="stops", variadic=True, body_required=True,
)  # fmt: skip

# --- sections, headers and footers (design §3.10) -----------------------------------------

_command(
    "section", BLOCK, "Page setup; after content it also starts a new section.",
    _o("size", PageSizeValue(PAGE_SIZES), "the paper size"),
    _o("orientation", Choice.of("portrait", "landscape"), "the page orientation"),
    _o(
        "margins",
        Record(
            {side: LengthValue() for side in ("all", *MARGIN_SIDES)},
            shorthand="all",
        ),
        "the page margins: one length, or by side",
    ),
    _o("gutter", LengthValue(nonnegative=True), "extra margin for binding"),
    _o("header-distance", LengthValue(nonnegative=True), "from the top edge to the header"),
    _o("footer-distance", LengthValue(nonnegative=True), "from the bottom edge to the footer"),
    _o("break", Choice.of(*ir.SECTION_STARTS), "how the section starts"),
    _o(
        "page-numbers",
        Record(
            {"format": Choice.of(*ir.PAGE_NUMBER_FORMATS), "start": Int(min=0)},
            shorthand="format",
        ),
        "the page number format (decimal, lower-roman, ...) and first number",
    ),
    _o(
        "columns",
        Record(
            {
                "count": Int(1, 45),
                "gap": LengthValue(nonnegative=True),
                "separator": FLAG,
                "widths": ListOf(LengthValue(nonnegative=True)),
            },
            shorthand="count",
        ),
        "text columns: a count, or (count, gap, separator, widths)",
    ),
    _o("valign", Choice.of(*ir.VERTICAL_ALIGNMENTS), "where the text sits on the page"),
    _o("borders", PageBordersValue(), "lines around the pages"),
    _o("rtl", FLAG, "right to left: columns and the gutter from the right"),
    _o("text-direction", Choice.of("horizontal", "vertical"), "vertical: East Asian layout"),
    _o(
        "line-numbers",
        Toggle(
            Record(
                {
                    "count-by": Int(min=1),
                    "start": Int(min=1),
                    "distance": LengthValue(nonnegative=True),
                    "restart": Choice.of(*ir.LINE_NUMBER_RESTARTS),
                }
            )
        ),
        "line numbers in the margin",
    ),
    _o("link-headers", FLAG, "headers and footers continue from the previous section"),
    _o("title-page", FLAG, "a different first page"),
    _o("mirror", FLAG, "facing pages (mirror margins)"),
    _o("separator", FLAG, "a line between columns"),
    body=None,
)  # fmt: skip
for _family in ("header", "footer"):
    _command(
        _family, BLOCK, f"The {_family} of the current section.",
        _o("kind", Choice.of(*ir.HEADER_KINDS), "every page, the first page, or even pages"),
        positional="kind",
    )  # fmt: skip

# --- tables (design §3.8) ----------------------------------------------------------------

FLOATING = (
    _o("float", FLAG, "positioned on the page, with text around it"),
    _o("wrap", Choice.of(*ir.WRAPS), "how text flows around it (implies float)"),
    _o("wrap-side", Choice.of(*ir.WRAP_SIDES), "where text goes beside it"),
    _o("x", Record({"at": PositionValue(ir.H_ALIGNS), "from": Choice.of(*ir.H_ANCHORS)},
                   shorthand="at"), "the horizontal position (implies float)"),
    _o("y", Record({"at": PositionValue(ir.V_ALIGNS), "from": Choice.of(*ir.V_ANCHORS)},
                   shorthand="at"), "the vertical position (implies float)"),
    _o("distance", Record({side: LengthValue(nonnegative=True)
                           for side in ("all", "top", "bottom", "left", "right")},
                          shorthand="all"), "space between it and the text"),
)  # fmt: skip
"""Options of pictures, text boxes and tables that float (design §3.16)."""
_command(
    "table", BLOCK, "A table of pipe rows and/or \\row commands.",
    _o("style", TEXT, "a table style, or none"),
    _o("header-rows", Int(min=0), "rows repeated on each page"),
    _o("widths", ListOf(ColumnWidthValue()), "the column widths"),
    _o("align", Choice.of("left", "center", "right"), "the table between the margins"),
    _o(
        "columns",
        ListOf(
            Record(
                {"align": Choice.of(*ALIGNMENTS, "decimal"), "tab": LengthValue()},
                shorthand="align",
            )
        ),
        "paragraph alignment per column",
    ),
    _o("look", FlagSet(ir.TABLE_LOOK, {"first-row": "header-row"}), "table-style parts to apply"),
    *FLOATING,
    props=Target.TABLE, body_required=True,
)  # fmt: skip
_command(
    "row", Placement.PART, "A table row of \\cell commands.",
    _o("header", FLAG, "repeat on each page"),
    _o("cant-split", FLAG, "keep the row on one page"),
    _o("height", RowHeightValue(), "the row height: at least, or exactly"),
    body_required=True,
)  # fmt: skip
_command(
    "cell", Placement.PART, "A table cell: any blocks.",
    _o("colspan", Int(min=1), "the number of columns it spans"),
    _o("rowspan", Int(min=1), "the number of rows it spans"),
    _o("shade", PropValue(Target.CELL, "shading"), "the cell's background"),
    _o("valign", CELL_VALIGN, "where the content sits in a taller cell"),
    _o("align", Choice.of(*ALIGNMENTS), "paragraph alignment"),
    props=Target.CELL,
)  # fmt: skip

# --- captions, figures, contents (design §3.9, §3.12) -----------------------------------

_command(
    "caption", BLOCK, "A numbered caption; the kind defaults from the neighbouring block.",
    _o("kind", TEXT, "Figure, Table, Listing, ..."), ID,
    positional="kind", body_required=True,
)  # fmt: skip
_command(
    "figure", BLOCK, "An image and its caption, kept together.",
    ID, _o("width", SizeValue(), "the image width"),
    props=Target.PARAGRAPH, body_required=True,
)  # fmt: skip
_command(
    "toc", BLOCK, "A table of contents.",
    _o("levels", LevelRange(), "the heading levels listed (default 1-3)"),
    _o("title", TEXT, "a heading above it"), _o("hyperlinks", FLAG, "always on"),
    body=None,
)  # fmt: skip
_command(
    "listof", BLOCK, "A list of figures, tables, ...",
    _o("kind", TEXT, "the caption kind"), _o("title", TEXT, "a heading above it"),
    positional="kind", body=None,
)  # fmt: skip

# --- math (design §3.14) -----------------------------------------------------------------

_command(
    "math", BLOCK, "A display equation.",
    ID, _o("numbered", FLAG, "numbered even without an id (off: not even with one)"),
    _o("nonumber", FLAG, "not numbered, even with an id"),
    body=MATH, body_required=True, body_doc="a formula",
)  # fmt: skip
_command("m", INLINE, "An inline formula.", body=MATH, body_required=True, body_doc="a formula")

# --- citations and the index (design §3.19) ----------------------------------------------

_command(
    "bibliography", BLOCK, "The bibliography data and citation style.",
    _o("file", ListOf(TEXT), "BibTeX or CSL-JSON files", required=True),
    _o("style", TEXT, "a bundled CSL style or a .csl file"),
    body=None,
)  # fmt: skip
_command(
    "printbibliography", BLOCK, "The cited entries (or all of them).",
    _o("title", TEXT, "a heading, or none"), _o("level", Int(1, 9), "the heading level"),
    _o("all", FLAG, "every entry in the data, cited or not"),
    body=None,
)  # fmt: skip
for _cite in ("cite", "textcite"):
    _command(
        _cite, INLINE, "A citation of bibliography keys.",
        *(_o(locator, TEXT, f"a {locator} locator: page=12") for locator in CITE_LOCATORS),
        _o("prefix", TEXT, "text before: see"), _o("suffix", TEXT, "text after: Lemma 2"),
        body=STRING, body_required=True, body_doc="bibliography keys",
    )  # fmt: skip
_command(
    "ix", INLINE, "An index entry; ! separates subentries.",
    _o("see", TEXT, "\"See ...\" instead of a page number"),
    _o("see-also", TEXT, "\"See also ...\" after the page numbers"),
    _o("xref", TEXT, "the whole cross-reference text, in any language"),
    _o("bold", FLAG, "a bold page number"), _o("italic", FLAG, "an italic page number"),
    body=STRING, body_required=True, body_doc="a term",
)  # fmt: skip
_command(
    "index", BLOCK, "The index of the \\ix entries.",
    _o("columns", Int(1, 4), "the number of columns"), _o("headings", FLAG, "letter headings"),
    _o("right-align", FLAG, "page numbers at the right, after dots"),
    _o("title", TEXT, "a heading above it"), _o("level", Int(1, 9), "the heading level"),
    body=None,
)  # fmt: skip

# --- inline formatting (design §3.4) -----------------------------------------------------

for _name, _doc in (
    ("b", "bold"), ("i", "italic"), ("s", "strikethrough"), ("sup", "superscript"),
    ("sub", "subscript"), ("hidden", "hidden text"),
    ("strong", "the Strong style"), ("emph", "the Emphasis style"), ("code", "the Code style"),
):  # fmt: skip
    _command(_name, INLINE, f"Text in {_doc}.", body_required=True)
_command(
    "u", INLINE, "Underlined text.",
    _o("underline", PropValue(Target.RUN, "underline"), "single, double, ..."),
    positional="underline", body_required=True,
)  # fmt: skip
_command(
    "hl", INLINE, "Highlighted text.",
    _o("highlight", PropValue(Target.RUN, "highlight"), "yellow, green, ..."),
    positional="highlight", body_required=True,
)  # fmt: skip
_command(
    "lang", INLINE, "Text in another language.",
    _o("lang", PropValue(Target.RUN, "lang"), "a language tag: de-DE", required=True),
    positional="lang", body_required=True,
)  # fmt: skip
_command("font", INLINE, "Text with direct run formatting.", props=Target.RUN, body_required=True)
_command(
    "style", INLINE, "Text in a character style.",
    _o("style", TEXT, "a character style", required=True), positional="style", body_required=True,
)  # fmt: skip
_command(
    "link", INLINE, "A hyperlink.",
    _o("to", TEXT, "the address", required=True),
    _o("style", TEXT, "a character style for the text, or none (default: Hyperlink)"),
    body_required=True,
)  # fmt: skip
_command(
    "url", INLINE, "A hyperlink showing its address.",
    body=STRING, body_required=True, body_doc="an address",
)  # fmt: skip
_command(
    "image", INLINE, "A picture (PNG, JPEG, GIF, BMP or SVG), inline or floating.",
    _o("width", SizeValue(), "the width (the height follows)"),
    _o("height", LengthValue(nonnegative=True), "the height (the width follows)"),
    _o("alt", TEXT, "a description"), _o("name", TEXT, "its name in Word (Selection pane)"),
    _o("fallback", TEXT, "a PNG or JPEG that Word 2013 and older show for an SVG"),
    *FLOATING,
    _o("crop", Record({side: PercentValue(max=Decimal(99))
                       for side in ("left", "top", "right", "bottom")}),
       "how much to cut off each side"),
    _o("rotate", Int(-360, 360), "degrees clockwise"),
    _o("border", PropValue(Target.RUN, "border"), "a line around the picture"),
    body=STRING, body_required=True, body_doc="a file name",
)  # fmt: skip
_command(
    "embed-docx", BLOCK, "Another .docx, which Word merges in when it opens the document.",
    body=STRING, body_required=True, body_doc="a file name",
)  # fmt: skip
_command(
    "raw", BOTH, "Raw WordprocessingML (block or inline, by its elements) or a whole equation.",
    _o("format", Choice.of("ooxml"), "the markup: ooxml"), positional="format",
    body=RAW, body_required=True, body_doc="XML",
)  # fmt: skip

# --- breaks ------------------------------------------------------------------------------

_command("br", INLINE, "A line break.", body=None)
_command("tab", INLINE, "A tab character.", body=None)
_command("pagebreak", BOTH, "A page break.", body=None)
_command("columnbreak", BOTH, "A column break.", body=None)

# --- fields, references, counters, notes (design §3.11-3.13) ------------------------------

_command("pagenum", INLINE, "The page number (PAGE).", body=None)
_command("numpages", INLINE, "The number of pages (NUMPAGES).", body=None)
_command("sectionpages", INLINE, "The pages of the section (SECTIONPAGES).", body=None)
_command(
    "field", INLINE, "Any Word field; nested fields in { }.",
    _o("result", TEXT, "the text shown until Word updates the field"),
    body=RAW, body_required=True, body_doc="a field code",
)  # fmt: skip
_command(
    "date", INLINE, "A date: fixed at build time, or a DATE / SAVEDATE / CREATEDATE field.",
    _o("kind", Choice.of("fixed", *DATE_FIELDS), "fixed text, or a field Word updates"),
    _o("format", TEXT, 'a Word date picture: "d MMMM yyyy"'),
    body=None,
)  # fmt: skip
_command(
    "ref", INLINE, "A cross-reference to a label.",
    _o("form", Choice.of(*REF_FORMS), "what is printed: Figure 3, 3, the text, ..."),
    positional="form",
    body=STRING, body_required=True, body_doc="a label",
)  # fmt: skip
_command(
    "pageref", INLINE, "The page of a label.",
    body=STRING, body_required=True, body_doc="a label",
)  # fmt: skip
_command(
    "seq", INLINE, "A counter (SEQ field).",
    _o("name", TEXT, "the counter (letters, digits, _)", required=True), ID,
    _o("restart", SeqRestart(), "after each heading of a level, or at a number"),
    _o("format", Choice(SEQ_FORMATS), "decimal, lower-letter, upper-roman, ..."),
    positional="name", body=None,
)  # fmt: skip
for _note in ("footnote", "endnote"):
    _command(
        _note,
        INLINE,
        f"A {_note}: the mark here, the text in the {_note}s.",
        ID,
        body_required=True,
    )

# --- planned -----------------------------------------------------------------------------

_command(
    "textbox", INLINE, "A text box: blocks in a frame, inline or floating.",
    _o("width", SizeValue(), "default: the text width"),
    _o("height", LengthValue(nonnegative=True), "default: as tall as its text"),
    _o("fill", PropValue(Target.RUN, "color"), "the background colour"),
    _o("border", PropValue(Target.RUN, "border"), "a line around it"),
    _o("padding", Record({side: LengthValue(nonnegative=True)
                          for side in ("all", "top", "bottom", "left", "right")},
                         shorthand="all"), "space between the frame and the text"),
    _o("valign", Choice.of(*ir.TEXT_BOX_ALIGNMENTS), "where the text sits in a taller box"),
    _o("rotate", Int(-360, 360), "degrees clockwise"),
    _o("name", TEXT, "its name in Word (Selection pane)"),
    *FLOATING,
    body_required=True,
)  # fmt: skip
_command(
    "watermark", BLOCK, "Text or a picture behind the text of every page of the section.",
    _o("text", TEXT, "the words, such as DRAFT"),
    _o("image", TEXT, "a picture file instead of text"),
    _o("color", PropValue(Target.RUN, "color"), "of the text (default: silver)"),
    _o("font", TEXT, "of the text (default: Calibri)"),
    _o("layout", Choice.of("diagonal", "horizontal"), "the direction of the text"),
    _o("semitransparent", FLAG, "text at half opacity (default: on)"),
    _o("washout", FLAG, "a pale picture (default: on)"),
    _o("width", SizeValue(), "the size (default: as wide as the text allows)"),
    _o("none", FLAG, "no watermark from this section on"),
    positional="text", body=None,
)  # fmt: skip
_command(
    "comment", INLINE, "A comment in the margin on the text in the body (none: a point).",
    _o("text", MarkupValue(), "what the comment says", required=True),
    _o("author", TEXT, "default: the document's author"),
    _o("initials", TEXT, "default: from the author's name"),
    _o("date", TEXT, "YYYY-MM-DD or YYYY-MM-DDThh:mm (default: the build time)"),
    body_doc="the commented text",
)  # fmt: skip
_command("ins", INLINE, "Tracked inserted text.", planned=POSTPONED, keep_body=True)
_command("del", INLINE, "Tracked deleted text.", planned=POSTPONED)
_command(
    "control", INLINE, "A content control: a field to fill in Word (the body: its value).",
    _o("type", Choice.of(*ir.CONTROL_KINDS), "default: text"),
    _o("tag", TEXT, "a name for programs that read the form"),
    _o("title", TEXT, "shown on the control in Word"),
    _o("placeholder", TEXT, "grey text shown until it is filled in"),
    _o("lock", Choice.of(*ir.CONTROL_LOCKS), "what cannot be edited or deleted"),
    _o("items", ListOf(TEXT), "the choices of a dropdown or combo box"),
    _o("format", TEXT, "a date's display format, as Word's (d MMMM yyyy)"),
    _o("date", TEXT, "a date's value, YYYY-MM-DD"),
    _o("checked", FLAG, "a checkbox that is ticked"),
    _o("width", LengthValue(nonnegative=True), "an empty picture control's size (4cm)"),
    _o("height", LengthValue(nonnegative=True), "(3cm)"),
    positional="type",
)  # fmt: skip

# --- fenced code block attributes (design §3.15) ------------------------------------------

CODE_BLOCK = CommandSpec(
    "code block", BLOCK, "A fenced code block: ```python {#lst:x, caption=..., line-numbers}.",
    (
        _o("id", TEXT, "a label to refer to this with @label"),
        _o("caption", Text(allow_empty=True), "a Listing caption"),
        _o("line-numbers", FLAG, "number the lines"),
        _o("first-line", Int(min=0), "the number of the first line"),
        _o("highlight", FLAG, "syntax colours (default: on when a language is given)"),
    ),
    body=None, title="code blocks",
)  # fmt: skip

COMMANDS: dict[str, CommandSpec] = {spec.name: spec for spec in _SPECS}
"""Every built-in command by name."""


def body_kind(name: str) -> ArgKind:
    """How the body of `name` is read; macros and unknown commands have markup bodies."""
    spec = COMMANDS.get(name)
    return spec.body if spec is not None and spec.body is not None else ArgKind.MARKUP


def is_block(name: str) -> bool:
    spec = COMMANDS.get(name)
    return spec is not None and spec.placement is Placement.BLOCK
