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
import re
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import get_args

from wordgenx import ir
from wordgenx.lower.builtins import BUILTIN_STYLES
from wordgenx.lower.macros import Binding, Macro, Substitution, define_macro, let_macro
from wordgenx.lower.typography import smarten
from wordgenx.ooxml.style_names import style_id
from wordgenx.props import (
    EMPTY_PARAGRAPH,
    EMPTY_RUN,
    REGISTRY,
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
from wordgenx.values import InvalidValueError, Length

Located = tuple[Source, Diagnostic]

BLOCK_COMMANDS = frozenset("p align list include use styles document meta settings section".split())
INLINE_COMMANDS = frozenset(
    "b i u s sup sub hl font style lang hidden strong emph code link url br tab".split()
)
BREAK_COMMANDS = frozenset({"pagebreak", "columnbreak"})
"""Allowed both inline and as a block of their own."""

PLANNED: dict[str, str] = {
    **dict.fromkeys(["table", "row", "cell", "image"], "M1 (tables and images)"),
    **dict.fromkeys(
        [
            "figure",
            "caption",
            "toc",
            "listof",
            "footnote",
            "endnote",
            "ref",
            "pageref",
            "field",
            "pagenum",
            "numpages",
            "sectionpages",
            "date",
            "header",
            "footer",
            "tabs",
            "seq",
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

PLANNED_BLOCKS = frozenset(
    "table figure caption toc listof header footer watermark bibliography printbibliography "
    "index".split()
)
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
    | column."""


@dataclass
class ListContext:
    numbering: str
    instance: int
    level: int
    ordered: bool
    named: bool
    start: int | None


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
    stylesheets: StyleSheetCompiler = field(default_factory=StyleSheetCompiler)
    macros: dict[str, Macro] = field(default_factory=lambda: {})
    meta: ir.Meta = field(default_factory=ir.Meta)
    settings: ir.Settings = field(default_factory=ir.Settings)
    page: ir.PageSetup = field(default_factory=ir.PageSetup)
    smart_quotes: bool = True
    has_content: bool = False
    style_uses: list[StyleUse] = field(default_factory=lambda: [])
    list_uses: list[tuple[str, Span, Source]] = field(default_factory=lambda: [])
    _instances: int = 0
    _last_instance: dict[str, tuple[int, int | None]] = field(default_factory=lambda: {})
    _links: int = 0
    _macro_depth: int = 0
    _include_stack: list[Path] = field(default_factory=lambda: [])

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

    def document(self, document: ast.Document) -> list[ir.Paragraph]:
        return self.blocks(document.blocks)

    def blocks(
        self, blocks: Iterable[ast.Block], lists: ListContext | None = None
    ) -> list[ir.Paragraph]:
        out: list[ir.Paragraph] = []
        for block in blocks:
            out.extend(self.block(block, lists))
        return out

    def block(self, block: ast.Block, lists: ListContext | None = None) -> list[ir.Paragraph]:
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
        self.warn(
            Code.NOT_SUPPORTED_YET,
            "tables are not supported yet; rows are written as tab-separated lines",
            block.span,
            help="planned for milestone M1 (tables)",
        )
        paragraphs: list[ir.Paragraph] = []
        for row in block.rows:
            pieces: list[Piece] = []
            for i, cell in enumerate(row.cells):
                if i:
                    pieces.append(Piece("\t", Format(), "tab"))
                pieces.extend(self.inlines(cell.content, Format()))
            paragraphs.append(ir.Paragraph(self.assemble(pieces)))
        return paragraphs

    def heading(self, heading: ast.Heading) -> ir.Paragraph:
        name = f"Heading {heading.level}"
        self.use_style(name, ir.StyleKind.PARAGRAPH, heading.span)
        content = self.assemble(self.inlines(heading.content, Format()))
        return ir.Paragraph(content, name, unnumbered=not heading.numbered)

    def code_block(self, block: ast.CodeBlock) -> list[ir.Paragraph]:
        if block.options is not None:
            for option in block.options.items:
                if option.key == "caption":
                    self.warn(
                        Code.NOT_SUPPORTED_YET,
                        "code captions are not supported yet",
                        option.span,
                        help="planned for milestone M2",
                    )
        return [self.code_paragraph(line) for line in block.code.split("\n")]

    def code_paragraph(self, line: str) -> ir.Paragraph:
        self.use_style("Code Block", ir.StyleKind.PARAGRAPH, Span(0, 0))
        return ir.Paragraph((ir.Run(line),) if line else (), "Code Block")

    # --- paragraphs: inline content or block commands ------------------------------------------

    def paragraph(self, paragraph: ast.Paragraph) -> list[ir.Paragraph]:
        items = self.expand(paragraph.content)
        blocks = [item for item in items if self.is_block_item(item)]
        content = [item for item in items if not self.is_block_item(item)]
        has_text = any(not _is_blank(item) for item in content)
        if blocks and has_text:
            for item in blocks:
                self.report(
                    Code.MISPLACED,
                    "a block cannot appear inside a paragraph",
                    item.span,
                    help="put it on its own line, separated from the text by a blank line",
                )
            blocks = []
        if blocks:
            out: list[ir.Paragraph] = []
            for item in items:
                if isinstance(item, ast.Command):
                    out.extend(self.block_command(item))
                elif not isinstance(item, ast.Text | ast.SoftBreak):
                    out.extend(self.block(item))  # type: ignore[arg-type]
            return out
        if not has_text:
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

    def block_command(self, command: ast.Command) -> list[ir.Paragraph]:
        name = command.name
        handler: Callable[[ast.Command], list[ir.Paragraph]] | None = {
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

    def cmd_p(self, command: ast.Command) -> list[ir.Paragraph]:
        self.has_content = True
        extra, props = self.option_props(command, Target.PARAGRAPH, extra=("style",))
        style = None
        if (value := extra.get("style")) is not None:
            style = self.text_value(value, command, "style")
            if style is not None:
                self.use_style(style, ir.StyleKind.PARAGRAPH, value.span)
        content = self.expand(self.single_paragraph(command))
        pieces = self.inlines([c for c in content if not isinstance(c, BLOCK_TYPES)], Format())  # type: ignore[misc]
        return [ir.Paragraph(self.assemble(pieces), style, props)]

    def cmd_align(self, command: ast.Command) -> list[ir.Paragraph]:
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
        return [replace(p, props=p.props.merged(aligned)) for p in paragraphs]

    def cmd_list(self, command: ast.Command) -> list[ir.Paragraph]:
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
        out: list[ir.Paragraph] = []
        for block in self.markup_blocks(command):
            if isinstance(block, ast.ListBlock):
                self.has_content = True
                out.extend(self.list_block(block, context))
            else:
                out.extend(self.block(block))
        return out

    def cmd_include(self, command: ast.Command) -> list[ir.Paragraph]:
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

    def cmd_use(self, command: ast.Command) -> list[ir.Paragraph]:
        target = command.body.text.strip() if isinstance(command.body, ast.VerbatimBody) else ""
        if target.startswith("std:"):
            self.warn(
                Code.NOT_SUPPORTED_YET,
                f"the standard library (`{target}`) is not available yet",
                command.span,
                help="planned with the macro library in M1",
            )
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
        # A macro file: register its definitions, ignore anything else.
        result = self.load(path, command)
        if result is None:
            return []
        source, base_dir = self.source, self.base_dir
        self.source, self.base_dir = result.source, path.resolve().parent
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
            self.source, self.base_dir = source, base_dir
        return []

    def cmd_styles(self, command: ast.Command) -> list[ir.Paragraph]:
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

    def cmd_document(self, command: ast.Command) -> list[ir.Paragraph]:
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

    def cmd_meta(self, command: ast.Command) -> list[ir.Paragraph]:
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

    def cmd_settings(self, command: ast.Command) -> list[ir.Paragraph]:
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

    def cmd_section(self, command: ast.Command) -> list[ir.Paragraph]:
        if self.has_content:
            self.warn(
                Code.NOT_SUPPORTED_YET,
                "multiple sections are not supported yet; this \\section is ignored",
                command.span,
                help="planned for milestone M2",
            )
            return []
        page = self.page
        orientation: str | None = None
        for option in command.options.items if command.options else ():
            key, value = option.key, option.value
            if key == "size":
                if isinstance(value, ast.TupleValue) and len(value.items) == 2:
                    w = self.length(
                        self.text_value(value.items[0].value, command, "size") or "", value.span
                    )
                    h = self.length(
                        self.text_value(value.items[1].value, command, "size") or "", value.span
                    )
                    if w and h:
                        page = replace(page, width=w, height=h)
                elif (name := self.text_value(value, command, "size")) in PAGE_SIZES:
                    w, h = PAGE_SIZES[name]  # type: ignore[index]
                    page = replace(page, width=w, height=h)
                else:
                    self.report(
                        Code.INVALID_ARGUMENT,
                        "unknown page size",
                        value.span,
                        help=f"{', '.join(PAGE_SIZES)} or (width, height)",
                    )
            elif key == "orientation":
                orientation = self.text_value(value, command, "orientation")
                if orientation not in ("portrait", "landscape"):
                    self.report(
                        Code.INVALID_ARGUMENT, "orientation is portrait or landscape", value.span
                    )
                    orientation = None
            elif key == "margins":
                page = self.margins(page, value, command)
            elif key == "gutter":
                if (
                    gutter := self.length(self.text_value(value, command, key) or "", value.span)
                ) is not None:
                    page = replace(page, gutter=gutter)
            else:
                self.warn(
                    Code.NOT_SUPPORTED_YET,
                    f"section option `{key or self.text_value(value, command, '')}` "
                    "is not supported yet",
                    option.span,
                    help="planned for milestone M2",
                )
        if orientation is not None and (orientation == "landscape") != page.landscape:
            page = replace(page, width=page.height, height=page.width)
        self.page = page
        return []

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

    # --- lists ---------------------------------------------------------------------------------

    def new_instance(self, numbering: str, start: int | None) -> int:
        self._instances += 1
        self._last_instance[numbering] = (self._instances, start)
        return self._instances

    def list_block(self, block: ast.ListBlock, parent: ListContext | None) -> list[ir.Paragraph]:
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
        out: list[ir.Paragraph] = []
        for item in block.items:
            out.extend(self.list_item(item, context))
        return out

    def list_item(self, item: ast.ListItem, context: ListContext) -> list[ir.Paragraph]:
        ref = ir.ListItemRef(context.numbering, context.instance, context.level, context.start)
        out: list[ir.Paragraph] = []
        numbered = False
        for block in item.blocks:
            if isinstance(block, ast.ListBlock):
                if not numbered:
                    out.append(ir.Paragraph(list_item=ref))
                    numbered = True
                out.extend(self.list_block(block, context))
                continue
            paragraphs = self.block(block, context)
            for paragraph in paragraphs:
                if not numbered:
                    out.append(replace(paragraph, list_item=ref))
                    numbered = True
                elif isinstance(block, ast.Paragraph):
                    out.append(replace(paragraph, list_item=replace(ref, continuation=True)))
                else:
                    out.append(paragraph)
        if not numbered:
            out.append(ir.Paragraph(list_item=ref))
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
                self.warn(
                    Code.NOT_SUPPORTED_YET,
                    "cross-references are not supported yet; the label is shown",
                    node.span,
                    help="planned for milestone M2",
                )
                return [Piece(node.label, fmt, "code")]
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
        if name in PLANNED:
            self.not_yet(command)
            if name in TRANSPARENT and isinstance(command.body, ast.MarkupBody):
                return self.body_inlines(command, fmt)
            return []
        if name not in INLINE_COMMANDS:
            self.unknown_command(command)
            return []
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
        compiled = {style_id(s.name): s for s in self.stylesheets._styles.values()}  # pyright: ignore[reportPrivateUsage]
        style = compiled.get(style_id(name))
        if style is None and name in BUILTIN_STYLES:
            style = BUILTIN_STYLES[name]()
        return style.run if style is not None else EMPTY_RUN

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
        if isinstance(value, ast.BareValue | ast.StringValue):
            return value.text
        self.report(
            Code.INVALID_ARGUMENT,
            f'`{what}` of `\\{command.name}` must be a word or a "string"',
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


def _is_blank(item: ast.Inline | ast.Block) -> bool:
    return isinstance(item, ast.SoftBreak) or (isinstance(item, ast.Text) and not item.text.strip())


def _trim_trailing(pieces: list[Piece]) -> None:
    while pieces and pieces[-1].kind == "text":
        text = pieces[-1].text.rstrip(" ")
        if text:
            pieces[-1] = replace(pieces[-1], text=text)
            return
        pieces.pop()
