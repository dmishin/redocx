"""Lowering: the document AST -> the IR.

One pass over the AST, in document order. This module is the core of that pass: it walks
blocks and paragraphs, expands macros, dispatches commands and assembles inline text into
runs. Each command is first bound against its spec (`wordgenx.commands`), which checks
its options and body, and then handed to its family's module:

* `text`: paragraphs, headings, lists, alignment, tab stops, inline formatting, links,
  breaks, raw XML;  `code`: fenced code blocks;  `preamble`: \\document, \\meta,
  \\settings, \\use, \\include, \\styles;  `sections`: \\section, \\header, \\footer;
  `tables`;  `fields`;  `xrefs`: references, counters, notes, captions, figures,
  tables of contents;  `media`: images;  `equations`;  `citations`;  `index`.

Style and list references are checked at the end, once every style sheet has been
read, so a \\styles block may appear anywhere. Built-in styles the markup relies on
(Heading 1, List Paragraph, Strong, ...) are created only when used and not defined.
"""

from __future__ import annotations

import difflib
from collections.abc import Generator, Iterable, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from pathlib import Path

from wordgenx import ir
from wordgenx.bibliography import Bibliography
from wordgenx.commands.bind import Args, bind
from wordgenx.commands.builtin import COMMANDS, is_block
from wordgenx.commands.spec import POSTPONED, CommandSpec, Placement
from wordgenx.highlight import DEFAULT_THEME
from wordgenx.lower import (
    citations,
    code,
    comments,
    equations,
    fields,
    index,
    media,
    preamble,
    sections,
    tables,
    text,
    watermarks,
    xrefs,
)
from wordgenx.lower.builtins import BUILTIN_STYLES
from wordgenx.lower.common import (
    BLOCK_TYPES,
    BlockHandler,
    Format,
    InlineHandler,
    Located,
    Piece,
    assemble,
    is_blank,
    shown_path,
)
from wordgenx.lower.macros import Binding, Macro, Substitution, define_macro, let_macro
from wordgenx.lower.references import Label, References
from wordgenx.ooxml.style_names import style_id
from wordgenx.ooxml.template import Template
from wordgenx.props import EMPTY_RUN, PropertySet
from wordgenx.stylesheet.compiler import StyleSheetCompiler
from wordgenx.syntax import ParseResult, ast, parse
from wordgenx.syntax.diagnostics import Code, Diagnostic, Severity, expansion_notes
from wordgenx.syntax.source import Expansion, Source, Span
from wordgenx.values import Length

MAX_MACRO_DEPTH = 32
MAX_INCLUDE_DEPTH = 16
PART_OF = {"row": "\\table", "cell": "\\row"}
BLOCK_NAMES: dict[type, str] = {
    ast.Paragraph: "a paragraph",
    ast.Heading: "a heading",
    ast.ListBlock: "a list",
    ast.CodeBlock: "a code block",
    ast.DisplayMath: "display math",
    ast.PipeTable: "a table",
}
"""How messages name blocks that are not commands."""

BLOCK_HANDLERS: dict[str, BlockHandler] = {
    **text.BLOCKS,
    **preamble.BLOCKS,
    **sections.BLOCKS,
    **tables.BLOCKS,
    **xrefs.BLOCKS,
    **equations.BLOCKS,
    **citations.BLOCKS,
    **index.BLOCKS,
    **media.BLOCKS,
    **watermarks.BLOCKS,
}
INLINE_HANDLERS: dict[str, InlineHandler] = {
    **text.INLINES,
    **fields.INLINES,
    **xrefs.INLINES,
    **media.INLINES,
    **equations.INLINES,
    **citations.INLINES,
    **index.INLINES,
    **comments.INLINES,
}


