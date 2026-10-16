"""The built-in commands of the language: every command, its options and its body.

This table is the single definition of what the language accepts (language design §3).
The parser takes body kinds from it, the lowering binds every command against it, and
documentation or editor support can be generated from it.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from wordgenx import ir
from wordgenx.commands.spec import CommandSpec, OptionSpec, Placement
from wordgenx.commands.types import (
    Bool,
    Choice,
    ColumnWidthValue,
    CustomPropertyValue,
    FlagSet,
    Int,
    LengthValue,
    LevelRange,
    ListOf,
    PageSize,
    PageSizeValue,
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
from wordgenx.props import Target
from wordgenx.syntax.commands import ArgKind
from wordgenx.values import Length

BLOCK, INLINE, BOTH = Placement.BLOCK, Placement.INLINE, Placement.BOTH
STRING, RAW, MATH = ArgKind.STRING, ArgKind.RAW, ArgKind.MATH


def _themes() -> Sequence[str]:
    from wordgenx.highlight import themes  # Pygments is loaded only when a theme is checked

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
    body=None,
)  # fmt: skip
_command(
    "meta", BLOCK, "Document properties (File > Info).",
    *(_o(name, TEXT) for name in ("title", "subject", "author", "description", "last-modified-by")),
    _o("keywords", ListOf(TEXT, split_text=True)),
    patterns={"custom.": CustomPropertyValue()},
    body=None,
)  # fmt: skip
_command(
    "settings",
    BLOCK,
    "Document settings (settings.xml).",
    _o("even-odd-headers", FLAG),
    _o("mirror-margins", FLAG),
    _o("update-fields-on-open", FLAG),
    _o("default-tab-stop", LengthValue(nonnegative=True)),
    _o("footnotes", _note_settings("footnote")),
    _o("endnotes", _note_settings("endnote")),
    _o(
        "protection",
        Record(
            {"mode": Choice.of(*ir.PROTECTION_MODES), "enforce": FLAG},
            shorthand="mode",
            required=("mode",),
        ),
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
    ),
    planned_options={"track-changes": "M5", "hyphenation": "M4"},
    body=None,
)
_command(
    "use",
    BLOCK,
    "A style sheet (.wgxs), macro file or std:module.",
    body=STRING,
    body_required=True,
    body_doc="a file name",
)
_command(
    "include", BLOCK, "The blocks of another .wgx file.",
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
    props=Target.PARAGRAPH, body_required=True,
)  # fmt: skip
_command(
    "align", BLOCK, "Aligns the paragraphs inside.",
    _o("align", Choice.of(*ALIGNMENTS), required=True),
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
    _o("stops", TabStopValue()),
    positional="stops", variadic=True, body_required=True,
)  # fmt: skip

# --- sections, headers and footers (design §3.10) -----------------------------------------

_command(
    "section", BLOCK, "Page setup; after content it also starts a new section.",
    _o("size", PageSizeValue(PAGE_SIZES)),
    _o("orientation", Choice.of("portrait", "landscape")),
    _o(
        "margins",
        Record(
            {side: LengthValue() for side in ("all", *MARGIN_SIDES)},
            shorthand="all",
        ),
    ),
    _o("gutter", LengthValue(nonnegative=True)),
    _o("header-distance", LengthValue(nonnegative=True)),
    _o("footer-distance", LengthValue(nonnegative=True)),
    _o("break", Choice.of(*ir.SECTION_STARTS), "how the section starts"),
    _o(
        "page-numbers",
        Record(
            {"format": Choice.of(*ir.PAGE_NUMBER_FORMATS), "start": Int(min=0)},
            shorthand="format",
        ),
    ),
    _o(
        "columns",
        Record(
            {"count": Int(1, 45), "gap": LengthValue(nonnegative=True), "separator": FLAG},
            shorthand="count",
        ),
    ),
    _o("valign", Choice.of(*ir.VERTICAL_ALIGNMENTS), "where the text sits on the page"),
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
        _o("kind", Choice.of(*ir.HEADER_KINDS)),
        positional="kind",
    )  # fmt: skip

# --- tables (design §3.8) ----------------------------------------------------------------

_command(
    "table", BLOCK, "A table of pipe rows and/or \\row commands.",
    _o("style", TEXT, "a table style, or none"),
    _o("header-rows", Int(min=0), "rows repeated on each page"),
    _o("widths", ListOf(ColumnWidthValue())),
    _o("align", Choice.of("left", "center", "right")),
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
    props=Target.TABLE, body_required=True,
)  # fmt: skip
_command(
    "row", Placement.PART, "A table row of \\cell commands.",
    _o("header", FLAG, "repeat on each page"),
    _o("cant-split", FLAG, "keep the row on one page"),
    _o("height", RowHeightValue()),
    body_required=True,
)  # fmt: skip
_command(
    "cell", Placement.PART, "A table cell: any blocks.",
    _o("colspan", Int(min=1)),
    _o("rowspan", Int(min=1)),
    _o("shade", PropValue(Target.CELL, "shading"), "the cell's background"),
    _o("valign", CELL_VALIGN),
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
    _o("levels", LevelRange()), _o("title", TEXT), _o("hyperlinks", FLAG, "always on"),
    body=None,
)  # fmt: skip
_command(
    "listof", BLOCK, "A list of figures, tables, ...",
    _o("kind", TEXT, "the caption kind"), _o("title", TEXT),
    positional="kind", body=None,
)  # fmt: skip

# --- math (design §3.14) -----------------------------------------------------------------

_command(
    "math", BLOCK, "A display equation.",
    ID, _o("numbered", FLAG), _o("nonumber", FLAG),
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
    _o("title", TEXT, "a heading, or none"), _o("level", Int(1, 9)), _o("all", FLAG),
    body=None,
)  # fmt: skip
for _cite in ("cite", "textcite"):
    _command(
        _cite, INLINE, "A citation of bibliography keys.",
        *(_o(locator, TEXT) for locator in CITE_LOCATORS),
        _o("prefix", TEXT), _o("suffix", TEXT),
        body=STRING, body_required=True, body_doc="bibliography keys",
    )  # fmt: skip
_command(
    "ix", INLINE, "An index entry; ! separates subentries.",
    _o("see", TEXT), _o("see-also", TEXT), _o("xref", TEXT), _o("bold", FLAG), _o("italic", FLAG),
    body=STRING, body_required=True, body_doc="a term",
)  # fmt: skip
_command(
    "index", BLOCK, "The index of the \\ix entries.",
    _o("columns", Int(1, 4)), _o("headings", FLAG), _o("right-align", FLAG),
    _o("title", TEXT), _o("level", Int(1, 9)),
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
    _o("lang", PropValue(Target.RUN, "lang"), required=True),
    positional="lang", body_required=True,
)  # fmt: skip
_command("font", INLINE, "Text with direct run formatting.", props=Target.RUN, body_required=True)
_command(
    "style", INLINE, "Text in a character style.",
    _o("style", TEXT, required=True), positional="style", body_required=True,
)  # fmt: skip
_command(
    "link", INLINE, "A hyperlink.",
    _o("to", TEXT, "the address", required=True), body_required=True,
)  # fmt: skip
_command(
    "url", INLINE, "A hyperlink showing its address.",
    body=STRING, body_required=True, body_doc="an address",
)  # fmt: skip
_command(
    "image", INLINE, "An inline picture (PNG, JPEG, GIF, BMP or SVG).",
    _o("width", SizeValue()), _o("height", LengthValue(nonnegative=True)),
    _o("alt", TEXT, "a description"), _o("name", TEXT),
    _o("fallback", TEXT, "a PNG or JPEG that Word 2013 and older show for an SVG"),
    body=STRING, body_required=True, body_doc="a file name",
)  # fmt: skip
_command(
    "embed-docx", BLOCK, "Another .docx, which Word merges in when it opens the document.",
    body=STRING, body_required=True, body_doc="a file name",
)  # fmt: skip
_command(
    "raw", BOTH, "Raw WordprocessingML (block or inline, by its elements).",
    _o("format", Choice.of("ooxml")), positional="format",
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
    _o("kind", Choice.of("fixed", *DATE_FIELDS)),
    _o("format", TEXT, 'a Word date picture: "d MMMM yyyy"'),
    body=None,
)  # fmt: skip
_command(
    "ref", INLINE, "A cross-reference to a label.",
    _o("form", Choice.of(*REF_FORMS)), positional="form",
    body=STRING, body_required=True, body_doc="a label",
)  # fmt: skip
_command(
    "pageref", INLINE, "The page of a label.",
    body=STRING, body_required=True, body_doc="a label",
)  # fmt: skip
_command(
    "seq", INLINE, "A counter (SEQ field).",
    _o("name", TEXT, "the counter (letters, digits, _)", required=True), ID,
    _o("restart", SeqRestart()), _o("format", Choice(SEQ_FORMATS)),
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

_command("textbox", INLINE, "A text box.", planned="M4")
_command("watermark", BLOCK, "A page watermark.", planned="M4")
_command("comment", INLINE, "A comment.", planned="M5", keep_body=True)
_command("ins", INLINE, "Tracked inserted text.", planned="M5", keep_body=True)
_command("del", INLINE, "Tracked deleted text.", planned="M5")
_command("control", INLINE, "A content control.", planned="M5", keep_body=True)

# --- fenced code block attributes (design §3.15) ------------------------------------------

CODE_BLOCK = CommandSpec(
    "code block", BLOCK, "A fenced code block: ```python {#lst:x, caption=..., line-numbers}.",
    (
        _o("id", TEXT), _o("caption", Text(allow_empty=True)),
        _o("line-numbers", FLAG), _o("first-line", Int(min=0)), _o("highlight", FLAG),
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
