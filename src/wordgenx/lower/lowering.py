"""Lowering: the document AST -> the IR.

One pass over the AST, in document order:

* Declarations (\\define, \\let, \\styles, \\use, \\document, \\meta, \\settings,
  \\section) update the lowering state.
* Blocks become IR paragraphs: plain paragraphs, headings, lists (with Word list
  numbering), code blocks, and the block commands \\p, \\align, \\list,
  \\pagebreak, \\include.
* Inline markup becomes runs with character styles and direct formatting.

Style and list references are checked at the end, once every style sheet has been
read, so a \\styles block may appear anywhere. Built-in styles the markup relies on
(Heading 1, List Paragraph, Strong, ...) are created only when used and not defined.
"""

from __future__ import annotations

import difflib
import io
import re
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field, replace
from decimal import Decimal
from importlib import resources
from pathlib import Path
from typing import get_args

from PIL import Image as PILImage
from PIL import UnidentifiedImageError

from wordgenx import ir
from wordgenx.lower.builtins import BUILTIN_STYLES
from wordgenx.lower.fields import (
    build_time,
    field_name,
    format_date,
    merge_text,
    parse_instruction,
)
from wordgenx.lower.macros import Binding, Macro, Substitution, define_macro, let_macro
from wordgenx.lower.references import REF_FORMS, Label, PendingRef, PendingToc, References
from wordgenx.lower.typography import smarten
from wordgenx.ooxml.style_names import style_id
from wordgenx.props import (
    EMPTY_CELL,
    EMPTY_PARAGRAPH,
    EMPTY_RUN,
    EMPTY_TABLE,
    REGISTRY,
    SHORTHANDS,
    PropertySet,
    Target,
    UnknownPropertyError,
)
from wordgenx.stylesheet.collect import STYLESHEET_SUFFIX
from wordgenx.stylesheet.compiler import StyleSheetCompiler
from wordgenx.stylesheet.syntax import parse_stylesheet
from wordgenx.syntax import ParseResult, ast, parse
from wordgenx.syntax.diagnostics import Code, Diagnostic, Severity
from wordgenx.syntax.source import Source, Span
from wordgenx.values import InvalidValueError, Length, TabStop

Located = tuple[Source, Diagnostic]

BLOCK_COMMANDS = frozenset(
    (
        "p align list include use styles document meta settings section table header footer tabs "
        "caption figure toc listof"
    ).split()
)
INLINE_COMMANDS = frozenset(
    (
        "b i u s sup sub hl font style lang hidden strong emph code link url br tab image "
        "pagenum numpages sectionpages field date ref pageref seq footnote endnote"
    ).split()
)
FIELD_COMMANDS = {"pagenum": "PAGE", "numpages": "NUMPAGES", "sectionpages": "SECTIONPAGES"}
DATE_FIELDS = {"today": "DATE", "saved": "SAVEDATE", "created": "CREATEDATE"}
META_FIELDS = {"TITLE": "title", "SUBJECT": "subject", "AUTHOR": "author", "KEYWORDS": "keywords"}
BREAK_COMMANDS = frozenset({"pagebreak", "columnbreak"})
"""Allowed both inline and as a block of their own."""
TABLE_PARTS = frozenset({"row", "cell"})
"""Only valid inside \\table (rows) and \\row (cells)."""

PLANNED: dict[str, str] = {
    **dict.fromkeys(
        [
            "raw",
        ],
        "M2",
    ),
    **dict.fromkeys(
        [
            "m",
            "math",
            "index",
            "ix",
            "cite",
            "textcite",
            "bibliography",
            "printbibliography",
            "embed-docx",
        ],
        "M3",
    ),
    **dict.fromkeys(["textbox", "watermark"], "M4"),
    **dict.fromkeys(["comment", "ins", "del", "control"], "M5"),
}
"""Commands of the language that are not implemented yet -> the milestone that adds them."""

PLANNED_BLOCKS = frozenset("watermark bibliography printbibliography index".split())
CELL_ALIGN = {"top": "top", "center": "center", "middle": "center", "bottom": "bottom"}
DEFAULT_DPI = 96
TRANSPARENT = frozenset({"comment", "ins", "control"})
"""Planned commands whose body text is kept (with a warning) rather than dropped."""

PAGE_SIZES: dict[str, tuple[Length, Length]] = {
    "A3": (Length.mm(297), Length.mm(420)),
    "A4": (Length.mm(210), Length.mm(297)),
    "A5": (Length.mm(148), Length.mm(210)),
    "Letter": (Length.inch("8.5"), Length.inch(11)),
    "Legal": (Length.inch("8.5"), Length.inch(14)),
}
ALIGN_WORDS = frozenset({"left", "center", "right", "justify", "distribute"})
MAX_MACRO_DEPTH = 32
BLOCK_TYPES = get_args(ast.Block)
WHITESPACE_RE = re.compile(r"[ \t\r\n]+")


# --- inline pieces ---------------------------------------------------------------------


@dataclass(frozen=True)
class Format:
    props: PropertySet = EMPTY_RUN
    style: str | None = None
    link: tuple[int, str] | None = None
    """(link id, url) of the hyperlink the text belongs to."""


@dataclass(frozen=True)
class Piece:
    text: str
    format: Format
    kind: str = "text"
    """text (smart punctuation, whitespace collapse) | code (verbatim) | tab | line | page
    | column | image | field | mark (a bookmark start or end) | note."""
    image: ir.Image | None = None
    field: ir.Field | None = None
    mark: ir.BookmarkStart | ir.BookmarkEnd | None = None
    note: ir.Note | None = None


@dataclass
class TableSpec:
    """Table options from \\table[...] (defaults for a stand-alone pipe table)."""

    style: str | None = "Table Grid"
    style_span: Span | None = None
    widths: list[tuple[str, Length | Decimal]] | None = None
    """("length", Length) | ("percent", Decimal) | ("fraction", Decimal) per column."""
    header_rows: int = 0
    align: str | None = None
    columns: list[str] = field(default_factory=lambda: [])
    """Paragraph alignment per column (left, center, right, justify, decimal)."""
    props: PropertySet = EMPTY_TABLE
    look: frozenset[str] | None = None


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
    height: Length | None = None
    exact: bool = False
    cant_split: bool = False


@dataclass
class ListContext:
    numbering: str
    instance: int
    level: int
    ordered: bool
    named: bool
    start: int | None


@dataclass
class FigureContext:
    label: str | None
    label_span: Span
    width: Length | None
    captioned: bool = False


SEQ_FORMATS = {
    "decimal": "ARABIC",
    "lower-letter": "alphabetic",
    "upper-letter": "ALPHABETIC",
    "lower-roman": "roman",
    "upper-roman": "ROMAN",
}
SEQ_NAME_RE = re.compile(r"[A-Za-z][A-Za-z0-9_]*")
NOTE_FORMATS = {**ir.PAGE_NUMBER_FORMATS, "symbols": "chicago"}
NOTE_RESTARTS = {"continuous": "continuous", "each-section": "eachSect", "each-page": "eachPage"}
NOTE_POSITIONS = {
    "footnote": {"page-bottom": "pageBottom", "beneath-text": "beneathText"},
    "endnote": {"section-end": "sectEnd", "document-end": "docEnd"},
}


@dataclass
class StyleUse:
    name: str
    kind: ir.StyleKind
    span: Span
    source: Source


# --- the lowering pass -----------------------------------------------------------------