@dataclass
class StyleUse:
    name: str
    kind: ir.StyleKind
    span: Span
    source: Source
    implicit: bool = False
    """Added by the compiler, not written in the document (a list level's style): it only
    brings in a built-in style; a style that is missing was reported where it is named."""


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
    comment_count: int = 0
    """Comments so far (they are named _Comment1, _Comment2, ...)."""
    container_depth: int = 0
    """> 0 while lowering a table cell, header, footer or note (no section breaks there)."""
    part: str | None = None
    """header | footer | footnote | endnote while lowering one (notes are not allowed there)."""
    bibliography: Bibliography | None = None
    """The data and style \\bibliography loaded; citations are collected in it."""
    code_theme: str = DEFAULT_THEME
    """The Pygments style that colours the Code ... token styles."""
    caption_chapters: tuple[str, str] | None = None
    """(chapter style, separator) when captions are numbered per chapter ("Figure 2.1")."""
    template: Template | None = None
    """The .dotx or .docx the document is compiled into."""
    template_fixed: bool = False
    """The template came from the command line (it wins over \\document[template=...])."""
    style_uses: list[StyleUse] = field(default_factory=lambda: [])
    list_uses: list[tuple[str, Span, Source]] = field(default_factory=lambda: [])
    lists: text.ListState = field(default_factory=lambda: text.ListState())
    links: int = 0
    macro_depth: int = 0
    include_stack: list[Path] = field(default_factory=lambda: [])
    std_loaded: set[str] = field(default_factory=lambda: set[str]())
    references: References = field(default_factory=References)
    figure: xrefs.FigureContext | None = None
    """The \\figure being lowered."""
    previous: ir.Block | None = None
    """The block lowered just before the current one (captions look at it)."""
    following: ast.Inline | ast.Block | None = None
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
        notes = expansion_notes(span)
        diagnostic = Diagnostic(severity, code, message, span, label, help, notes)
        self.diagnostics.append((self.source_of(span), diagnostic))

    def source_of(self, span: Span) -> Source:
        """The file `span` is in: the macro's for text a macro brought in."""
        return span.expansion.source if span.expansion is not None else self.source

    def warn(self, code: Code, message: str, span: Span, help: str | None = None) -> None:
        self.report(code, message, span, help=help, severity=Severity.WARNING)

    def not_yet(self, command: ast.Command, spec: CommandSpec) -> None:
        kept = " (its text is kept)" if spec.keep_body else ""
        if spec.planned == POSTPONED:
            message = f"`\\{command.name}` is postponed indefinitely; it is ignored{kept}"
            help = "see docs/09-postponed.md"
        else:
            message = f"`\\{command.name}` is not supported yet; it is ignored{kept}"
            help = f"planned for milestone {spec.planned}"
        self.warn(Code.NOT_SUPPORTED_YET, message, command.span, help=help)

    def unknown_command(self, command: ast.Command) -> None:
        close = difflib.get_close_matches(command.name, [*COMMANDS, *self.macros], n=1)
        self.report(
            Code.UNKNOWN_COMMAND,
            f"unknown command `\\{command.name}`",
            command.span,
            f"did you mean `\\{close[0]}`?" if close else "",
            help="define it with \\define, or escape the backslash as \\\\",
        )

    def bind(self, command: ast.Command, spec: CommandSpec | None = None) -> Args:
        """The command's options checked against its spec; problems are reported."""
        args, problems = bind(command, spec or COMMANDS[command.name])
        for p in problems:
            self.report(p.code, p.message, p.span, p.label, p.help, p.severity)
        return args

    def in_body(self, command: ast.Command) -> bool:
        """Whether `command` is in the document body (not in a table, header, note or
        figure); if not, that is reported."""
        if self.container_depth or self.figure is not None:
            self.report(
                Code.MISPLACED, f"`\\{command.name}` must be in the document body", command.span
            )
            return False
        return True

    @contextmanager
    def contained(self, part: str | None = None) -> Generator[None]:
        """Lowering the content of a cell, header, footer or note."""
        saved = self.part, self.previous, self.following
        self.container_depth += 1
        if part is not None:
            self.part = part
        try:
            yield
        finally:
            self.container_depth -= 1
            self.part, self.previous, self.following = saved

    # --- documents and blocks --------------------------------------------------------------

    def document(self, document: ast.Document) -> list[ir.Block]:
        return self.blocks(document.blocks)

    def blocks(
        self, blocks: Iterable[ast.Block], lists: text.ListContext | None = None
    ) -> list[ir.Block]:
        out: list[ir.Block] = []
        items = list(blocks)
        for i, block in enumerate(items):
            self.previous = out[-1] if out else None
            self.following = items[i + 1] if i + 1 < len(items) else None
            out.extend(self.block(block, lists))
        return out

    def block(self, block: ast.Block, lists: text.ListContext | None = None) -> list[ir.Block]:
        if isinstance(block, ast.Paragraph):
            return self.paragraph(block)
        self.has_content = True
        if isinstance(block, ast.Heading):
            return [text.heading(self, block)]
        if isinstance(block, ast.ListBlock):
            return text.list_block(self, block, lists)
        if isinstance(block, ast.CodeBlock):
            return code.code_block(self, block)
        if isinstance(block, ast.DisplayMath):
            return equations.display_block(self, block)
        return tables.pipe_table(self, block)

    def paragraph(self, paragraph: ast.Paragraph) -> list[ir.Block]:
        items = self.expand(paragraph.content)
        blocks = [i for i, item in enumerate(items) if self.is_block_item(item)]
        has_text = any(not is_blank(item) and not self.is_block_item(item) for item in items)
        inside = [i for i in blocks if not _on_own_line(items, i)]
        if inside and has_text:
            for i in inside:
                self.misplaced_block(items[i])
            items = [item for item in items if not self.is_block_item(item)]
            blocks = []
        if not blocks:
            return self.text_paragraph(items) if has_text else []
        # Block commands on lines of their own split the paragraph: text, block, text, ...
        segments: list[ast.Inline | ast.Block | list[ast.Inline | ast.Block]] = []
        for item in items:
            if self.is_block_item(item):
                segments.append(item)
            elif not is_blank(item) or (segments and isinstance(segments[-1], list)):
                if not segments or not isinstance(segments[-1], list):
                    segments.append([])
                segments[-1].append(item)  # type: ignore[union-attr]
        out: list[ir.Block] = []
        previous, following = self.previous, self.following
        for i, segment in enumerate(segments):
            self.previous = out[-1] if out else previous
            after = segments[i + 1] if i + 1 < len(segments) else following
            self.following = after[0] if isinstance(after, list) else after
            if isinstance(segment, list):
                out.extend(self.text_paragraph(segment))
            elif isinstance(segment, ast.Command):
                out.extend(self.block_command(segment))
            else:
                out.extend(self.block(segment))  # type: ignore[arg-type]
        return out

    def text_paragraph(self, content: list[ast.Inline | ast.Block]) -> list[ir.Block]:
        if all(is_blank(item) for item in content):
            return []
        self.has_content = True
        pieces = self.inlines(content, Format())  # type: ignore[arg-type]
        return [ir.Paragraph(self.assemble(pieces))]

    def is_block_item(self, item: ast.Inline | ast.Block) -> bool:
        if isinstance(item, BLOCK_TYPES):
            return True  # a block spliced in by a macro
        if isinstance(item, ast.Command):
            # \pagebreak and \columnbreak are inline: alone they still make their own paragraph.
            if item.name == "raw":
                return text.raw_kind(item) == "block"
            return is_block(item.name)
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

    def single_paragraph(self, command: ast.Command) -> Sequence[ast.Inline]:
        """The inline content of a body that must be one paragraph (\\p, \\caption)."""
        blocks = command.body.blocks if isinstance(command.body, ast.MarkupBody) else ()
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

    def paragraph_content(self, command: ast.Command) -> tuple[ir.Inline, ...]:
        """The body of \\p or \\caption as the content of one paragraph."""
        content = self.expand(self.single_paragraph(command))
        inlines = [c for c in content if not isinstance(c, BLOCK_TYPES)]
        return self.assemble(self.inlines(inlines, Format()))  # type: ignore[arg-type]

    # --- macros -----------------------------------------------------------------------------

    def register_define(self, node: ast.Define) -> None:
        if not self.check_macro_name(node.name, node.span):
            return
        self.macros[node.name] = define_macro(
            node, self.source_of(node.span), lambda msg, span: self.report(Code.MACRO, msg, span)
        )

    def register_let(self, node: ast.Let) -> None:
        if self.check_macro_name(node.name, node.span):
            self.macros[node.name] = let_macro(node, self.source_of(node.span))

    def check_macro_name(self, name: str, span: Span) -> bool:
        if name in COMMANDS or name in ("define", "let"):
            self.report(
                Code.MACRO, f"`\\{name}` is a built-in command and cannot be redefined", span
            )
            return False
        return True

    def call_macro(self, call: ast.Command) -> list[ast.Inline | ast.Block]:
        macro = self.macros[call.name]
        if self.macro_depth >= MAX_MACRO_DEPTH:
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
        expansion = Expansion(macro.name, macro.source, call.span, self.source_of(call.span))
        expanded = Substitution(
            bindings,
            lambda msg, span, label: self.report(Code.MACRO, msg, span, label),
            expansion,
        ).blocks(macro.body)
        self.macro_depth += 1
        try:
            return self.splice(expanded)
        finally:
            self.macro_depth -= 1

    # --- commands ---------------------------------------------------------------------------

    def block_command(self, command: ast.Command) -> list[ir.Block]:
        spec = COMMANDS[command.name]
        if spec.planned is not None:
            self.not_yet(command, spec)
            return []
        if isinstance(command.body, ast.VerbatimBody) and command.body.unclosed:
            return []  # reported by the parser; its text is the rest of the file
        return BLOCK_HANDLERS[command.name](self, command, self.bind(command, spec))

    def inline_command(self, command: ast.Command, fmt: Format) -> list[Piece]:
        name = command.name
        if name in self.macros:
            return self.spliced_inlines(self.call_macro(command), fmt, command.span)
        spec = COMMANDS.get(name)
        if spec is None:
            self.unknown_command(command)
            return []
        if spec.placement is Placement.BLOCK:
            self.report(
                Code.MISPLACED,
                f"`\\{name}` is a block command and cannot appear inside text",
                command.span,
                help="put it on its own line",
            )
            return []
        if spec.placement is Placement.PART:
            self.report(
                Code.MISPLACED,
                f"`\\{name}` is only valid directly inside {PART_OF[name]}",
                command.span,
            )
            return []
        if spec.planned is not None:
            self.not_yet(command, spec)
            if spec.keep_body and isinstance(command.body, ast.MarkupBody):
                return self.body_inlines(command, fmt)
            return []
        if isinstance(command.body, ast.VerbatimBody) and command.body.unclosed:
            return []  # reported by the parser; its text is the rest of the file
        return INLINE_HANDLERS[name](self, command, self.bind(command, spec), fmt)

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
                return xrefs.make_ref(self, node.label, "default", node.span, fmt)
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
        reported = False
        for item in items:
            if isinstance(item, BLOCK_TYPES):  # commands report their own, more specific error
                if not reported:
                    self.misplaced_block(item)
                    reported = True
                continue
            out.extend(self.inline(item, fmt))  # type: ignore[arg-type]
        return out

    def misplaced_block(self, item: ast.Inline | ast.Block) -> None:
        """Report a block inside text, naming it. A block a macro brought in is reported at
        the call written in the document."""
        if isinstance(item, ast.Command):
            what = f"`\\{item.name}`"
        else:
            what = BLOCK_NAMES.get(type(item), "a block")
        call = item.span.expansion
        while call is not None and call.call.expansion is not None:
            call = call.call.expansion
        if call is not None:
            self.report(
                Code.MISPLACED,
                f"`\\{call.macro}` brings in {what}, which cannot appear inside a paragraph",
                call.call,
                help="call it on a line of its own",
            )
        elif isinstance(item, ast.Command):
            self.report(
                Code.MISPLACED,
                f"{what} is a block command and cannot appear inside a paragraph",
                item.span,
                help="put it on its own line",
            )
        else:
            self.report(Code.MISPLACED, f"{what} cannot appear inside a paragraph", item.span)

    def body_inlines(self, command: ast.Command, fmt: Format) -> list[Piece]:
        """The body of an inline command (a missing body has been reported by binding)."""
        if isinstance(command.body, ast.VerbatimBody):
            return [Piece(command.body.text, fmt, "code")]
        if command.body is None:
            return []
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
        self.links += 1
        self.use_style("Hyperlink", ir.StyleKind.CHARACTER, span)
        return replace(fmt, link=(self.links, url), style=fmt.style or "Hyperlink")

    def style_run_props(self, name: str) -> PropertySet:
        style = self.compiled_style(name)
        return style.run if style is not None else EMPTY_RUN

    def compiled_style(self, name: str) -> ir.Style | None:
        """A style defined by the style sheets so far, or the built-in one of that name."""
        style = self.stylesheets._styles.get(style_id(name))  # pyright: ignore[reportPrivateUsage]
        if style is None and name in BUILTIN_STYLES:
            style = BUILTIN_STYLES[name]()
        return style

    def assemble(self, pieces: list[Piece]) -> tuple[ir.Inline, ...]:
        return assemble(pieces, self.smart_quotes)

    # --- shared services ---------------------------------------------------------------------

    def use_style(self, name: str, kind: ir.StyleKind, span: Span) -> None:
        self.style_uses.append(StyleUse(name, kind, span, self.source_of(span)))

    def register_label(self, name: str, span: Span, kind: str) -> Label:
        """A new label and its bookmark; a duplicate is reported (and gets a bookmark of its own,
        which nothing refers to)."""
        refs = self.references
        label = Label(name, refs.bookmark(name), kind, span, self.source_of(span))
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

    def paths_help(self) -> str:
        folder = shown_path(self.base_dir)
        return (
            "paths are relative to the current folder"
            if folder == "."
            else (f"paths are relative to {folder}")
        )

    def text_width(self) -> Length:
        """The width text takes: a column's in a section with columns (the narrowest of
        unequal ones), as Word fits pictures and tables to the column; else the page's."""
        section = self.section
        if section.column_widths:
            return min(section.column_widths, key=lambda width: width.emu)
        if section.columns > 1:
            gaps = section.column_gap.emu * (section.columns - 1)
            return Length((section.page.text_width.emu - gaps) // section.columns)
        return section.page.text_width

    def load(self, path: Path, command: ast.Command) -> ParseResult | None:
        if len(self.include_stack) > MAX_INCLUDE_DEPTH:
            self.report(
                Code.INCLUDE,
                f"files are included more than {MAX_INCLUDE_DEPTH} levels deep",
                command.span,
            )
            return None
        try:
            source = Source.from_path(path, shown_path(path))
        except (OSError, UnicodeDecodeError) as e:
            reason = e.strerror if isinstance(e, OSError) else "not valid UTF-8"
            self.report(
                Code.INCLUDE,
                f"cannot read `{path.name}`: {reason}",
                command.span,
                help=self.paths_help(),
            )
            return None
        result = parse(source)
        self.diagnostics.extend((source, d) for d in result.diagnostics)
        return result


def _on_own_line(items: Sequence[ast.Inline | ast.Block], index: int) -> bool:
    """Whether only line breaks (or spaces) separate items[index] from its neighbours."""

    def boundary(step: int) -> bool:
        i = index + step
        while 0 <= i < len(items):
            item = items[i]
            if not isinstance(item, ast.Text):
                return isinstance(item, (ast.SoftBreak, *BLOCK_TYPES)) or (
                    isinstance(item, ast.Command) and is_block(item.name)
                )
            if item.text.strip():
                return False
            i += step
        return True

    return boundary(-1) and boundary(1)