@dataclass
class Lowering:
    source: Source
    base_dir: Path
    diagnostics: list[Located] = field(default_factory=lambda: [])
    stylesheets: StyleSheetCompiler = field(
        default_factory=lambda: StyleSheetCompiler(builtins=BUILTIN_STYLES)
    )
    macros: dict[str, Macro] = field(default_factory=lambda: {})
    meta: ir.Meta = field(default_factory=ir.Meta)
    settings: ir.Settings = field(default_factory=ir.Settings)
    section: ir.Section = field(default_factory=ir.Section)
    """The section being written (earlier ones are closed by SectionBreak blocks)."""
    smart_quotes: bool = True
    has_content: bool = False
    even_headers: bool = False
    header_kinds: set[str] = field(default_factory=lambda: set[str]())
    """Header/footer kinds defined so far ("header:first", ...): what a new section inherits."""
    _container_depth: int = 0
    _part: str | None = None
    """header | footer | footnote | endnote while lowering one (notes are not allowed there)."""
    """> 0 while lowering a table cell, header or footer (no section breaks there)."""
    style_uses: list[StyleUse] = field(default_factory=lambda: [])
    list_uses: list[tuple[str, Span, Source]] = field(default_factory=lambda: [])
    _instances: int = 0
    _last_instance: dict[str, tuple[int, int | None]] = field(default_factory=lambda: {})
    _links: int = 0
    _macro_depth: int = 0
    _include_stack: list[Path] = field(default_factory=lambda: [])
    _std_loaded: set[str] = field(default_factory=lambda: set[str]())
    references: References = field(default_factory=References)
    _figure: FigureContext | None = None
    _previous: ir.Block | None = None
    """The block lowered just before the current one (captions look at it)."""
    _next: ast.Inline | ast.Block | None = None
    """The block (or block command) after the current one."""

    # --- reporting ----------------------------------------------------------------------

    def report(
        self,
        code: Code,
        message: str,
        span: Span,
        label: str = "",
        help: str | None = None,
        severity: Severity = Severity.ERROR,
    ) -> None:
        self.diagnostics.append(
            (self.source, Diagnostic(severity, code, message, span, label, help))
        )

    def warn(self, code: Code, message: str, span: Span, help: str | None = None) -> None:
        self.report(code, message, span, help=help, severity=Severity.WARNING)

    def not_yet(self, command: ast.Command) -> None:
        self.warn(
            Code.NOT_SUPPORTED_YET,
            f"`\\{command.name}` is not supported yet; it is ignored",
            command.span,
            help=f"planned for milestone {PLANNED[command.name]}",
        )

    def unknown_command(self, command: ast.Command) -> None:
        known = sorted(
            BLOCK_COMMANDS | INLINE_COMMANDS | BREAK_COMMANDS | set(PLANNED) | set(self.macros)
        )
        close = difflib.get_close_matches(command.name, known, n=1)
        self.report(
            Code.UNKNOWN_COMMAND,
            f"unknown command `\\{command.name}`",
            command.span,
            f"did you mean `\\{close[0]}`?" if close else "",
            help="define it with \\define, or escape the backslash as \\\\",
        )

    # --- documents and blocks --------------------------------------------------------------

    def document(self, document: ast.Document) -> list[ir.Block]:
        return self.blocks(document.blocks)

    def blocks(
        self, blocks: Iterable[ast.Block], lists: ListContext | None = None
    ) -> list[ir.Block]:
        out: list[ir.Block] = []
        items = list(blocks)
        for i, block in enumerate(items):
            self._previous = out[-1] if out else None
            self._next = items[i + 1] if i + 1 < len(items) else None
            out.extend(self.block(block, lists))
        return out

    def block(self, block: ast.Block, lists: ListContext | None = None) -> list[ir.Block]:
        if isinstance(block, ast.Paragraph):
            return self.paragraph(block)
        self.has_content = True
        if isinstance(block, ast.Heading):
            return [self.heading(block)]
        if isinstance(block, ast.ListBlock):
            return self.list_block(block, lists)
        if isinstance(block, ast.CodeBlock):
            return self.code_block(block)
        if isinstance(block, ast.DisplayMath):
            self.warn(
                Code.NOT_SUPPORTED_YET,
                "display math is not supported yet; the source is shown as code",
                block.span,
                help="planned for milestone M3",
            )
            return [self.code_paragraph(line) for line in block.source.split("\n")]
        assert isinstance(block, ast.PipeTable)
        table = self.make_table(TableSpec(), self.pipe_rows(block), block.span)
        return [table] if table is not None else []

    def heading(self, heading: ast.Heading) -> ir.Paragraph:
        name = f"Heading {heading.level}"
        self.use_style(name, ir.StyleKind.PARAGRAPH, heading.span)
        content = self.assemble(self.inlines(heading.content, Format()))
        if heading.label is not None:
            label = self.register_label(heading.label.name, heading.label.span, "heading")
            content = _marked(label.bookmark, content)
        return ir.Paragraph(content, name, unnumbered=not heading.numbered)

    def code_block(self, block: ast.CodeBlock) -> list[ir.Block]:
        """A fenced code block; ```` ```python {#lst:x, caption="..."} ```` adds a Listing
        caption above it."""
        out: list[ir.Block] = []
        caption: str | None = None
        label: tuple[str, Span] | None = None
        command = ast.Command("code block", None, None, span=block.span)
        for option in block.options.items if block.options else ():
            if option.key == "caption":
                caption = self.text_value(option.value, command, "caption")
            elif option.key == "id":
                text = self.text_value(option.value, command, "id")
                if text:
                    label = (text, option.value.span)
        if label is not None and caption is None:
            caption = ""
        if caption is not None:
            text = self.assemble([Piece(caption, Format())])
            name, span = label if label is not None else (None, block.span)
            out.append(self.make_caption("Listing", name, span, text, keep_with_next=True))
        out += [self.code_paragraph(line) for line in block.code.split("\n")]
        return out

    def code_paragraph(self, line: str) -> ir.Paragraph:
        self.use_style("Code Block", ir.StyleKind.PARAGRAPH, Span(0, 0))
        return ir.Paragraph((ir.Run(line),) if line else (), "Code Block")

    # --- paragraphs: inline content or block commands ------------------------------------------

    def paragraph(self, paragraph: ast.Paragraph) -> list[ir.Block]:
        items = self.expand(paragraph.content)
        blocks = [i for i, item in enumerate(items) if self.is_block_item(item)]
        has_text = any(not _is_blank(item) and not self.is_block_item(item) for item in items)
        inside = [i for i in blocks if not _on_own_line(items, i)]
        if inside and has_text:
            for i in inside:
                self.report(
                    Code.MISPLACED,
                    "a block cannot appear inside a paragraph",
                    items[i].span,  # type: ignore[union-attr]
                    help="put it on its own line",
                )
            items = [item for item in items if not self.is_block_item(item)]
            blocks = []
        if not blocks:
            return self.text_paragraph(items) if has_text else []
        # Block commands on lines of their own split the paragraph: text, block, text, ...
        segments: list[ast.Inline | ast.Block | list[ast.Inline | ast.Block]] = []
        for item in items:
            if self.is_block_item(item):
                segments.append(item)
            elif not _is_blank(item) or (segments and isinstance(segments[-1], list)):
                if not segments or not isinstance(segments[-1], list):
                    segments.append([])
                segments[-1].append(item)  # type: ignore[union-attr]
        out: list[ir.Block] = []
        previous, following = self._previous, self._next
        for i, segment in enumerate(segments):
            self._previous = out[-1] if out else previous
            after = segments[i + 1] if i + 1 < len(segments) else following
            self._next = after[0] if isinstance(after, list) else after
            if isinstance(segment, list):
                out.extend(self.text_paragraph(segment))
            elif isinstance(segment, ast.Command):
                out.extend(self.block_command(segment))
            else:
                out.extend(self.block(segment))  # type: ignore[arg-type]
        return out

    def text_paragraph(self, content: list[ast.Inline | ast.Block]) -> list[ir.Block]:
        if all(_is_blank(item) for item in content):
            return []
        self.has_content = True
        pieces = self.inlines(content, Format())  # type: ignore[arg-type]
        return [ir.Paragraph(self.assemble(pieces))]

    def is_block_item(self, item: ast.Inline | ast.Block) -> bool:
        if isinstance(item, BLOCK_TYPES):
            return True  # a block spliced in by a macro
        if isinstance(item, ast.Command):
            # \pagebreak and \columnbreak are inline: alone they still make their own paragraph.
            return item.name in BLOCK_COMMANDS or item.name in PLANNED_BLOCKS
        return False

    def expand(self, content: Sequence[ast.Inline]) -> list[ast.Inline | ast.Block]:
        """Register declarations and expand macro calls; blocks from macros are spliced in."""
        out: list[ast.Inline | ast.Block] = []
        for node in content:
            if isinstance(node, ast.Define):
                self.register_define(node)
            elif isinstance(node, ast.Let):
                self.register_let(node)
            elif isinstance(node, ast.Command) and node.name in self.macros:
                out.extend(self.call_macro(node))
            elif isinstance(node, ast.Group):
                out.extend(self.splice(node.blocks))
            else:
                out.append(node)
        return out

    def splice(self, blocks: tuple[ast.Block, ...]) -> list[ast.Inline | ast.Block]:
        if len(blocks) == 1 and isinstance(blocks[0], ast.Paragraph):
            return self.expand(blocks[0].content)
        return list(blocks)

    # --- macros -----------------------------------------------------------------------------

    def register_define(self, node: ast.Define) -> None:
        if not self.check_macro_name(node.name, node.span):
            return
        self.macros[node.name] = define_macro(
            node, self.source, lambda msg, span: self.report(Code.MACRO, msg, span)
        )

    def register_let(self, node: ast.Let) -> None:
        if self.check_macro_name(node.name, node.span):
            self.macros[node.name] = let_macro(node, self.source)

    def check_macro_name(self, name: str, span: Span) -> bool:
        if name in BLOCK_COMMANDS | INLINE_COMMANDS | BREAK_COMMANDS | set(PLANNED) | {
            "define",
            "let",
        }:
            self.report(
                Code.MACRO, f"`\\{name}` is a built-in command and cannot be redefined", span
            )
            return False
        return True

    def call_macro(self, call: ast.Command) -> list[ast.Inline | ast.Block]:
        macro = self.macros[call.name]
        if self._macro_depth >= MAX_MACRO_DEPTH:
            self.report(
                Code.MACRO,
                f"macro expansion deeper than {MAX_MACRO_DEPTH} levels",
                call.span,
                help="is the macro calling itself?",
            )
            return []
        bindings: dict[str, Binding] = dict(macro.params)
        for option in call.options.items if call.options else ():
            if option.key is None:
                self.report(
                    Code.MACRO,
                    f"`\\{call.name}` takes named options",
                    option.span,
                    help=f"parameters: {', '.join(macro.params) or 'none'}",
                )
            elif option.key not in macro.params:
                close = difflib.get_close_matches(option.key, list(macro.params), n=1)
                self.report(
                    Code.MACRO,
                    f"`\\{call.name}` has no parameter `{option.key}`",
                    option.span,
                    f"did you mean `{close[0]}`?" if close else "",
                )
            else:
                bindings[option.key] = option.value
        if isinstance(call.body, ast.MarkupBody):
            bindings["body"] = call.body
        else:
            bindings.setdefault("body", None)
        source = self.source
        self.source = macro.source
        expanded = Substitution(
            bindings, lambda msg, span: self.report(Code.MACRO, msg, span)
        ).blocks(macro.body)
        self.source = source
        self._macro_depth += 1
        try:
            return self.splice(expanded)
        finally:
            self._macro_depth -= 1

    # --- block commands ----------------------------------------------------------------------

    def block_command(self, command: ast.Command) -> list[ir.Block]:
        name = command.name
        handler: Callable[[ast.Command], list[ir.Block]] | None = {
            "p": self.cmd_p,
            "align": self.cmd_align,
            "list": self.cmd_list,
            "include": self.cmd_include,
            "use": self.cmd_use,
            "styles": self.cmd_styles,
            "document": self.cmd_document,
            "meta": self.cmd_meta,
            "settings": self.cmd_settings,
            "section": self.cmd_section,
            "table": self.cmd_table,
            "header": self.cmd_header,
            "footer": self.cmd_header,
            "tabs": self.cmd_tabs,
            "caption": self.cmd_caption,
            "figure": self.cmd_figure,
            "toc": self.cmd_toc,
            "listof": self.cmd_toc,
        }.get(name)
        if handler is not None:
            return handler(command)
        if name in BREAK_COMMANDS:
            self.has_content = True
            self.no_body(command)
            self.no_options(command)
            return [ir.Paragraph((ir.Break("page" if name == "pagebreak" else "column"),))]
        self.not_yet(command)
        return []

    def markup_blocks(self, command: ast.Command) -> tuple[ast.Block, ...]:
        if isinstance(command.body, ast.MarkupBody):
            return command.body.blocks
        self.report(
            Code.INVALID_ARGUMENT,
            f"`\\{command.name}` needs a body: \\{command.name}{{...}}",
            command.span,
        )
        return ()

    def single_paragraph(self, command: ast.Command) -> Sequence[ast.Inline]:
        blocks = self.markup_blocks(command)
        if not blocks:
            return ()
        if len(blocks) == 1 and isinstance(blocks[0], ast.Paragraph):
            return blocks[0].content
        self.report(
            Code.INVALID_ARGUMENT,
            f"the body of `\\{command.name}` must be a single paragraph",
            command.span,
            help="split it into several commands",
        )
        content: list[ast.Inline] = []
        for block in blocks:
            if isinstance(block, ast.Paragraph):
                content.extend(block.content)
        return content

    def cmd_p(self, command: ast.Command) -> list[ir.Block]:
        self.has_content = True
        extra, props = self.option_props(command, Target.PARAGRAPH, extra=("style", "id"))
        style = None
        if (value := extra.get("style")) is not None:
            style = self.text_value(value, command, "style")
            if style is not None:
                self.use_style(style, ir.StyleKind.PARAGRAPH, value.span)
        content = self.expand(self.single_paragraph(command))
        pieces = self.inlines([c for c in content if not isinstance(c, BLOCK_TYPES)], Format())  # type: ignore[misc]
        inlines = self.assemble(pieces)
        if (value := extra.get("id")) is not None and (
            name := self.text_value(value, command, "id")
        ):
            inlines = _marked(self.register_label(name, value.span, "paragraph").bookmark, inlines)
        return [ir.Paragraph(inlines, style, props)]

    def cmd_align(self, command: ast.Command) -> list[ir.Block]:
        positional = self.positional(command, 1)
        alignment = positional[0] if positional else None
        if alignment not in ALIGN_WORDS:
            self.report(
                Code.INVALID_ARGUMENT,
                "expected an alignment: \\align[center]{...}",
                command.span,
                help=", ".join(sorted(ALIGN_WORDS)),
            )
            alignment = None
        paragraphs = self.blocks(self.markup_blocks(command))
        if alignment is None:
            return paragraphs
        aligned = PropertySet(Target.PARAGRAPH, {"align": alignment})
        return list(
            ir.map_paragraphs(
                tuple(paragraphs), lambda p: replace(p, props=p.props.merged(aligned))
            )
        )

    def cmd_list(self, command: ast.Command) -> list[ir.Block]:
        name: str | None = None
        continue_list = False
        start: int | None = None
        for option in command.options.items if command.options else ():
            text = self.text_value(option.value, command, option.key or "option")
            if option.key is None and text == "continue":
                continue_list = True
            elif option.key is None and name is None and text:
                name = text
            elif option.key == "restart" and text is not None:
                if text.isdigit():
                    start = int(text)
                else:
                    self.report(
                        Code.INVALID_ARGUMENT, "restart must be a whole number", option.value.span
                    )
            else:
                self.report(
                    Code.INVALID_ARGUMENT,
                    "unknown \\list option",
                    option.span,
                    help="\\list[Name, continue] or \\list[Name, restart=5]",
                )
        if name is None:
            self.report(
                Code.INVALID_ARGUMENT,
                "\\list needs a numbering name: \\list[Legal]{...}",
                command.span,
            )
            return self.blocks(self.markup_blocks(command))
        self.list_uses.append((name, command.span, self.source))
        if continue_list and start is None and name in self._last_instance:
            instance, start = self._last_instance[name]
        else:
            instance = self.new_instance(name, start)
        context = ListContext(name, instance, 0, ordered=True, named=True, start=start)
        out: list[ir.Block] = []
        for block in self.markup_blocks(command):
            if isinstance(block, ast.ListBlock):
                self.has_content = True
                out.extend(self.list_block(block, context))
            else:
                out.extend(self.block(block))
        return out

    def cmd_include(self, command: ast.Command) -> list[ir.Block]:
        path = self.path_value(command)
        if path is None:
            return []
        resolved = path.resolve()
        if resolved in self._include_stack:
            self.report(Code.INCLUDE, f"`{path.name}` includes itself", command.span)
            return []
        result = self.load(path, command)
        if result is None:
            return []
        source, base_dir = self.source, self.base_dir
        self.source, self.base_dir = result.source, resolved.parent
        self._include_stack.append(resolved)
        try:
            return self.blocks(result.ast.blocks)
        finally:
            self._include_stack.pop()
            self.source, self.base_dir = source, base_dir

    def cmd_use(self, command: ast.Command) -> list[ir.Block]:
        target = command.body.text.strip() if isinstance(command.body, ast.VerbatimBody) else ""
        if target.startswith("std:"):
            self.use_std(target.removeprefix("std:"), command)
            return []
        path = self.path_value(command)
        if path is None:
            return []
        if target.endswith(STYLESHEET_SUFFIX):
            try:
                self.stylesheets.add(parse_stylesheet(Source.from_path(path)))
            except (OSError, UnicodeDecodeError) as e:
                reason = e.strerror if isinstance(e, OSError) else "not valid UTF-8"
                self.report(
                    Code.STYLESHEET_NOT_FOUND,
                    f"cannot read style sheet `{target}`: {reason}",
                    command.span,
                    help=f"paths are relative to {self.base_dir}",
                )
            return []
        result = self.load(path, command)
        if result is not None:
            self.macro_file(result, path.resolve().parent)
        return []

    def use_std(self, module: str, command: ast.Command) -> None:
        """\\use{std:name}: a macro file from the standard library (wordgenx/std/name.wgx)."""
        if module in self._std_loaded:
            return
        available = std_modules()
        if module not in available:
            close = difflib.get_close_matches(module, available, n=1)
            self.report(
                Code.INCLUDE,
                f"unknown standard library module `std:{module}`",
                command.span,
                f"did you mean `std:{close[0]}`?" if close else "",
                help=f"available: {', '.join(f'std:{m}' for m in available)}",
            )
            return
        self._std_loaded.add(module)
        source = Source(
            f"std:{module}", (resources.files("wordgenx.std") / f"{module}.wgx").read_text("utf-8")
        )
        result = parse(source)
        self.diagnostics.extend((source, d) for d in result.diagnostics)
        self.macro_file(result, self.base_dir)

    def macro_file(self, result: ParseResult, base_dir: Path) -> None:
        """Register a macro file's definitions; other content is ignored with a warning."""
        source, previous_dir = self.source, self.base_dir
        self.source, self.base_dir = result.source, base_dir
        try:
            for block in result.ast.blocks:
                if not isinstance(block, ast.Paragraph):
                    self.warn(
                        Code.MISPLACED,
                        "a macro file only defines macros; this is ignored",
                        block.span,
                    )
                    continue
                for item in self.expand(block.content):
                    if isinstance(item, ast.Command) and item.name in ("use", "styles"):
                        self.block_command(item)
                    elif not _is_blank(item):
                        self.warn(
                            Code.MISPLACED,
                            "a macro file only defines macros; this is ignored",
                            item.span,
                        )
        finally:
            self.source, self.base_dir = source, previous_dir

    def cmd_styles(self, command: ast.Command) -> list[ir.Block]:
        body = command.body
        if not isinstance(body, ast.VerbatimBody):
            self.report(
                Code.INVALID_ARGUMENT, "\\styles needs a body: \\styles{ ... }", command.span
            )
            return []
        text = self.source.text
        closed = text[body.span.end - 1 : body.span.end] == "}" and len(body.span) > 1
        end = body.span.end - 1 if closed else body.span.end
        self.stylesheets.add(parse_stylesheet(self.source, body.span.start + 1, end))
        return []

    def cmd_document(self, command: ast.Command) -> list[ir.Block]:
        self.no_body(command)
        for option in command.options.items if command.options else ():
            text = self.text_value(option.value, command, option.key or "option")
            if option.key == "lang" and text:
                self.meta = replace(self.meta, language=text)
            elif option.key == "smart-quotes" and text in ("on", "off", "true", "false"):
                self.smart_quotes = text in ("on", "true")
            elif option.key == "template":
                self.warn(
                    Code.NOT_SUPPORTED_YET,
                    "templates are not supported yet",
                    option.span,
                    help="planned for milestone M2",
                )
            else:
                self.report(
                    Code.INVALID_ARGUMENT,
                    "unknown \\document option",
                    option.span,
                    help='lang=en-US, smart-quotes=on|off, template="file.dotx"',
                )
        return []

    def cmd_meta(self, command: ast.Command) -> list[ir.Block]:
        self.no_body(command)
        fields_map = {
            "title": "title",
            "subject": "subject",
            "author": "author",
            "description": "description",
            "last-modified-by": "last_modified_by",
        }
        for option in command.options.items if command.options else ():
            key = option.key or ""
            if key in fields_map:
                text = self.text_value(option.value, command, key)
                if text is not None:
                    self.meta = replace(self.meta, **{fields_map[key]: text})
            elif key == "keywords":
                if isinstance(option.value, ast.TupleValue):
                    words = [
                        self.text_value(item.value, command, key) for item in option.value.items
                    ]
                    self.meta = replace(self.meta, keywords=tuple(w for w in words if w))
                elif (text := self.text_value(option.value, command, key)) is not None:
                    self.meta = replace(
                        self.meta, keywords=tuple(w.strip() for w in text.split(","))
                    )
            elif key.startswith("custom."):
                self.warn(
                    Code.NOT_SUPPORTED_YET,
                    "custom document properties are not supported yet",
                    option.span,
                    help="planned for milestone M2",
                )
            else:
                self.report(
                    Code.INVALID_ARGUMENT,
                    f"unknown \\meta field `{key}`",
                    option.span,
                    help="title, subject, author, keywords, description, last-modified-by",
                )
        return []

    def cmd_settings(self, command: ast.Command) -> list[ir.Block]:
        self.no_body(command)
        for option in command.options.items if command.options else ():
            if option.key is None:
                flag = self.text_value(option.value, command, "setting")
                if flag == "even-odd-headers":
                    self.settings = replace(self.settings, even_and_odd_headers=True)
                    continue
                self.warn(
                    Code.NOT_SUPPORTED_YET, f"setting `{flag}` is not supported yet", option.span
                )
                continue
            if option.key in ("footnotes", "endnotes"):
                notes = self.note_settings(option.key[:-1], option.value, command)
                self.settings = replace(self.settings, **{option.key: notes})
                continue
            if option.key not in ("update-fields-on-open", "default-tab-stop"):
                self.warn(
                    Code.NOT_SUPPORTED_YET,
                    f"setting `{option.key}` is not supported yet",
                    option.span,
                )
                continue
            text = self.text_value(option.value, command, option.key)
            if option.key == "update-fields-on-open" and text in ("on", "off", "true", "false"):
                self.settings = replace(self.settings, update_fields_on_open=text in ("on", "true"))
            elif option.key == "default-tab-stop" and text is not None:
                if (length := self.length(text, option.value.span)) is not None:
                    self.settings = replace(self.settings, default_tab_stop=length)
            else:
                self.warn(
                    Code.NOT_SUPPORTED_YET,
                    f"setting `{option.key}` is not supported yet",
                    option.span,
                )
        return []

    def note_settings(self, kind: str, value: ast.Value, command: ast.Command) -> ir.NoteSettings:
        """footnotes=(format=lower-roman, restart=each-section, position=page-bottom, start=1)."""
        notes = self.settings.notes(kind)
        items = (
            value.items
            if isinstance(value, ast.TupleValue)
            else (ast.Option(None, value, span=value.span),)
        )
        positions = NOTE_POSITIONS[kind]
        restarts = {
            k: v for k, v in NOTE_RESTARTS.items() if kind == "footnote" or k != "each-page"
        }
        for item in items:
            text = self.text_value(item.value, command, f"{kind}s") or ""
            if item.key in (None, "format") and text in NOTE_FORMATS:
                notes = replace(notes, format=NOTE_FORMATS[text])
            elif item.key == "restart" and text in restarts:
                notes = replace(notes, restart=restarts[text])
            elif item.key == "position" and text in positions:
                notes = replace(notes, position=positions[text])
            elif item.key == "start" and text.isdigit() and int(text) > 0:
                notes = replace(notes, start=int(text))
            else:
                self.report(
                    Code.INVALID_ARGUMENT,
                    f"invalid {kind}s setting",
                    item.span,
                    help=f"format={'|'.join(NOTE_FORMATS)}, restart={'|'.join(restarts)}, "
                    f"position={'|'.join(positions)}, start=N",
                )
        return notes

    def cmd_section(self, command: ast.Command) -> list[ir.Block]:
        """Page setup of the current section; after content, also a section break."""
        if self._container_depth > 0:
            self.report(
                Code.MISPLACED,
                "\\section cannot appear inside a table, header, footer or note",
                command.span,
            )
            return []
        out: list[ir.Block] = []
        breaking = self.has_content
        if breaking:
            out.append(ir.SectionBreak(self.section))
            # A new section keeps the page setup; headers and footers are inherited (not
            # repeated), page numbering continues unless restarted, and a different first
            # page applies only where it is asked for.
            self.section = replace(
                self.section,
                headers=(),
                footers=(),
                page_number_start=None,
                start="next-page",
                title_page=False,
            )
        section = self.section
        page = section.page
        orientation: str | None = None
        link_headers = True
        for option in command.options.items if command.options else ():
            key, value = option.key, option.value
            if key is None:
                flag = self.text_value(value, command, "option")
                if flag == "title-page":
                    section = replace(section, title_page=True)
                elif flag == "separator":
                    section = replace(section, column_separator=True)
                else:
                    self.warn(
                        Code.NOT_SUPPORTED_YET,
                        f"section option `{flag}` is not supported yet",
                        option.span,
                    )
            elif key == "size":
                page = self.page_size(page, value, command)
            elif key == "orientation":
                orientation = self.text_value(value, command, "orientation")
                if orientation not in ("portrait", "landscape"):
                    self.report(
                        Code.INVALID_ARGUMENT, "orientation is portrait or landscape", value.span
                    )
                    orientation = None
            elif key == "margins":
                page = self.margins(page, value, command)
            elif key in ("gutter", "header-distance", "footer-distance"):
                length = self.length(self.text_value(value, command, key) or "", value.span)
                if length is not None:
                    page = replace(page, **{key.replace("-", "_"): length})
            elif key == "break":
                start = self.text_value(value, command, key)
                if start in ir.SECTION_STARTS:
                    section = replace(section, start=start)
                else:
                    self.report(
                        Code.INVALID_ARGUMENT,
                        "unknown section break",
                        value.span,
                        help=", ".join(ir.SECTION_STARTS),
                    )
            elif key == "page-numbers":
                section = self.page_numbers(section, value, command)
            elif key == "columns":
                section = self.columns(section, value, command)
            elif key == "link-headers":
                link_headers = self.text_value(value, command, key) not in ("false", "off", "no")
            else:
                self.warn(
                    Code.NOT_SUPPORTED_YET,
                    f"section option `{key}` is not supported yet",
                    option.span,
                )
        if orientation is not None and (orientation == "landscape") != page.landscape:
            page = replace(page, width=page.height, height=page.width)
        if breaking and not link_headers:
            # Unlinked: every kind defined so far gets an explicit empty part (unless redefined).
            section = replace(
                section, headers=self.empty_parts("header"), footers=self.empty_parts("footer")
            )
        self.section = replace(section, page=page)
        return out

    def empty_parts(self, family: str) -> ir.HeaderFooterParts:
        kinds = [k.split(":")[1] for k in self.header_kinds if k.startswith(family + ":")]
        return tuple((kind, ()) for kind in ir.HEADER_KINDS if kind in kinds)

    def page_size(self, page: ir.PageSetup, value: ast.Value, command: ast.Command) -> ir.PageSetup:
        if isinstance(value, ast.TupleValue) and len(value.items) == 2:
            w = self.length(
                self.text_value(value.items[0].value, command, "size") or "", value.span
            )
            h = self.length(
                self.text_value(value.items[1].value, command, "size") or "", value.span
            )
            return replace(page, width=w, height=h) if w and h else page
        name = self.text_value(value, command, "size")
        if name in PAGE_SIZES:
            width, height = PAGE_SIZES[name]
            return replace(page, width=width, height=height)
        self.report(
            Code.INVALID_ARGUMENT,
            "unknown page size",
            value.span,
            help=f"{', '.join(PAGE_SIZES)} or (width, height)",
        )
        return page

    def page_numbers(
        self, section: ir.Section, value: ast.Value, command: ast.Command
    ) -> ir.Section:
        """page-numbers=lower-roman or page-numbers=(format=decimal, start=1)."""
        items = (
            value.items
            if isinstance(value, ast.TupleValue)
            else (ast.Option(None, value, span=value.span),)
        )
        for item in items:
            text = self.text_value(item.value, command, "page-numbers") or ""
            if item.key in (None, "format") and text in ir.PAGE_NUMBER_FORMATS:
                section = replace(section, page_number_format=text)
            elif item.key == "start" and text.isdigit():
                section = replace(section, page_number_start=int(text))
            else:
                self.report(
                    Code.INVALID_ARGUMENT,
                    "invalid page-numbers",
                    item.span,
                    help="e.g. page-numbers=(format=lower-roman, start=1); formats: "
                    + ", ".join(ir.PAGE_NUMBER_FORMATS),
                )
        return section

    def columns(self, section: ir.Section, value: ast.Value, command: ast.Command) -> ir.Section:
        """columns=2 or columns=(count=2, gap=1cm, separator)."""
        items = (
            value.items
            if isinstance(value, ast.TupleValue)
            else (ast.Option("count", value, span=value.span),)
        )
        for item in items:
            text = self.text_value(item.value, command, "columns") or ""
            if item.key == "count" and text.isdigit() and 1 <= int(text) <= 45:
                section = replace(section, columns=int(text))
            elif item.key == "gap" and (gap := self.length(text, item.span)) is not None:
                section = replace(section, column_gap=gap)
            elif item.key is None and text == "separator":
                section = replace(section, column_separator=True)
            else:
                self.report(
                    Code.INVALID_ARGUMENT,
                    "invalid columns",
                    item.span,
                    help="columns=2 or columns=(count=2, gap=1cm, separator)",
                )
        return section

    def cmd_header(self, command: ast.Command) -> list[ir.Block]:
        """\\header[kind]{...} and \\footer[kind]{...} for the current section."""
        family = command.name
        positional = self.positional(command, 1)
        kind = positional[0] if positional else "default"
        if kind not in ir.HEADER_KINDS:
            self.report(
                Code.INVALID_ARGUMENT,
                f"unknown {family} kind `{kind}`",
                command.options.span if command.options else command.span,
                help="default, first or even",
            )
            return []
        self._container_depth += 1
        self._part = family
        try:
            blocks = self.blocks(self.markup_blocks(command)) if command.body is not None else []
        finally:
            self._container_depth -= 1
            self._part = None
        style = "Header" if family == "header" else "Footer"
        self.use_style(style, ir.StyleKind.PARAGRAPH, command.span)
        content = ir.map_paragraphs(
            tuple(blocks), lambda p: p if p.style else replace(p, style=style)
        )
        existing = self.section.headers if family == "header" else self.section.footers
        parts = dict(existing)
        parts[kind] = content
        ordered = tuple((k, parts[k]) for k in ir.HEADER_KINDS if k in parts)
        section = replace(self.section, **{f"{family}s": ordered})
        if kind == "first":
            section = replace(section, title_page=True)
        if kind == "even":
            self.even_headers = True
        self.header_kinds.add(f"{family}:{kind}")
        self.section = section
        return []

    def cmd_tabs(self, command: ast.Command) -> list[ir.Block]:
        """\\tabs[(8cm, center), (16cm, right, leader=dot)]{...}: tab stops for the paragraphs."""
        texts: list[str] = []
        for option in command.options.items if command.options else ():
            if option.key is not None:
                self.report(
                    Code.INVALID_ARGUMENT,
                    "\\tabs takes tab stops: \\tabs[(8cm, center), (16cm, right)]",
                    option.span,
                )
                continue
            if isinstance(option.value, ast.TupleValue):
                words = [
                    (f"{item.key}=" if item.key else "")
                    + (self.text_value(item.value, command, "tab") or "")
                    for item in option.value.items
                ]
                texts.append(" ".join(words))
            elif (text := self.text_value(option.value, command, "tab")) is not None:
                texts.append(text)
        paragraphs = self.blocks(self.markup_blocks(command))
        try:
            stops = TabStop.parse_list(", ".join(texts))
        except InvalidValueError as e:
            span = command.options.span if command.options else command.span
            self.report(Code.INVALID_ARGUMENT, str(e), span)
            return paragraphs
        tabs = PropertySet(Target.PARAGRAPH, {"tabs": stops})
        return list(
            ir.map_paragraphs(tuple(paragraphs), lambda p: replace(p, props=tabs.merged(p.props)))
        )

    def margins(self, page: ir.PageSetup, value: ast.Value, command: ast.Command) -> ir.PageSetup:
        sides = {
            "top": "margin_top",
            "bottom": "margin_bottom",
            "left": "margin_left",
            "right": "margin_right",
            "inside": "margin_left",
            "outside": "margin_right",
        }
        if not isinstance(value, ast.TupleValue):
            length = self.length(self.text_value(value, command, "margins") or "", value.span)
            if length is None:
                return page
            return replace(
                page,
                margin_top=length,
                margin_bottom=length,
                margin_left=length,
                margin_right=length,
            )
        for item in value.items:
            length = self.length(self.text_value(item.value, command, "margins") or "", item.span)
            if length is None:
                continue
            if item.key == "all":
                page = replace(
                    page,
                    margin_top=length,
                    margin_bottom=length,
                    margin_left=length,
                    margin_right=length,
                )
            elif item.key in sides:
                if item.key in ("inside", "outside"):
                    self.warn(
                        Code.NOT_SUPPORTED_YET,
                        "mirrored margins are not supported yet; "
                        f"{item.key} is used as {'left' if item.key == 'inside' else 'right'}",
                        item.span,
                    )
                page = replace(page, **{sides[item.key]: length})
            else:
                self.report(
                    Code.INVALID_ARGUMENT,
                    "unknown margin",
                    item.span,
                    help="all, top, bottom, left, right",
                )
        return page

    # --- tables ---------------------------------------------------------------------------------

    def cmd_table(self, command: ast.Command) -> list[ir.Block]:
        self.has_content = True
        spec = self.table_spec(command)
        rows: list[RowSpec] = []
        for block in self.markup_blocks(command):
            if isinstance(block, ast.PipeTable):
                rows.extend(self.pipe_rows(block))
            elif isinstance(block, ast.Paragraph):
                for item in self.expand(block.content):
                    if isinstance(item, ast.Command) and item.name == "row":
                        rows.append(self.row_spec(item))
                    elif not _is_blank(item):
                        self.misplaced_in_table(item.span)
            else:
                self.misplaced_in_table(block.span)
        table = self.make_table(spec, rows, command.span)
        return [table] if table is not None else []

    def misplaced_in_table(self, span: Span) -> None:
        self.report(
            Code.MISPLACED,
            "a \\table contains pipe rows (| a | b |) or \\row commands",
            span,
            help="put other content inside a \\cell",
        )

    def table_spec(self, command: ast.Command) -> TableSpec:
        spec = TableSpec()
        for option in command.options.items if command.options else ():
            key, value = option.key, option.value
            if key == "style":
                name = self.text_value(value, command, key)
                if name == "none":
                    spec.style = None
                elif name:
                    spec.style = name
                    spec.style_span = value.span
            elif key == "header-rows":
                text = self.text_value(value, command, key) or ""
                if text.isdigit():
                    spec.header_rows = int(text)
                else:
                    self.report(
                        Code.INVALID_ARGUMENT, "header-rows must be a whole number", value.span
                    )
            elif key == "widths":
                spec.widths = self.width_list(value, command)
            elif key == "align":
                align = self.text_value(value, command, key)
                if align in ("left", "center", "right"):
                    spec.align = align
                else:
                    self.report(
                        Code.INVALID_ARGUMENT, "table align is left, center or right", value.span
                    )
            elif key == "columns":
                items = value.items if isinstance(value, ast.TupleValue) else ()
                words = [self.text_value(item.value, command, key) or "" for item in items]
                bad = [w for w in words if w not in ALIGN_WORDS | {"decimal"}]
                if not words or bad:
                    self.report(
                        Code.INVALID_ARGUMENT,
                        "columns is a tuple of alignments",
                        value.span,
                        help="e.g. columns=(left, right, center)",
                    )
                else:
                    spec.columns = words
            elif key == "look":
                spec.look = self.table_look(value, command)
            elif key is not None and (key in SHORTHANDS or key in REGISTRY[Target.TABLE]):
                spec.props = self.registry_option(Target.TABLE, key, value, spec.props)
            else:
                self.report(
                    Code.INVALID_ARGUMENT,
                    f"unknown \\table option `{key}`",
                    option.span,
                    help="style, header-rows, widths, align, columns, look, borders, "
                    "cell-margins, border-top, ... (use \\caption for captions)",
                )
        return spec

    def table_look(self, value: ast.Value, command: ast.Command) -> frozenset[str] | None:
        """look=(header-row, banded-rows) or look=none: which table-style parts apply."""
        items = (
            value.items
            if isinstance(value, ast.TupleValue)
            else (ast.Option(None, value, span=value.span),)
        )
        flags: set[str] = set()
        for item in items:
            word = self.text_value(item.value, command, "look") or ""
            word = {"first-row": "header-row"}.get(word, word)
            if item.key is None and word in ir.TABLE_LOOK:
                flags.add(word)
            elif not (item.key is None and word == "none" and len(items) == 1):
                self.report(
                    Code.INVALID_ARGUMENT,
                    f"invalid look `{word}`",
                    item.span,
                    help=f"look=none or a tuple of {', '.join(ir.TABLE_LOOK)}",
                )
                return None
        return frozenset(flags)

    def registry_option(
        self, target: Target, key: str, value: ast.Value, props: PropertySet
    ) -> PropertySet:
        """`props` plus a table or cell property (or shorthand) given as a command option."""
        try:
            return props.merged(PropertySet(target, {key: _value_text(value)}))
        except (InvalidValueError, UnknownPropertyError) as e:
            self.report(Code.INVALID_ARGUMENT, str(e), value.span)
            return props

    def width_list(
        self, value: ast.Value, command: ast.Command
    ) -> list[tuple[str, Length | Decimal]] | None:
        if not isinstance(value, ast.TupleValue) or not value.items:
            self.report(
                Code.INVALID_ARGUMENT, "widths is a tuple: widths=(4cm, 1fr, 25%)", value.span
            )
            return None
        widths: list[tuple[str, Length | Decimal]] = []
        for item in value.items:
            text = (self.text_value(item.value, command, "widths") or "").strip()
            try:
                if text == "auto":
                    widths.append(("fraction", Decimal(1)))
                elif text.endswith("fr"):
                    widths.append(("fraction", Decimal(text[:-2] or "1")))
                elif text.endswith("%"):
                    widths.append(("percent", Decimal(text[:-1])))
                else:
                    widths.append(("length", Length.parse(text)))
            except (InvalidValueError, ArithmeticError):
                self.report(
                    Code.INVALID_ARGUMENT,
                    f"invalid column width `{text}`",
                    item.span,
                    help="a length (4cm), a percentage of the text width (25%), "
                    "or a fraction (1fr)",
                )
                return None
        return widths

    def pipe_rows(self, table: ast.PipeTable) -> list[RowSpec]:
        rows: list[RowSpec] = []
        for row in table.rows:
            cells: list[CellSpec] = []
            covered = 0
            for pipe_cell in row.cells:
                content = self.expand(pipe_cell.content)
                text = [c for c in content if not _is_blank(c)]
                if covered:
                    covered -= 1
                    if text:
                        self.report(
                            Code.INVALID_ARGUMENT,
                            "this cell is covered by a colspan and must be empty",
                            pipe_cell.span,
                        )
                    continue
                if len(text) == 1 and isinstance(text[0], ast.Command) and text[0].name == "cell":
                    cell = self.cell_spec(text[0])
                    covered = cell.colspan - 1
                elif text:
                    pieces = self.spliced_inlines(content, Format(), pipe_cell.span)
                    cell = CellSpec([ir.Paragraph(self.assemble(pieces))], pipe_cell.span)
                else:
                    cell = CellSpec([], pipe_cell.span)
                cells.append(cell)
            rows.append(RowSpec(cells, row.span))
        return rows

    def row_spec(self, command: ast.Command) -> RowSpec:
        row = RowSpec([], command.span)
        for option in command.options.items if command.options else ():
            key, value = option.key, option.value
            flag = self.text_value(value, command, "option") if key is None else None
            if flag == "header":
                row.header = True
            elif flag == "cant-split":
                row.cant_split = True
            elif key == "height":
                parts = value.items if isinstance(value, ast.TupleValue) else (option,)
                texts = [self.text_value(p.value, command, key) or "" for p in parts]
                row.height = self.length(texts[0], value.span)
                if len(texts) > 1:
                    if texts[1] not in ("exact", "at-least"):
                        self.report(
                            Code.INVALID_ARGUMENT,
                            "row height rule is exact or at-least",
                            value.span,
                        )
                    row.exact = texts[1] == "exact"
            else:
                self.report(
                    Code.INVALID_ARGUMENT,
                    "unknown \\row option",
                    option.span,
                    help="header, cant-split, height=1cm or height=(1cm, exact)",
                )
        for block in self.markup_blocks(command):
            if not isinstance(block, ast.Paragraph):
                self.report(Code.MISPLACED, "a \\row contains \\cell commands", block.span)
                continue
            for item in self.expand(block.content):
                if isinstance(item, ast.Command) and item.name == "cell":
                    row.cells.append(self.cell_spec(item))
                elif not _is_blank(item):
                    self.report(
                        Code.MISPLACED,
                        "a \\row contains \\cell commands",
                        item.span,
                        help="put the text inside \\cell{...}",
                    )
        return row

    def cell_spec(self, command: ast.Command) -> CellSpec:
        cell = CellSpec([], command.span)
        for option in command.options.items if command.options else ():
            key, value = option.key, option.value
            text = (
                ""
                if isinstance(value, ast.TupleValue)
                else self.text_value(value, command, key or "option") or ""
            )
            if key in ("colspan", "rowspan"):
                if text.isdigit() and int(text) >= 1:
                    setattr(cell, key, int(text))
                else:
                    self.report(
                        Code.INVALID_ARGUMENT,
                        f"{key} must be a whole number of at least 1",
                        value.span,
                    )
            elif key in ("shade", "shading"):
                cell.props = self.registry_option(Target.CELL, "shading", value, cell.props)
            elif key == "valign":
                if text in CELL_ALIGN:
                    valign = PropertySet(Target.CELL, {"valign": CELL_ALIGN[text]})
                    cell.props = cell.props.merged(valign)
                else:
                    self.report(
                        Code.INVALID_ARGUMENT, "valign is top, center or bottom", value.span
                    )
            elif key is not None and (key in SHORTHANDS or key in REGISTRY[Target.CELL]):
                cell.props = self.registry_option(Target.CELL, key, value, cell.props)
            elif key == "align":
                if text in ALIGN_WORDS:
                    cell.align = text
                else:
                    self.report(
                        Code.INVALID_ARGUMENT, "align is left, center, right or justify", value.span
                    )
            else:
                self.report(
                    Code.INVALID_ARGUMENT,
                    "unknown \\cell option",
                    option.span,
                    help="colspan, rowspan, shade, valign, align, borders, border-top, ...",
                )
        if command.body is not None:
            self._container_depth += 1
            try:
                cell.content = self.blocks(self.markup_blocks(command))
            finally:
                self._container_depth -= 1
        return cell

    def make_table(self, spec: TableSpec, rows: list[RowSpec], span: Span) -> ir.Table | None:
        if not rows:
            self.report(Code.INVALID_ARGUMENT, "a table needs at least one row", span)
            return None
        repeat_header = False
        if spec.style is not None:
            self.use_style(spec.style, ir.StyleKind.TABLE, spec.style_span or span)
            style = self.compiled_style(spec.style)
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
            self.report(
                Code.INVALID_ARGUMENT,
                f"the table has {max(extents)} columns but widths lists {columns}",
                span,
            )
            return None
        for row, extent in zip(rows, extents, strict=True):  # short rows get empty cells
            row.cells.extend(CellSpec([], row.span) for _ in range(columns - extent))

        widths = self.resolve_widths(spec, columns, span)
        if widths is None:
            return None
        if "decimal" in spec.columns:
            self.warn(
                Code.NOT_SUPPORTED_YET,
                "decimal column alignment is not supported yet; right is used",
                span,
                help="planned for milestone M2 (tab stops)",
            )
        alignments = ["right" if a == "decimal" else a for a in spec.columns]

        ir_rows: list[ir.Row] = []
        for r, row in enumerate(rows):
            column = 0
            cells: list[ir.Cell] = []
            for cell in row.cells:
                align = cell.align or (alignments[column] if column < len(alignments) else None)
                content = tuple(cell.content)
                if align is not None:
                    content = _align_paragraphs(content, align)
                cells.append(ir.Cell(content, cell.colspan, cell.rowspan, cell.props))
                column += cell.colspan
            header = row.header or r < spec.header_rows or (repeat_header and r == 0)
            ir_rows.append(ir.Row(tuple(cells), header, row.height, row.exact, row.cant_split))
        try:
            look = ir.DEFAULT_TABLE_LOOK if spec.look is None else spec.look
            return ir.Table(tuple(ir_rows), widths, spec.style, spec.align, spec.props, look)
        except ValueError as e:
            self.report(Code.INVALID_ARGUMENT, str(e), span, help="check colspan and rowspan")
            return None

    @property
    def page(self) -> ir.PageSetup:
        return self.section.page

    def text_width(self) -> Length:
        page = self.page
        return Length(
            page.width.emu - page.margin_left.emu - page.margin_right.emu - page.gutter.emu
        )

    def resolve_widths(
        self, spec: TableSpec, columns: int, span: Span
    ) -> tuple[Length, ...] | None:
        available = self.text_width().emu
        entries = spec.widths or [("fraction", Decimal(1))] * columns
        fixed = 0
        fractions = Decimal(0)
        for kind, amount in entries:
            if kind == "length":
                assert isinstance(amount, Length)
                fixed += amount.emu
            elif kind == "percent":
                assert isinstance(amount, Decimal)
                fixed += int(available * amount / 100)
            else:
                assert isinstance(amount, Decimal)
                fractions += amount
        remaining = available - fixed
        if fractions and remaining <= 0:
            self.report(
                Code.INVALID_ARGUMENT, "fixed column widths leave no room for the 1fr columns", span
            )
            return None
        widths: list[Length] = []
        for kind, amount in entries:
            if kind == "length":
                assert isinstance(amount, Length)
                widths.append(amount)
            elif kind == "percent":
                assert isinstance(amount, Decimal)
                widths.append(Length(int(available * amount / 100)))
            else:
                assert isinstance(amount, Decimal)
                widths.append(Length(int(remaining * amount / fractions)))
        if any(w.emu <= 0 for w in widths):
            self.report(Code.INVALID_ARGUMENT, "column widths must be positive", span)
            return None
        return tuple(widths)

    # --- fields --------------------------------------------------------------------------------

    def inline_field(self, command: ast.Command, fmt: Format) -> list[Piece]:
        """\\field[result="..."]{CODE}: any Word field; nested fields in { } as in Word."""
        code = command.body.text.strip() if isinstance(command.body, ast.VerbatimBody) else ""
        if not code:
            self.report(
                Code.INVALID_ARGUMENT, "\\field needs a field code: \\field{PAGE}", command.span
            )
            return []
        result: str | None = None
        for option in command.options.items if command.options else ():
            if option.key == "result":
                result = self.text_value(option.value, command, "result")
            else:
                self.report(
                    Code.INVALID_ARGUMENT, "unknown \\field option", option.span, help="result"
                )
        try:
            instruction = parse_instruction(code)
        except ValueError as e:
            self.report(Code.INVALID_ARGUMENT, str(e), command.span)
            return []
        name = field_name(code)
        dirty = False
        if result is None:
            known = self.known_field_result(name)
            dirty = known is None  # Word computes it when the document opens
            result = known or ""
        field = ir.Field(merge_text((" ", *instruction, " ")), result, fmt.props, fmt.style, dirty)
        return [Piece("", fmt, "field", field=field)]

    def known_field_result(self, name: str) -> str | None:
        if name in ("PAGE", "SECTIONPAGES", "NUMPAGES"):
            return "1"
        if name in META_FIELDS:
            value: object = getattr(self.meta, META_FIELDS[name])
            if isinstance(value, tuple):
                return ", ".join(str(v) for v in value)  # pyright: ignore[reportUnknownVariableType, reportUnknownArgumentType]
            return value if isinstance(value, str) else None
        return None

    def inline_date(self, command: ast.Command, fmt: Format) -> list[Piece]:
        """\\date[kind=fixed|today|saved|created, format="d MMMM yyyy"]."""
        kind, picture = "fixed", "d MMMM yyyy"
        for option in command.options.items if command.options else ():
            text = self.text_value(option.value, command, option.key or "option") or ""
            if option.key == "kind" and text in ("fixed", *DATE_FIELDS):
                kind = text
            elif option.key == "format" and text:
                picture = text
            else:
                self.report(
                    Code.INVALID_ARGUMENT,
                    "invalid \\date option",
                    option.span,
                    help='kind=fixed|today|saved|created, format="d MMMM yyyy"',
                )
        self.no_body(command)
        text = format_date(build_time(), picture)
        if kind == "fixed":
            return [Piece(text, fmt, "code")]
        code = f' {DATE_FIELDS[kind]} \\@ "{picture}" '
        return [Piece("", fmt, "field", field=ir.Field((code,), text, fmt.props, fmt.style))]

    # --- images --------------------------------------------------------------------------------

    def image_width(self, text: str, span: Span) -> Length | None:
        """A length, or a percentage of the text width."""
        if text.endswith("%"):
            try:
                return Length(int(self.text_width().emu * Decimal(text[:-1]) / 100))
            except ArithmeticError:
                self.report(Code.INVALID_ARGUMENT, f"invalid width `{text}`", span)
                return None
        return self.length(text, span)

    def inline_image(self, command: ast.Command, fmt: Format) -> list[Piece]:
        path = self.path_value(command)
        if path is None:
            return []
        width: Length | None = None
        height: Length | None = None
        description = name = ""
        for option in command.options.items if command.options else ():
            text = self.text_value(option.value, command, option.key or "option") or ""
            if option.key == "width":
                width = self.image_width(text, option.value.span)
            elif option.key == "height":
                height = self.length(text, option.value.span)
            elif option.key == "alt":
                description = text
            elif option.key == "name":
                name = text
            else:
                self.report(
                    Code.INVALID_ARGUMENT,
                    "unknown \\image option",
                    option.span,
                    help="width, height, alt, name",
                )
        try:
            data = path.read_bytes()
        except OSError as e:
            self.report(
                Code.INVALID_ARGUMENT,
                f"cannot read image `{path.name}`: {e.strerror}",
                command.span,
                help=f"paths are relative to {self.base_dir}",
            )
            return []
        try:
            with PILImage.open(io.BytesIO(data)) as picture:
                image_format = (picture.format or "").lower()
                pixels = picture.size
                dpi = picture.info.get("dpi")
        except (UnidentifiedImageError, OSError):
            self.report(
                Code.INVALID_ARGUMENT, f"`{path.name}` is not an image Word can show", command.span
            )
            return []
        if image_format not in ir.IMAGE_FORMATS:
            self.report(
                Code.INVALID_ARGUMENT,
                f"unsupported image format {image_format.upper() or '?'}",
                command.span,
                help="use PNG, JPEG, GIF or BMP",
            )
            return []
        if width is None and height is None and self._figure is not None:
            width = self._figure.width
        dpi_x, dpi_y = _dpi(dpi)
        natural = (Length.inch(Decimal(pixels[0]) / dpi_x), Length.inch(Decimal(pixels[1]) / dpi_y))
        if width is None and height is None and natural[0].emu > self.text_width().emu:
            width = self.text_width()  # shrink to fit, as Word does on insert
        if width is not None and height is None:
            height = Length(natural[1].emu * width.emu // natural[0].emu)
        elif height is not None and width is None:
            width = Length(natural[0].emu * height.emu // natural[1].emu)
        size = (width or natural[0], height or natural[1])
        try:
            image = ir.Image(data, image_format, size[0], size[1], description, name or path.name)
        except ValueError as e:
            self.report(Code.INVALID_ARGUMENT, str(e), command.span)
            return []
        return [Piece("", fmt, "image", image)]

    # --- labels, references, captions, counters, tables of contents ---------------------------

    def register_label(self, name: str, span: Span, kind: str) -> Label:
        """A new label and its bookmark; a duplicate is reported (and gets a bookmark of its own,
        which nothing refers to)."""
        refs = self.references
        label = Label(name, refs.bookmark(name), kind, span, self.source)
        existing = refs.labels.get(name)
        if existing is None:
            refs.labels[name] = label
        else:
            line = existing.source.position(existing.span.start).line
            self.report(
                Code.DUPLICATE_LABEL,
                f"label `{name}` is defined twice",
                span,
                help=f"first defined at {existing.source.name}:{line}",
            )
        return label

    def make_ref(self, label: str, form: str, span: Span, fmt: Format) -> list[Piece]:
        """A placeholder REF field, resolved once every label is known."""
        ref_id = len(self.references.refs) + 1
        self.references.refs[ref_id] = (span, self.source)
        ref = PendingRef(
            (f" REF {label} ",), "", fmt.props, fmt.style, label=label, form=form, ref_id=ref_id
        )
        return [Piece("", fmt, "field", field=ref)]

    def inline_ref(self, command: ast.Command, fmt: Format) -> list[Piece]:
        r"""\ref[form=number]{label} and \pageref{label}."""
        label = command.body.text.strip() if isinstance(command.body, ast.VerbatimBody) else ""
        if not label:
            self.report(
                Code.INVALID_ARGUMENT,
                f"`\\{command.name}` needs a label: \\{command.name}{{fig:arch}}",
                command.span,
            )
            return []
        form = "page"
        if command.name == "pageref":
            self.no_options(command)
        else:
            form = "default"
            for option in command.options.items if command.options else ():
                text = self.text_value(option.value, command, option.key or "form")
                if option.key in (None, "form") and text in REF_FORMS:
                    form = text
                else:
                    self.report(
                        Code.INVALID_ARGUMENT,
                        "invalid \\ref option",
                        option.span,
                        help=f"form={'|'.join(REF_FORMS)}",
                    )
        return self.make_ref(label, form, command.span, fmt)

    def inline_seq(self, command: ast.Command, fmt: Format) -> list[Piece]:
        r"""\seq[Req, id=req:x, restart=heading-1 | 1, format=decimal]: a SEQ counter."""
        self.no_body(command)
        name = label = restart = None
        label_span = command.span
        number_format = "ARABIC"
        for option in command.options.items if command.options else ():
            text = self.text_value(option.value, command, option.key or "counter")
            if text is None:
                continue
            if option.key is None and name is None:
                name = text
            elif option.key == "id":
                label, label_span = text, option.value.span
            elif option.key == "restart" and (match := re.fullmatch(r"heading-([1-9])", text)):
                restart = f"\\s {match.group(1)}"
            elif option.key == "restart" and text.isdigit():
                restart = f"\\r {text}"
            elif option.key == "format" and text in SEQ_FORMATS:
                number_format = SEQ_FORMATS[text]
            else:
                self.report(
                    Code.INVALID_ARGUMENT,
                    "invalid \\seq option",
                    option.span,
                    help="\\seq[Name, id=label, restart=heading-N|N, "
                    f"format={'|'.join(SEQ_FORMATS)}]",
                )
        if name is None or not SEQ_NAME_RE.fullmatch(name):
            self.report(
                Code.INVALID_ARGUMENT,
                "\\seq needs a counter name (letters, digits, _): \\seq[Req]",
                command.span,
            )
            return []
        code = f" SEQ {name} \\* {number_format} " + (f"{restart} " if restart else "")
        pieces = [Piece("", fmt, "field", field=ir.Field((code,), "1", fmt.props, fmt.style))]
        if label is not None:
            bookmark = self.register_label(label, label_span, "seq").bookmark
            pieces = [
                Piece("", fmt, "mark", mark=ir.BookmarkStart(bookmark)),
                *pieces,
                Piece("", fmt, "mark", mark=ir.BookmarkEnd(bookmark)),
            ]
        return pieces

    def inline_note(self, command: ast.Command, fmt: Format) -> list[Piece]:
        r"""\footnote[id=fn:x]{text} and \endnote{...}: the reference mark here, the text in the
        footnotes or endnotes part. The body may hold several paragraphs, lists and tables."""
        kind = command.name
        if self._part is not None:
            where = "another note" if self._part in ir.NOTE_KINDS else f"a {self._part}"
            article = "an" if kind == "endnote" else "a"
            self.report(Code.MISPLACED, f"{article} {kind} cannot be inside {where}", command.span)
            return []
        label: str | None = None
        label_span = command.span
        for option in command.options.items if command.options else ():
            text = self.text_value(option.value, command, option.key or "option")
            if option.key == "id" and text:
                label, label_span = text, option.value.span
            elif text is not None:
                self.report(
                    Code.INVALID_ARGUMENT, f"invalid \\{kind} option", option.span, help="id=label"
                )
        if not isinstance(command.body, ast.MarkupBody):
            self.report(
                Code.INVALID_ARGUMENT,
                f"`\\{kind}` needs a body: \\{kind}{{text}}",
                command.span,
            )
            return []
        title = kind.capitalize()
        text_style, mark_style = f"{title} Text", f"{title} Reference"
        self.use_style(text_style, ir.StyleKind.PARAGRAPH, command.span)
        self.use_style(mark_style, ir.StyleKind.CHARACTER, command.span)
        previous, following = self._previous, self._next
        self._container_depth += 1
        self._part = kind
        try:
            lowered = self.blocks(command.body.blocks)
        finally:
            self._container_depth -= 1
            self._part = None
            self._previous, self._next = previous, following
        blocks: list[ir.Block] = [
            replace(b, style=text_style)
            if isinstance(b, ir.Paragraph) and b.style is None and b.list_item is None
            else b
            for b in lowered
        ]
        mark = (ir.NoteMark(kind, mark_style), ir.Run(" "))
        first = blocks[0] if blocks else None
        if isinstance(first, ir.Paragraph):
            blocks[0] = replace(first, content=(*mark, *first.content))
        else:
            blocks.insert(0, ir.Paragraph(mark, text_style))
        note = ir.Note(kind, tuple(blocks), fmt.props, mark_style)
        pieces = [Piece("", fmt, "note", note=note)]
        if label is not None:
            bookmark = self.register_label(label, label_span, "note").bookmark
            pieces = [
                Piece("", fmt, "mark", mark=ir.BookmarkStart(bookmark)),
                *pieces,
                Piece("", fmt, "mark", mark=ir.BookmarkEnd(bookmark)),
            ]
        return pieces

    def make_caption(
        self,
        kind: str,
        label: str | None,
        span: Span,
        text: tuple[ir.Inline, ...],
        keep_with_next: bool,
    ) -> ir.Paragraph:
        """A Caption paragraph, "Figure {SEQ Figure} : text", with the bookmarks Word's
        Insert Cross-reference would use: label and number, number only, text only, whole."""
        self.use_style("Caption", ir.StyleKind.PARAGRAPH, span)
        number: tuple[ir.Inline, ...] = (
            ir.Field((f" SEQ {kind.replace(' ', '_')} \\* ARABIC ",), "1"),
        )
        body = text
        separator = (ir.Run(": "),) if text else ()
        if label is not None:
            entry = self.register_label(label, span, "caption")
            refs = self.references
            entry.parts.update(
                number=refs.bookmark(label, "_n"),
                text=refs.bookmark(label, "_t"),
                full=refs.bookmark(label, "_f"),
            )
            number = _marked(entry.parts["number"], number)
            head = _marked(entry.bookmark, (ir.Run(f"{kind} "), *number))
            if text:
                body = _marked(entry.parts["text"], text)
            content = _marked(entry.parts["full"], (*head, *separator, *body))
        else:
            content = _marked(
                self.references.hidden_bookmark(), (ir.Run(f"{kind} "), *number, *separator, *body)
            )
        props = (
            PropertySet(Target.PARAGRAPH, {"keep-with-next": True})
            if keep_with_next
            else EMPTY_PARAGRAPH
        )
        return ir.Paragraph(content, "Caption", props)

    def caption_neighbours(self) -> tuple[str, bool]:
        """(kind, caption is above its subject) inferred from the blocks around a caption."""
        previous, following = self._previous, self._next
        if isinstance(previous, ir.Table):
            return "Table", False
        if isinstance(previous, ir.Paragraph) and previous.style == "Code Block":
            return "Listing", False
        if _has_image(previous):
            return "Figure", False
        if isinstance(following, ast.PipeTable) or _starts_with(following, "table"):
            return "Table", True
        if isinstance(following, ast.CodeBlock):
            return "Listing", True
        return "Figure", True

    def cmd_caption(self, command: ast.Command) -> list[ir.Block]:
        r"""\caption[Table, id=tbl:x]{text}; the kind defaults from the neighbouring block."""
        self.has_content = True
        kind = label = None
        label_span = command.span
        for option in command.options.items if command.options else ():
            text = self.text_value(option.value, command, option.key or "kind")
            if text is None:
                continue
            if option.key is None and kind is None and text.strip():
                kind = text.strip()
            elif option.key == "id" and text:
                label, label_span = text, option.value.span
            else:
                self.report(
                    Code.INVALID_ARGUMENT,
                    "invalid \\caption option",
                    option.span,
                    help="\\caption[Figure|Table|Listing|..., id=label]{text}",
                )
        content = self.expand(self.single_paragraph(command))
        pieces = self.inlines([c for c in content if not isinstance(c, BLOCK_TYPES)], Format())  # type: ignore[misc]
        text = self.assemble(pieces)
        inferred, above = self.caption_neighbours()
        figure = self._figure
        if figure is not None:
            inferred = "Figure"
            if figure.label is not None:
                if label is not None:
                    self.report(
                        Code.INVALID_ARGUMENT,
                        "both the \\figure and its \\caption have an id",
                        label_span,
                        help="keep one of them",
                    )
                else:
                    label, label_span = figure.label, figure.label_span
            figure.captioned = True
        return [self.make_caption(kind or inferred, label, label_span, text, above)]

    def cmd_figure(self, command: ast.Command) -> list[ir.Block]:
        r"""\figure[id=fig:x, width=12cm, align=center]{\image{...} \caption{...}}: the
        image and caption paragraphs, kept together on one page."""
        self.has_content = True
        if self._figure is not None:
            self.report(Code.MISPLACED, "a \\figure cannot contain another \\figure", command.span)
            return []
        extra, props = self.option_props(command, Target.PARAGRAPH, extra=("id", "width"))
        label: str | None = None
        label_span = command.span
        if (value := extra.get("id")) is not None:
            label, label_span = self.text_value(value, command, "id") or None, value.span
        width: Length | None = None
        if (value := extra.get("width")) is not None and (
            text := self.text_value(value, command, "width")
        ):
            width = self.image_width(text, value.span)
        context = FigureContext(label, label_span, width)
        self._figure = context
        try:
            blocks = self.blocks(self.markup_blocks(command))
        finally:
            self._figure = None
        if label is not None and not context.captioned:
            self.report(
                Code.INVALID_ARGUMENT,
                "a labelled \\figure needs a \\caption: references point at the caption",
                label_span,
            )
        base = PropertySet(Target.PARAGRAPH, {"align": "center"}).merged(props)
        keep = base.merged(PropertySet(Target.PARAGRAPH, {"keep-with-next": True}))
        last = max((i for i, b in enumerate(blocks) if isinstance(b, ir.Paragraph)), default=-1)
        out: list[ir.Block] = []
        for i, block in enumerate(blocks):
            if isinstance(block, ir.Paragraph):
                own = block.props
                if i == last:  # the last paragraph does not keep with what follows the figure
                    own = own.merged(PropertySet(Target.PARAGRAPH, {"keep-with-next": False}))
                block = replace(block, props=(keep if i < last else base).merged(own))
            out.append(block)
        return out

    def cmd_toc(self, command: ast.Command) -> list[ir.Block]:
        r"""\toc[levels=1-3, title="Contents"] and \listof[Figure, title=...]."""
        self.has_content = True
        self.no_body(command)
        if self._container_depth or self._figure is not None:
            self.report(
                Code.MISPLACED,
                f"`\\{command.name}` must be in the document body",
                command.span,
            )
            return []
        levels = (1, 3)
        title: str | None = None
        kind: str | None = None
        for option in command.options.items if command.options else ():
            text = self.text_value(option.value, command, option.key or "option")
            if text is None:
                continue
            if option.key == "title":
                title = text
            elif (
                command.name == "toc"
                and option.key == "levels"
                and (match := re.fullmatch(r"([1-9])(?:\s*-\s*([1-9]))?", text))
            ):
                first = int(match.group(1))
                levels = (first, int(match.group(2) or first))
            elif command.name == "toc" and option.key is None and text == "hyperlinks":
                pass  # entries are always links
            elif command.name == "listof" and option.key is None and kind is None and text:
                kind = text
            else:
                self.report(
                    Code.INVALID_ARGUMENT,
                    f"invalid \\{command.name} option",
                    option.span,
                    help='\\toc[levels=1-3, title="Contents"]'
                    if command.name == "toc"
                    else '\\listof[Figure, title="List of Figures"]',
                )
        out: list[ir.Block] = []
        if title:
            self.use_style("TOC Heading", ir.StyleKind.PARAGRAPH, command.span)
            out.append(ir.Paragraph(self.assemble([Piece(title, Format())]), "TOC Heading"))
        if command.name == "toc":
            if levels[0] > levels[1]:
                levels = (levels[1], levels[0])
            field = ir.Field((f' TOC \\o "{levels[0]}-{levels[1]}" \\h \\z \\u ',), dirty=True)
            out.append(PendingToc(field, (), levels=levels))
        else:
            kind = kind or "Figure"
            field = ir.Field((f' TOC \\h \\z \\c "{kind.replace(" ", "_")}" ',), dirty=True)
            out.append(PendingToc(field, (), caption_kind=kind))
        return out

    # --- lists ---------------------------------------------------------------------------------

    def new_instance(self, numbering: str, start: int | None) -> int:
        self._instances += 1
        self._last_instance[numbering] = (self._instances, start)
        return self._instances

    def list_block(self, block: ast.ListBlock, parent: ListContext | None) -> list[ir.Block]:
        if parent is not None and (parent.named or parent.ordered == block.ordered):
            context = replace(parent, level=parent.level + 1, ordered=block.ordered)
        else:
            name = "Numbers" if block.ordered else "Bullets"
            self.list_uses.append((name, block.span, self.source))
            level = parent.level + 1 if parent else 1
            context = ListContext(
                name, self.new_instance(name, None), level, block.ordered, False, None
            )
        if context.level > 9:
            self.report(Code.INVALID_ARGUMENT, "lists nest at most 9 levels deep", block.span)
            context = replace(context, level=9)
        out: list[ir.Block] = []
        for item in block.items:
            out.extend(self.list_item(item, context))
        return out

    def list_item(self, item: ast.ListItem, context: ListContext) -> list[ir.Block]:
        ref = ir.ListItemRef(context.numbering, context.instance, context.level, context.start)
        out: list[ir.Block] = []
        numbered = False
        for block in item.blocks:
            if isinstance(block, ast.ListBlock):
                if not numbered:
                    out.append(ir.Paragraph(list_item=ref))
                    numbered = True
                out.extend(self.list_block(block, context))
                continue
            for lowered in self.block(block, context):
                if not isinstance(lowered, ir.Paragraph):
                    if not numbered:  # an item starting with a table still gets its number
                        out.append(ir.Paragraph(list_item=ref))
                        numbered = True
                    out.append(lowered)
                elif not numbered:
                    out.append(replace(lowered, list_item=ref))
                    numbered = True
                elif isinstance(block, ast.Paragraph):
                    out.append(replace(lowered, list_item=replace(ref, continuation=True)))
                else:
                    out.append(lowered)
        if not numbered:
            out.append(ir.Paragraph(list_item=ref))
        if item.label is not None:
            label = self.register_label(item.label.name, item.label.span, "item")
            for i, lowered in enumerate(out):
                if isinstance(lowered, ir.Paragraph) and lowered.list_item == ref:
                    out[i] = replace(lowered, content=_marked(label.bookmark, lowered.content))
                    break
        return out

    # --- inline content ---------------------------------------------------------------------------

    def inlines(self, content: Iterable[ast.Inline], fmt: Format) -> list[Piece]:
        out: list[Piece] = []
        for node in content:
            out.extend(self.inline(node, fmt))
        return out

    def inline(self, node: ast.Inline, fmt: Format) -> list[Piece]:
        match node:
            case ast.Text():
                return [Piece(node.text, fmt)]
            case ast.SoftBreak():
                return [Piece(" ", fmt)]
            case ast.NonBreakingSpace():
                return [Piece("\u00a0", fmt)]
            case ast.Strong():
                return self.inlines(node.content, self.with_style(fmt, "Strong", node.span))
            case ast.Emph():
                return self.inlines(node.content, self.with_style(fmt, "Emphasis", node.span))
            case ast.CodeSpan():
                return [Piece(node.code, self.with_style(fmt, "Code", node.span), "code")]
            case ast.Autolink():
                link = self.with_link(fmt, node.url, node.span)
                return [Piece(node.url.removeprefix("mailto:"), link, "code")]
            case ast.Link():
                return self.inlines(node.content, self.with_link(fmt, node.target, node.span))
            case ast.Ref():
                return self.make_ref(node.label, "default", node.span, fmt)
            case ast.Param():
                self.report(Code.MACRO, f"`#{node.name}` is only valid inside \\define", node.span)
                return []
            case ast.Define():
                self.register_define(node)
                return []
            case ast.Let():
                self.register_let(node)
                return []
            case ast.Group():
                items = self.splice(node.blocks)
                return self.spliced_inlines(items, fmt, node.span)
            case ast.Command():
                return self.inline_command(node, fmt)

    def spliced_inlines(
        self, items: list[ast.Inline | ast.Block], fmt: Format, span: Span
    ) -> list[Piece]:
        out: list[Piece] = []
        for item in items:
            if isinstance(item, BLOCK_TYPES):  # commands report their own, more specific error
                self.report(
                    Code.MISPLACED, "a block cannot appear inside a paragraph", item.span or span
                )
                continue
            out.extend(self.inline(item, fmt))  # type: ignore[arg-type]
        return out

    def inline_command(self, command: ast.Command, fmt: Format) -> list[Piece]:
        name = command.name
        if name in self.macros:
            return self.spliced_inlines(self.call_macro(command), fmt, command.span)
        if name in BREAK_COMMANDS:
            self.no_body(command)
            self.no_options(command)
            return [Piece("", fmt, "page" if name == "pagebreak" else "column")]
        if name in ("br", "tab"):
            self.no_body(command)
            self.no_options(command)
            return [Piece("\n", fmt, "line")] if name == "br" else [Piece("\t", fmt, "tab")]
        if name in BLOCK_COMMANDS or name in PLANNED_BLOCKS:
            self.report(
                Code.MISPLACED,
                f"`\\{name}` is a block command and cannot appear inside text",
                command.span,
                help="put it on its own line",
            )
            return []
        if name in TABLE_PARTS:
            where = "\\table" if name == "row" else "\\row"
            self.report(
                Code.MISPLACED, f"`\\{name}` is only valid directly inside {where}", command.span
            )
            return []
        if name in PLANNED:
            self.not_yet(command)
            if name in TRANSPARENT and isinstance(command.body, ast.MarkupBody):
                return self.body_inlines(command, fmt)
            return []
        if name not in INLINE_COMMANDS:
            self.unknown_command(command)
            return []
        if name == "image":
            return self.inline_image(command, fmt)
        if name in FIELD_COMMANDS:
            self.no_body(command)
            self.no_options(command)
            field = ir.Field((f" {FIELD_COMMANDS[name]} ",), "1", fmt.props, fmt.style)
            return [Piece("", fmt, "field", field=field)]
        if name == "field":
            return self.inline_field(command, fmt)
        if name == "date":
            return self.inline_date(command, fmt)
        if name in ("ref", "pageref"):
            return self.inline_ref(command, fmt)
        if name == "seq":
            return self.inline_seq(command, fmt)
        if name in ("footnote", "endnote"):
            return self.inline_note(command, fmt)
        if name == "url":
            url = command.body.text.strip() if isinstance(command.body, ast.VerbatimBody) else ""
            self.no_options(command)
            if not url:
                self.report(
                    Code.INVALID_ARGUMENT,
                    "\\url needs an address: \\url{https://...}",
                    command.span,
                )
                return []
            return [
                Piece(url.removeprefix("mailto:"), self.with_link(fmt, url, command.span), "code")
            ]
        if name == "link":
            extra, _ = self.option_props(command, None, extra=("to",))
            target = extra.get("to")
            url = self.text_value(target, command, "to") if target is not None else None
            if not url:
                self.report(
                    Code.INVALID_ARGUMENT,
                    '\\link needs a target: \\link[to="https://..."]{text}',
                    command.span,
                )
                return self.body_inlines(command, fmt)
            return self.body_inlines(command, self.with_link(fmt, url, command.span))
        if name in ("strong", "emph", "code"):
            self.no_options(command)
            style = {"strong": "Strong", "emph": "Emphasis", "code": "Code"}[name]
            return self.body_inlines(command, self.with_style(fmt, style, command.span))
        if name == "style":
            positional = self.positional(command, 1)
            if not positional:
                self.report(
                    Code.INVALID_ARGUMENT,
                    "\\style needs a character style: \\style[Term]{...}",
                    command.span,
                )
                return self.body_inlines(command, fmt)
            return self.body_inlines(command, self.with_style(fmt, positional[0], command.span))
        props = self.formatting(command)
        new_fmt = replace(fmt, props=fmt.props.merged(props)) if props is not None else fmt
        return self.body_inlines(command, new_fmt)

    def formatting(self, command: ast.Command) -> PropertySet | None:
        """Run properties set by \\b, \\i, \\u, \\font, ..."""
        simple = {
            "b": {"bold": True},
            "i": {"italic": True},
            "s": {"strike": True},
            "sup": {"vertical-align": "superscript"},
            "sub": {"vertical-align": "subscript"},
            "hidden": {"hidden": True},
        }
        name = command.name
        values: dict[str, object]
        if name in simple:
            self.no_options(command)
            values = dict(simple[name])
        elif name in ("u", "hl", "lang"):
            positional = self.positional(command, 1)
            key = {"u": "underline", "hl": "highlight", "lang": "lang"}[name]
            default = {"u": "single", "hl": "yellow", "lang": None}[name]
            value = positional[0] if positional else default
            if value is None:
                self.report(
                    Code.INVALID_ARGUMENT,
                    "\\lang needs a language: \\lang[de-DE]{...}",
                    command.span,
                )
                return None
            values = {key: value}
        else:
            assert name == "font"
            _, props = self.option_props(command, Target.RUN)
            return props
        try:
            return PropertySet(Target.RUN, values)
        except InvalidValueError as e:
            self.report(
                Code.INVALID_ARGUMENT,
                str(e),
                command.options.span if command.options else command.span,
            )
            return None

    def body_inlines(self, command: ast.Command, fmt: Format) -> list[Piece]:
        if command.body is None:
            self.report(
                Code.INVALID_ARGUMENT,
                f"`\\{command.name}` needs a body: \\{command.name}{{...}}",
                command.span,
            )
            return []
        if isinstance(command.body, ast.VerbatimBody):
            return [Piece(command.body.text, fmt, "code")]
        match command.body.blocks:
            case ():
                return []
            case (ast.Paragraph() as only,):
                return self.spliced_inlines(self.expand(only.content), fmt, command.span)
            case _:
                self.report(
                    Code.MISPLACED,
                    f"the body of `\\{command.name}` must be inline text",
                    command.span,
                    help="it cannot contain several paragraphs, lists or headings",
                )
                return []

    def with_style(self, fmt: Format, name: str, span: Span) -> Format:
        self.use_style(name, ir.StyleKind.CHARACTER, span)
        if fmt.style is not None and fmt.style != name:
            # Word runs take one character style: keep the outer one's look as direct formatting.
            outer = self.style_run_props(fmt.style)
            return replace(fmt, props=outer.merged(fmt.props), style=name)
        return replace(fmt, style=name)

    def with_link(self, fmt: Format, url: str, span: Span) -> Format:
        self._links += 1
        self.use_style("Hyperlink", ir.StyleKind.CHARACTER, span)
        return replace(fmt, link=(self._links, url), style=fmt.style or "Hyperlink")

    def style_run_props(self, name: str) -> PropertySet:
        style = self.compiled_style(name)
        return style.run if style is not None else EMPTY_RUN

    def compiled_style(self, name: str) -> ir.Style | None:
        """A style defined by the style sheets so far, or the built-in one of that name."""
        style = self.stylesheets._styles.get(style_id(name))  # pyright: ignore[reportPrivateUsage]
        if style is None and name in BUILTIN_STYLES:
            style = BUILTIN_STYLES[name]()
        return style

    # --- paragraph assembly ------------------------------------------------------------------

    def assemble(self, pieces: list[Piece]) -> tuple[ir.Inline, ...]:
        """Smart punctuation, whitespace collapsing, and grouping into runs and hyperlinks."""
        previous = ""
        smart: list[Piece] = []
        for piece in pieces:
            if piece.kind == "text" and self.smart_quotes:
                piece = replace(piece, text=smarten(piece.text, previous))
            if piece.text:
                previous = piece.text[-1]
            elif piece.kind in ("page", "column"):
                previous = ""
            smart.append(piece)

        collapsed: list[Piece] = []
        after_space = True  # the start of a paragraph behaves like a space
        for piece in smart:
            if piece.kind == "text":
                text = WHITESPACE_RE.sub(" ", piece.text)
                if after_space:
                    text = text.lstrip(" ")
                if text:
                    after_space = text.endswith(" ")
                piece = replace(piece, text=text)
            elif piece.kind in ("tab", "line", "page", "column"):
                _trim_trailing(collapsed)
                after_space = True
            elif piece.kind == "mark":
                pass  # a bookmark takes no room
            else:
                after_space = False
            collapsed.append(piece)
        _trim_trailing(collapsed)

        inlines: list[ir.Inline] = []
        link: tuple[int, str] | None = None
        link_runs: list[ir.Run] = []

        def flush_link() -> None:
            nonlocal link, link_runs
            if link is not None and link_runs:
                inlines.append(ir.Hyperlink(link[1], tuple(link_runs)))
            link, link_runs = None, []

        for piece in collapsed:
            if piece.kind in ("page", "column"):
                flush_link()
                inlines.append(ir.Break(piece.kind))
                continue
            special = piece.image or piece.field or piece.mark or piece.note
            if special is not None:  # not inside hyperlinks (yet)
                flush_link()
                inlines.append(special)
                continue
            if not piece.text:
                continue
            if piece.format.link != link:
                flush_link()
                link = piece.format.link
            target = link_runs if link is not None else inlines
            last = target[-1] if target else None
            if (
                isinstance(last, ir.Run)
                and last.props == piece.format.props
                and last.style == piece.format.style
            ):
                target[-1] = replace(last, text=last.text + piece.text)
            else:
                target.append(ir.Run(piece.text, piece.format.props, piece.format.style))  # type: ignore[arg-type]
        flush_link()
        return tuple(inlines)

    # --- options and values ---------------------------------------------------------------------

    def text_value(self, value: ast.Value, command: ast.Command, what: str) -> str | None:
        """The text of an option value: a word, a "string", or plain text in braces.

        The brace form ``alt={a long text ...}`` may span lines; line breaks become spaces.
        """
        if isinstance(value, ast.BareValue | ast.StringValue):
            return value.text
        if isinstance(value, ast.MarkupBody) and (text := _plain_text(value)) is not None:
            return text
        self.report(
            Code.INVALID_ARGUMENT,
            f'`{what}` of `\\{command.name}` must be a word, a "string" or plain text in braces',
            value.span,
        )
        return None

    def length(self, text: str, span: Span) -> Length | None:
        try:
            return Length.parse(text)
        except InvalidValueError as e:
            self.report(Code.INVALID_ARGUMENT, str(e), span)
            return None

    def positional(self, command: ast.Command, count: int) -> list[str]:
        values: list[str] = []
        for option in command.options.items if command.options else ():
            if option.key is not None or len(values) >= count:
                self.report(
                    Code.INVALID_ARGUMENT, f"unexpected option for `\\{command.name}`", option.span
                )
                continue
            text = self.text_value(option.value, command, "option")
            if text is not None:
                values.append(text)
        return values

    def option_props(
        self, command: ast.Command, target: Target | None, extra: Sequence[str] = ()
    ) -> tuple[dict[str, ast.Value], PropertySet]:
        """Split options into `extra` keys and registry properties of `target`."""
        found: dict[str, ast.Value] = {}
        values: dict[str, object] = {}
        empty = EMPTY_RUN if target is Target.RUN else EMPTY_PARAGRAPH
        for option in command.options.items if command.options else ():
            key = option.key
            value: object
            if key is None:
                flag = self.text_value(option.value, command, "option")
                if flag is None:
                    continue
                if target is Target.PARAGRAPH and flag in ALIGN_WORDS:
                    key, value = "align", flag
                else:
                    key, value = flag, True
            elif key in extra:
                found[key] = option.value
                continue
            else:
                text = self.text_value(option.value, command, key)
                if text is None:
                    continue
                value = text
            if target is None or key not in REGISTRY[target]:
                options = [*extra, *(REGISTRY[target] if target else ())]
                close = difflib.get_close_matches(key, options, n=1)
                self.report(
                    Code.INVALID_ARGUMENT,
                    f"unknown option `{key}` for `\\{command.name}`",
                    option.span,
                    f"did you mean `{close[0]}`?" if close else "",
                )
                continue
            try:
                PropertySet(target, {key: value})
            except (InvalidValueError, UnknownPropertyError) as e:
                self.report(Code.INVALID_ARGUMENT, str(e), option.span)
                continue
            values[key] = value
        try:
            props = PropertySet(target, values) if target else empty
        except InvalidValueError as e:
            self.report(Code.INVALID_ARGUMENT, str(e), command.span)
            props = empty
        return found, props

    def no_body(self, command: ast.Command) -> None:
        body = command.body
        if body is None or (isinstance(body, ast.MarkupBody) and not body.blocks):
            return
        self.report(Code.INVALID_ARGUMENT, f"`\\{command.name}` takes no body", body.span)

    def no_options(self, command: ast.Command) -> None:
        if command.options is not None and command.options.items:
            self.report(
                Code.INVALID_ARGUMENT, f"`\\{command.name}` takes no options", command.options.span
            )

    def path_value(self, command: ast.Command) -> Path | None:
        if not isinstance(command.body, ast.VerbatimBody) or not command.body.text.strip():
            self.report(
                Code.INVALID_ARGUMENT,
                f"`\\{command.name}` needs a file name in braces",
                command.span,
            )
            return None
        return self.base_dir / command.body.text.strip()

    def load(self, path: Path, command: ast.Command) -> ParseResult | None:
        if len(self._include_stack) > 16:
            self.report(Code.INCLUDE, "files are included more than 16 levels deep", command.span)
            return None
        try:
            source = Source.from_path(path)
        except (OSError, UnicodeDecodeError) as e:
            reason = e.strerror if isinstance(e, OSError) else "not valid UTF-8"
            self.report(
                Code.INCLUDE,
                f"cannot read `{path.name}`: {reason}",
                command.span,
                help=f"paths are relative to {self.base_dir}",
            )
            return None
        result = parse(source)
        self.diagnostics.extend((source, d) for d in result.diagnostics)
        return result

    # --- styles ---------------------------------------------------------------------------------

    def use_style(self, name: str, kind: ir.StyleKind, span: Span) -> None:
        self.style_uses.append(StyleUse(name, kind, span, self.source))


def _value_text(value: ast.Value) -> str:
    """An option value as style-sheet text: ``(all=(single, 0.5pt), top=none)``."""
    if isinstance(value, ast.TupleValue):
        parts = (
            (f"{item.key}=" if item.key else "") + _value_text(item.value) for item in value.items
        )
        return "(" + ", ".join(parts) + ")"
    if isinstance(value, ast.BareValue | ast.StringValue):
        return value.text
    if isinstance(value, ast.MarkupBody):
        return _plain_text(value) or ""
    return ""


def _plain_text(body: ast.MarkupBody) -> str | None:
    """The text of a markup value holding only plain text (None if it has markup)."""
    parts: list[str] = []
    for block in body.blocks:
        if not isinstance(block, ast.Paragraph):
            return None
        for inline in block.content:
            if isinstance(inline, ast.Text):
                parts.append(inline.text)
            elif isinstance(inline, ast.SoftBreak | ast.NonBreakingSpace):
                parts.append(" ")
            else:
                return None
        parts.append(" ")
    return WHITESPACE_RE.sub(" ", "".join(parts)).strip()


def std_modules() -> list[str]:
    """Names of the standard library modules (``\\use{std:name}``)."""
    root = resources.files("wordgenx.std")
    return sorted(
        entry.name.removesuffix(".wgx") for entry in root.iterdir() if entry.name.endswith(".wgx")
    )


def _align_paragraphs(blocks: tuple[ir.Block, ...], align: str) -> tuple[ir.Block, ...]:
    """Apply a column/cell alignment to paragraphs that do not set their own."""
    aligned = PropertySet(Target.PARAGRAPH, {"align": align})

    def apply(paragraph: ir.Paragraph) -> ir.Paragraph:
        if "align" in paragraph.props:
            return paragraph
        return replace(paragraph, props=aligned.merged(paragraph.props))

    return ir.map_paragraphs(blocks, apply)


def _dpi(info: object) -> tuple[Decimal, Decimal]:
    """Pillow's (x, y) resolution, falling back to 96 dpi for missing or implausible values."""
    if isinstance(info, tuple) and len(info) == 2:  # pyright: ignore[reportUnknownArgumentType]
        x, y = info  # pyright: ignore[reportUnknownVariableType]
        if isinstance(x, int | float) and isinstance(y, int | float) and x >= 10 and y >= 10:
            return _near_integer(x), _near_integer(y)
    return Decimal(DEFAULT_DPI), Decimal(DEFAULT_DPI)


def _near_integer(dpi: float) -> Decimal:
    """PNG stores resolution per metre, so 96 dpi reads back as 95.9866: snap such values."""
    return Decimal(round(dpi)) if abs(dpi - round(dpi)) < 0.05 else Decimal(str(dpi))


def _is_blank(item: ast.Inline | ast.Block) -> bool:
    return isinstance(item, ast.SoftBreak) or (isinstance(item, ast.Text) and not item.text.strip())


def _trim_trailing(pieces: list[Piece]) -> None:
    """Drop trailing spaces (looking through bookmarks, which take no room)."""
    i = len(pieces) - 1
    while i >= 0 and pieces[i].kind in ("text", "mark"):
        if pieces[i].kind == "mark":
            i -= 1
            continue
        text = pieces[i].text.rstrip(" ")
        if text:
            pieces[i] = replace(pieces[i], text=text)
            return
        del pieces[i]
        i -= 1


def _on_own_line(items: Sequence[ast.Inline | ast.Block], index: int) -> bool:
    """Whether only line breaks (or spaces) separate items[index] from its neighbours."""

    def boundary(step: int) -> bool:
        i = index + step
        while 0 <= i < len(items):
            item = items[i]
            if not isinstance(item, ast.Text):
                return isinstance(item, (ast.SoftBreak, *BLOCK_TYPES)) or (
                    isinstance(item, ast.Command) and item.name in BLOCK_COMMANDS | PLANNED_BLOCKS
                )
            if item.text.strip():
                return False
            i += step
        return True

    return boundary(-1) and boundary(1)


def _marked(name: str, content: Iterable[ir.Inline]) -> tuple[ir.Inline, ...]:
    """`content` inside the bookmark `name`."""
    return (ir.BookmarkStart(name), *content, ir.BookmarkEnd(name))


def _has_image(block: ir.Block | None) -> bool:
    return isinstance(block, ir.Paragraph) and any(isinstance(i, ir.Image) for i in block.content)


def _starts_with(item: ast.Inline | ast.Block | None, command: str) -> bool:
    """Whether `item` is (or is a paragraph starting with) the block command `command`."""
    if isinstance(item, ast.Paragraph):
        item = next((c for c in item.content if not _is_blank(c)), None)
    return isinstance(item, ast.Command) and item.name == command
