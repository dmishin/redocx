"""IR -> redocx source with commands only (level L1 of the decompiler note).

Every paragraph, run, table, section and note becomes the command that compiles back to
it: `\\p[style=…]`, direct formatting as `\\font[…]`, `\\table`/`\\row`/`\\cell`,
`\\section`, `\\header`/`\\footer`, `\\footnote`, `\\image` (the pictures are written to
files), fields as their commands or `\\field`. A style sheet declares the styles and list
definitions the document uses. What cannot be printed is counted in `lost`; nothing is
dropped silently.
"""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime
from decimal import Decimal

from redocx import ir
from redocx.commands.builtin import NOTE_FORMATS, NOTE_POSITIONS, NOTE_RESTARTS, PAGE_SIZES
from redocx.escape import inline, line_start
from redocx.importer.labels import HIDDEN, Caption, Labels, analyse
from redocx.importer.values import (
    border,
    color,
    declarations,
    length,
    number,
    options,
    quote,
)
from redocx.lower.builtins import BUILTIN_STYLES
from redocx.math.latex import raw_equation, to_latex
from redocx.math.nodes import Raw as MathRaw
from redocx.math.nodes import walk
from redocx.ooxml.parts.theme import Theme
from redocx.ooxml.style_names import is_builtin, stored_name
from redocx.props import REGISTRY, PropertySet, Target
from redocx.stylesheet.compiler import BANDS, CONDITION_TARGETS, RUN_LAST, TABLE_PARTS
from redocx.values import Length

INDENT = "  "
_SIMPLE_FIELDS = {"PAGE": "\\pagenum", "NUMPAGES": "\\numpages", "SECTIONPAGES": "\\sectionpages"}
_MERGEFORMAT_RE = re.compile(r"\s*\\\*\s*MERGEFORMAT\s*", re.IGNORECASE)
_TOC_LEVELS_RE = re.compile(r'\\o\s+"(\d)-(\d)"')
_TOC_CAPTION_RE = re.compile(r'\\c\s+"([^"]+)"')
_HEADING_RE = re.compile(r"heading ([1-9])", re.IGNORECASE)
_DEFAULT_LOOK = ir.DEFAULT_TABLE_LOOK
_DISPLAY = {name.lower(): name for name in BUILTIN_STYLES}
_PART_NAMES = {kind: name for name, kind in reversed(TABLE_PARTS.items())}
_BAND_NAMES = {kind: (name, i) for name, kinds in BANDS.items() for i, kind in enumerate(kinds)}
_CUSTOM_NAME_RE = re.compile(r"[A-Za-z][A-Za-z0-9_-]*")
_HIDDEN_BOOKMARKS = HIDDEN
_RAW_BOOKMARK_IDS = 100_000
"""Bookmarks kept as raw XML count from here, clear of the ids redocx writes."""


def display_name(name: str) -> str:
    """The name a person writes: ``Heading 1`` for Word's stored ``heading 1``."""
    lower = name.lower()
    if lower in _DISPLAY:
        return _DISPLAY[lower]
    if is_builtin(name) and stored_name(name) == lower:  # one Word stores in lower case
        return " ".join("TOC" if w == "toc" else w.capitalize() for w in lower.split())
    return name


@dataclass
class Printed:
    """The source of a document, its style sheet, the files it refers to and what was lost."""

    source: str
    stylesheet: str
    files: dict[str, bytes] = field(default_factory=lambda: {})
    lost: Counter[str] = field(default_factory=lambda: Counter[str]())


@dataclass(frozen=True)
class _Tok:
    text: str
    bare: bool = False
    """A command without options or body: needs `{}` before a letter, digit, `-` or `[`."""
    sugar: str | None = None
    """A shorter form (`*strong*`, `@label`) used where the text around it allows."""
    kind: str = ""
    """emphasis | ref: what the text around the sugar must allow."""


_LABEL_CHAR_RE = re.compile(r"[A-Za-z0-9_:-]")


def _sugar_fits(kind: str, before: str, after: str) -> bool:
    """`*x*` opens at a word start and closes at a word end; `@label` follows a space or
    an opening bracket, and is not followed by more label characters."""
    if kind == "emphasis":
        return not before[-1:].isalnum() and not after[:1].isalnum() and after[:1] not in "*_"
    return (not before or before[-1] in " \t([{\"'") and not _LABEL_CHAR_RE.match(after[:1])


def _join(tokens: Sequence[_Tok]) -> str:
    texts = [tok.text for tok in tokens]
    for i, tok in enumerate(tokens):  # sugar where it reads the same way
        if tok.sugar is not None:
            before = "".join(texts[:i])
            after = "".join(texts[i + 1 : i + 2])
            if _sugar_fits(tok.kind, before, after):
                texts[i] = tok.sugar
    out: list[str] = []
    for i, tok in enumerate(tokens):
        out.append(texts[i])
        if tok.bare and i + 1 < len(tokens):
            nxt = texts[i + 1][:1]
            if nxt.isalnum() or nxt in "-[":
                out.append("{}")
    return "".join(out)


def _indent(text: str, prefix: str) -> str:
    return "\n".join(prefix + line if line else line for line in text.split("\n"))


def _opts(items: Sequence[str]) -> str:
    return "[" + ", ".join(items) + "]" if items else ""


def _date(moment: datetime) -> str:
    if moment.hour == moment.minute == 0:
        return moment.strftime("%Y-%m-%d")
    return moment.strftime("%Y-%m-%dT%H:%M")


class Printer:
    def __init__(
        self,
        document: ir.Document,
        stylesheet_name: str,
        template: KeptStyles | None = None,
        sugar: bool = True,
    ) -> None:
        self.doc = document
        self.sheet_name = stylesheet_name
        self.template = template
        self.sugar = sugar
        self.labels = analyse(document) if sugar else Labels()
        self.table_depth = 0
        self.part_style: str | None = None
        """While printing a header or footer: its style, which the lowering gives every
        paragraph there without one (in tables and text boxes too)."""
        self.run_base: PropertySet | None = None
        """While printing a paragraph's text: the run formatting its style gives (direct
        formatting that repeats it is left out). None: everything is printed."""
        self.files: dict[str, bytes] = {}
        self.media: dict[str, str] = {}
        """Content hash -> file name."""
        self.lost: Counter[str] = Counter()
        self.styles_used: set[str] = set()
        self.numbering_used: set[str] = set()
        self.bookmarks: dict[str, int] = {}
        self.in_parentheses: set[int] = set()
        """id() of REF fields to equations printed as `@eq:x` (the parentheses taken off)."""
        self.lists_seen: dict[str, list[tuple[int, int | None]]] = {}
        self.styles = {s.name.lower(): s for s in document.styles}
        self.numbering = {n.name: n for n in document.numbering}

    # --- the document ------------------------------------------------------------------

    def print(self) -> Printed:
        doc = self.doc
        sections = doc.sections()
        body_chunks: list[str] = []
        index = 0
        for chunk_blocks, section_break in self._split(doc.body):
            body_chunks.extend(self.blocks(chunk_blocks))
            if section_break:
                index += 1
                body_chunks.append(self.section(sections[index], sections[index - 1]))
        first = self.section(sections[0], None)
        sheet = self.stylesheet()  # after the content: it holds what the content uses
        preamble = [*self.preamble(uses_sheet=bool(sheet)), first]
        parts = [p for p in preamble if p] + body_chunks
        source = "\n\n".join(parts) + "\n"
        return Printed(source, sheet, self.files, self.lost)

    @staticmethod
    def _split(blocks: tuple[ir.Block, ...]) -> list[tuple[tuple[ir.Block, ...], bool]]:
        out: list[tuple[tuple[ir.Block, ...], bool]] = []
        current: list[ir.Block] = []
        for block in blocks:
            if isinstance(block, ir.SectionBreak):
                out.append((tuple(current), True))
                current = []
            else:
                current.append(block)
        out.append((tuple(current), False))
        return out

    def preamble(self, uses_sheet: bool) -> list[str]:
        doc, meta, settings = self.doc, self.doc.meta, self.doc.settings
        document = ["smart-quotes=off"]
        if self.template is not None:
            document.insert(0, f"template={_string(self.template.file)}")
        if meta.language:
            document.append(f"lang={quote(meta.language)}")
        if settings.page_color is not None:
            document.append(f"page-color={color(settings.page_color)}")
        out = [f"\\document{_opts(document)}"]

        fields: list[str] = []
        for key in ("title", "subject", "author", "description", "last_modified_by"):
            if (value := getattr(meta, key)) is not None:
                fields.append(f"{key.replace('_', '-')}={quote(value)}")
        if meta.keywords:
            fields.append("keywords=(" + ", ".join(quote(k) for k in meta.keywords) + ")")
        for name, value in meta.custom:
            key = name if _CUSTOM_NAME_RE.fullmatch(name) else _string(name)
            # Text is always quoted: a bare 3 or true would be a number or yes/no property.
            text = _string(value) if isinstance(value, str) else str(value).lower()
            fields.append(f"custom.{key}={text}")
        if fields:
            out.append("\\meta[\n" + "".join(f"  {f},\n" for f in fields) + "]")

        opts: list[str] = []
        if settings.even_and_odd_headers:
            opts.append("even-odd-headers")
        if settings.mirror_margins:
            opts.append("mirror-margins")
        if settings.update_fields_on_open:
            opts.append("update-fields-on-open")
        if settings.default_tab_stop != ir.Settings().default_tab_stop:
            opts.append(f"default-tab-stop={length(settings.default_tab_stop, 'cm')}")
        for kind in ir.NOTE_KINDS:
            notes = settings.notes(kind)
            parts: list[str] = []
            if notes.format is not None:
                parts.append("format=" + _key(NOTE_FORMATS, notes.format))
            if notes.restart is not None:
                parts.append("restart=" + _key(NOTE_RESTARTS, notes.restart))
            if notes.position is not None:
                parts.append("position=" + _key(NOTE_POSITIONS[kind], notes.position))
            if notes.start is not None:
                parts.append(f"start={notes.start}")
            if parts:
                opts.append(f"{kind}s=({', '.join(parts)})")
        if settings.protection is not None:
            mode = _key(ir.PROTECTION_MODES, settings.protection)
            opts.append(
                f"protection=(mode={mode}" + (", enforce)" if settings.protection_enforced else ")")
            )
        if settings.track_changes:
            opts.append("track-changes")
        if settings.auto_hyphenation:
            opts.append("hyphenation=auto")
        if (separator := _chapter_separator(doc)) is not None:
            opts.append(
                "captions=(chapter-numbers"
                + (f", separator={quote(separator)})" if separator != "." else ")")
            )
        if opts:
            out.append(f"\\settings{_opts(opts)}")
        if uses_sheet:
            out.append(f"\\use{{{self.sheet_name}}}")
        del doc
        return out

    # --- sections ------------------------------------------------------------------------

    def section(self, section: ir.Section, previous: ir.Section | None) -> str:
        if previous is None:
            base = ir.Section()
        else:  # what a new section inherits (lower.sections)
            base = replace(previous, page_number_start=None, start="next-page", title_page=False)
        opts: list[str] = []
        page, old = section.page, base.page
        if not _same(page.width, old.width) or not _same(page.height, old.height):
            opts.extend(_page_size(page))
        margins = [
            f"{side}={length(getattr(page, f'margin_{side}'), 'cm')}"
            for side in ("top", "bottom", "left", "right")
            if not _same(getattr(page, f"margin_{side}"), getattr(old, f"margin_{side}"))
        ]
        if margins:
            opts.append(f"margins=({', '.join(margins)})")
        for key in ("gutter", "header_distance", "footer_distance"):
            if not _same(getattr(page, key), getattr(old, key)):
                opts.append(f"{key.replace('_', '-')}={length(getattr(page, key), 'cm')}")
        if previous is not None and section.start != "next-page":
            opts.append(f"break={section.start}")
        numbers: list[str] = []
        if section.page_number_format != base.page_number_format:
            numbers.append(f"format={section.page_number_format or 'decimal'}")
        if section.page_number_start is not None:
            numbers.append(f"start={section.page_number_start}")
        if numbers:
            opts.append(f"page-numbers=({', '.join(numbers)})")
        columns = (section.columns, section.column_gap.in_twips, section.column_separator)
        old_columns = (base.columns, base.column_gap.in_twips, base.column_separator)
        widths = [w.in_twips for w in section.column_widths]
        if columns != old_columns or widths != [w.in_twips for w in base.column_widths]:
            parts = [f"count={section.columns}", f"gap={length(section.column_gap, 'cm')}"]
            if section.column_separator:
                parts.append("separator")
            elif base.column_separator:
                self.lost["column separator turned off"] += 1
            if section.column_widths:
                widths = ", ".join(length(w, "size") for w in section.column_widths)
                parts.append(f"widths=({widths})")
            opts.append(f"columns=({', '.join(parts)})")
        if section.vertical_align != base.vertical_align:
            opts.append(f"valign={section.vertical_align or 'top'}")
        if section.borders != base.borders:
            opts.append(f"borders={self._page_borders(section.borders)}")
        if section.rtl != base.rtl:
            opts.append("rtl" if section.rtl else "rtl=off")
        if section.text_direction != base.text_direction:
            opts.append(f"text-direction={section.text_direction}")
        if section.line_numbers != base.line_numbers:
            opts.append(f"line-numbers={_line_numbers(section.line_numbers)}")
        if section.title_page:
            opts.append("title-page")
        if section.watermark is not None:
            self.lost["watermark"] += 1
        out: list[str] = []
        if opts or previous is not None:
            out.append(f"\\section{_opts(opts)}")
        for family, parts_of in (("header", section.headers), ("footer", section.footers)):
            for kind, blocks in parts_of:
                if kind == "first" and not section.title_page:
                    self.lost[
                        f"first-page {family} of a section without a different first page"
                    ] += 1
                    continue
                if kind == "even" and not self.doc.settings.even_and_odd_headers:
                    self.lost[f"even-page {family} without odd and even headers"] += 1
                    continue
                arg = "" if kind == "default" else f"[{kind}]"
                self.part_style = family  # in its tables too: the lowering styles them all
                try:
                    body = self.body(blocks, default_style=family)
                finally:
                    self.part_style = None
                out.append(f"\\{family}{arg}{{{body}}}")
        return "\n".join(out)

    @staticmethod
    def _page_borders(borders: ir.PageBorders | None) -> str:
        if borders is None:
            return "none"
        parts = [f"{name}={border(b)}" for name, b in borders.sides()]
        if borders.offset_from != "page":
            parts.append(f"from={borders.offset_from}")
        if borders.pages != "all":
            parts.append(f"pages={borders.pages}")
        return "(" + ", ".join(parts) + ")"

    # --- blocks ----------------------------------------------------------------------------

    def body(self, blocks: tuple[ir.Block, ...], default_style: str | None = None) -> str:
        """Blocks as the body of a command: one plain paragraph inline, else on lines."""
        if len(blocks) == 1 and isinstance(blocks[0], ir.Paragraph):
            only = blocks[0]
            if self._is_plain(only, default_style):
                return line_start(self.inline(only.content))
        if not blocks:
            return ""
        printed = "\n\n".join(self.blocks(blocks, default_style))
        return "\n" + _indent(printed, INDENT) + "\n"

    def _is_plain(self, paragraph: ir.Paragraph, default_style: str | None) -> bool:
        return (
            self._style(paragraph.style, default_style) is None
            and not paragraph.props
            and paragraph.list_item is None
            and paragraph.drop_cap is None
            and bool(paragraph.content)
            and not all(isinstance(i, ir.Break) for i in paragraph.content)
        )

    def _style(self, name: str | None, default: str | None) -> str | None:
        """The style to write (None: the default the context gives), and note it as used.
        Where the context gives a style (headers, notes, list items), no style is Normal."""
        if name is None:
            return None if default is None else "Normal"
        self.styles_used.add(name.lower())
        if default is not None and name.lower() == default.lower():
            return None
        return display_name(name)

    def blocks(self, blocks: tuple[ir.Block, ...], default_style: str | None = None) -> list[str]:
        out: list[str] = []
        i = 0
        while i < len(blocks):
            block = blocks[i]
            if isinstance(block, ir.Paragraph) and block.list_item is not None:
                j = i
                while j < len(blocks):
                    nxt = blocks[j]
                    if not isinstance(nxt, ir.Paragraph) or nxt.list_item is None:
                        break
                    if nxt.list_item.numbering != block.list_item.numbering:
                        break
                    if (nxt.list_item.instance, nxt.list_item.start) != (
                        block.list_item.instance,
                        block.list_item.start,
                    ):
                        break
                    j += 1
                out.append(self.list(blocks[i:j]))  # type: ignore[arg-type]
                i = j
                continue
            if isinstance(block, ir.Paragraph) and block.drop_cap is not None:
                following = blocks[i + 1] if i + 1 < len(blocks) else None
                if isinstance(following, ir.Paragraph) and following.drop_cap is None:
                    out.append(self.paragraph(following, default_style, drop=block))
                    i += 2
                    continue
                self.lost["drop cap without a paragraph after it"] += 1
            out.append(self.block(block, default_style))
            i += 1
        return out

    def block(self, block: ir.Block, default_style: str | None = None) -> str:
        if isinstance(block, ir.Paragraph):
            return self.paragraph(block, default_style)
        if isinstance(block, ir.Table):
            return self.table(block)
        if isinstance(block, ir.FieldBlock):
            return self.field_block(block)
        if isinstance(block, ir.RawBlock):
            return f"\\raw[ooxml]{{{block.xml}}}"
        if isinstance(block, ir.EmbeddedDocx):
            name = self._file(block.data, "embedded", "docx", block.name)
            return f"\\embed-docx{{{name}}}"
        self.lost["section break inside a table or note"] += 1
        return ""

    def paragraph(
        self, p: ir.Paragraph, default_style: str | None = None, drop: ir.Paragraph | None = None
    ) -> str:
        style = self._style(p.style, default_style)
        if (caption := self.labels.captions.get(id(p))) is not None:
            return self.caption(caption)
        content = p.content
        if len(content) == 1 and isinstance(content[0], ir.Math) and content[0].display:
            if style is not None or p.props:
                self.lost["formatting of a display equation's paragraph"] += 1
            return self.math(content[0])
        opts: list[str] = []
        if drop is not None:
            letters = tuple(ir.Run(r.text) for r in drop.runs())
            content = (*letters, *content)
            assert drop.drop_cap is not None
            lines = drop.drop_cap.lines
            opts.append(
                f"drop-cap=(lines={lines}, in-margin)" if drop.drop_cap.in_margin
                else f"drop-cap={lines}"
            )  # fmt: skip
        text = self.inline(content, self._run_base(p.style or default_style))
        heading = _HEADING_RE.fullmatch(style) if style is not None else None
        label = self.labels.headings.get(id(p))
        tail = f" {{#{label}}}" if label else ""
        if heading is not None and text.strip() and not opts:
            if self.sugar and not p.props:
                marker = "=" * int(heading.group(1)) + ("*" if p.unnumbered else "")
                return f"{marker} {text}{tail}"
            heading_opts = [heading.group(1)]
            if p.unnumbered:
                heading_opts.append("unnumbered")
            if label is not None:
                heading_opts.append(f"id={label}")
            heading_opts.extend(options(Target.PARAGRAPH, p.props))
            return f"\\heading{_opts(heading_opts)}{{{line_start(text)}}}"
        if heading is not None and p.unnumbered:
            if p.props or opts:
                self.lost["formatting of an unnumbered heading"] += 1
            return "=" * int(heading.group(1)) + "* " + text + tail
        if p.unnumbered:
            self.lost["numbering turned off on a paragraph"] += 1
        if self.sugar and heading and not p.props and not opts and text.strip():
            return "=" * int(heading.group(1)) + " " + text + tail
        if style is not None:
            opts.insert(0, f"style={quote(style)}")
        if label is not None:
            opts.append(f"id={label}")
        opts.extend(options(Target.PARAGRAPH, p.props))
        if content and all(isinstance(i, ir.Break) for i in content) and not opts:
            return text
        if not opts and text.strip():
            return line_start(text)
        return f"\\p{_opts(opts)}{{{line_start(text)}}}"

    def math(self, math: ir.Math) -> str:
        r"""An equation as \m{...} (inline) or $$ ... $$ (display); objects LaTeX cannot say
        as \ooxml{...}, and the whole equation as \raw when even that does not print it."""
        text = to_latex(math.expr)
        if text is None:
            self.lost["equation (kept as OMML)"] += 1
            if math.numbered:
                self.lost["equation number"] += 1
            return f"\\raw[ooxml]{{{raw_equation(math.expr, display=math.display)}}}"
        kept = text.count("\\ooxml{") - sum(isinstance(n, MathRaw) for n in walk(math.expr))
        if kept:
            self.lost["math object with no LaTeX form (kept as OMML)"] += kept
        if not math.display:
            return f"\\m{{{text}}}"
        label = self.labels.equations.get(math.bookmark or "") if math.numbered else None
        bookmark = math.bookmark or "_"
        if math.numbered and label is None and not bookmark.startswith("_"):
            self.lost["bookmark around an equation number"] += 1
        if "$$" not in text and (label is not None or not math.numbered):
            return f"$$ {text} $$" + (f" {{#{label}}}" if label else "")
        opts = f"[id={label}]" if label else "[numbered]" if math.numbered else ""
        return f"\\math{opts}{{{text}}}"

    def list(self, items: Sequence[ir.Paragraph]) -> str:
        first = items[0].list_item
        assert first is not None
        name = first.numbering
        self.numbering_used.add(name)
        definition = self.numbering.get(name)
        seen = self.lists_seen.setdefault(name, [])
        key = (first.instance, first.start)
        opts = [quote(name)]
        if key in seen:
            if seen[-1] != key:
                self.lost["list continuing an earlier list"] += 1
            opts.append("continue")
        else:
            seen.append(key)
            if first.start is not None:
                opts.append(f"restart={first.start}")
        lines: list[str] = []
        depth = 0
        for item in items:
            ref = item.list_item
            assert ref is not None
            level = definition.level(ref.level) if definition else None
            new_depth = min(ref.level, depth + 1)
            if new_depth != ref.level:
                self.lost["list level skipped"] += 1
            depth = new_depth
            marker = "-" if level is not None and level.format == "bullet" else "1."
            linked = level.style if level is not None and level.style else "List Paragraph"
            plain = replace(item, list_item=None)
            if self._is_plain(plain, linked):
                text = line_start(self.inline(item.content))
            else:
                text = self.paragraph(plain, linked)
            prefix = "   " * (depth - 1)
            column = " " * (len(prefix) + len(marker) + 1)
            text = text.replace("\n", "\n" + column)
            lines.append(f"{prefix}{marker} {text}")
        return f"\\list{_opts(opts)}{{\n" + _indent("\n".join(lines), INDENT) + "\n}"

    def field_block(self, block: ir.FieldBlock) -> str:
        code = block.field.code.strip()
        if code.upper().startswith("TOC"):
            if (caption := _TOC_CAPTION_RE.search(code)) is not None:
                return f"\\listof[{quote(caption.group(1))}]"
            if (levels := _TOC_LEVELS_RE.search(code)) is not None:
                return f"\\toc[levels={levels.group(1)}-{levels.group(2)}]"
        # Any other: the field alone, for Word to fill in when it updates fields.
        self.lost[f"{code.split()[0] if code else 'field'} field result (Word fills it in)"] += 1
        return f"\\p{{\\field{{{code}}}}}"

    # --- tables ------------------------------------------------------------------------------

    def table(self, table: ir.Table) -> str:
        opts: list[str] = []
        if table.style is None:
            opts.append("style=none")
        elif table.style != "Table Grid":
            opts.append(f"style={quote(self._style(table.style, None) or table.style)}")
        if table.style is not None:
            self.styles_used.add(table.style.lower())
        opts.append("widths=(" + ", ".join(length(w, "size") for w in table.widths) + ")")
        if table.align is not None:
            opts.append(f"align={table.align}")
        if table.look != _DEFAULT_LOOK:
            looks = [flag for flag in ir.TABLE_LOOK if flag in table.look]
            opts.append(f"look=({', '.join(looks)})" if looks else "look=none")
        opts.extend(options(Target.TABLE, table.props))
        if table.anchor is not None:
            opts.extend(self._anchor(table.anchor, table=True))
        self.table_depth += 1
        try:
            rows = self._rows(table, opts)
        finally:
            self.table_depth -= 1
        return f"\\table{_opts(opts)}{{\n" + _indent("\n".join(rows), INDENT) + "\n}"

    def _rows(self, table: ir.Table, opts: list[str]) -> list[str]:
        """The rows: as pipe rows (`| a | b |`) where every cell is plain text, else as
        \\row{\\cell{…} …}. Leading header rows become `header-rows=N`."""
        leading = next((i for i, row in enumerate(table.rows) if not row.header), len(table.rows))
        pipes = [self._pipe_row(row) if self.sugar else None for row in table.rows]
        headers = leading if all(pipes[:leading]) else 0
        if headers:
            opts.insert(
                len(opts) - len(options(Target.TABLE, table.props)), f"header-rows={headers}"
            )
        rows: list[str] = []
        for index, row in enumerate(table.rows):
            pipe = pipes[index]
            if pipe is not None and (not row.header or index < headers):
                rows.append(pipe)
                continue
            row_opts: list[str] = []
            if row.header:
                row_opts.append("header")
            if row.cant_split:
                row_opts.append("cant-split")
            if row.height is not None:
                height = length(row.height, "cm")
                row_opts.append(
                    f"height=({height}, exact)" if row.exact_height else f"height={height}"
                )
            cells = [self.cell(cell) for cell in row.cells]
            rows.append(f"\\row{_opts(row_opts)}{{\n" + _indent("\n".join(cells), INDENT) + "\n}")
        return rows

    def _pipe_row(self, row: ir.Row) -> str | None:
        if row.cant_split or row.height is not None:
            return None
        texts: list[str] = []
        for cell in row.cells:
            if cell.colspan > 1 or cell.rowspan > 1 or cell.props or len(cell.content) != 1:
                return None
            (paragraph,) = cell.content
            if not isinstance(paragraph, ir.Paragraph):
                return None
            empty = paragraph == ir.Paragraph() and self.part_style is None
            if not empty and not self._is_plain(paragraph, self.part_style):
                return None
            text = self.body(cell.content, self.part_style) if not empty else ""
            if "|" in text or "\n" in text:
                return None
            texts.append(text)
        return "| " + " | ".join(texts) + " |"

    def cell(self, cell: ir.Cell) -> str:
        opts: list[str] = []
        if cell.colspan > 1:
            opts.append(f"colspan={cell.colspan}")
        if cell.rowspan > 1:
            opts.append(f"rowspan={cell.rowspan}")
        opts.extend(options(Target.CELL, cell.props))
        blocks = cell.content
        only = blocks[0] if len(blocks) == 1 else None
        if isinstance(only, ir.Paragraph) and only == ir.Paragraph() and not self.part_style:
            blocks = ()  # an empty cell
        return f"\\cell{_opts(opts)}{{{self.body(blocks, self.part_style)}}}"

    # --- inline content --------------------------------------------------------------------

    def inline(self, items: Sequence[ir.Inline], base: PropertySet | None = None) -> str:
        """`items` as markup; with `base` (the run formatting of the paragraph's style),
        direct formatting that only repeats it is left out."""
        saved, self.run_base = self.run_base, base
        try:
            return _join(self.tokens(items))
        finally:
            self.run_base = saved

    def _run_base(self, style: str | None) -> PropertySet | None:
        """The run formatting a paragraph style gives: the defaults, then its chain of
        bases. None where it cannot be known here: in a table (its style formats text too)
        or without sugar."""
        if not self.sugar or self.table_depth:
            return None
        chain: list[ir.Style] = []
        name = style or "Normal"
        while (found := self.styles.get(name.lower())) is not None and found not in chain:
            chain.append(found)
            if found.based_on is None:
                break
            name = found.based_on
        legacy = {"font": "Times New Roman", "size": Length.pt(10)}
        base = PropertySet(Target.RUN, legacy).merged(self.doc.default_run)
        for found in reversed(chain):
            base = base.merged(found.run)
        return base.without(RUN_LAST)  # a style sheet cannot keep these on a paragraph style

    def caption(self, caption: Caption) -> str:
        opts = [quote(caption.kind)]
        if caption.label is not None:
            opts.append(f"id={caption.label}")
        self.styles_used.add("caption")
        text = self.inline(caption.text, self._run_base("Caption"))
        return f"\\caption{_opts(opts)}{{{line_start(text)}}}"

    def _equation_references(self, items: list[ir.Inline]) -> list[ir.Inline]:
        """The items with "(" and ")" taken off plain text around a REF to an equation's
        number: `@eq:x` prints "(3.2)" with them (the REF itself prints the number)."""
        equations = set(self.labels.equations.values())
        out = list(items)
        for i in range(1, len(out) - 1):
            item, before, after = out[i], out[i - 1], out[i + 1]
            ref = self.labels.is_label_field(item) if isinstance(item, ir.Field) else None
            if (
                ref is None
                or ref[0] != "REF"
                or ref[1] not in equations
                or not (isinstance(before, ir.Run) and isinstance(after, ir.Run))
                or before.props
                or before.style
                or after.props
                or after.style
                or not before.text.endswith("(")
                or not after.text.startswith(")")
            ):
                continue
            out[i - 1] = replace(before, text=before.text[:-1])
            out[i + 1] = replace(after, text=after.text[1:])
            self.in_parentheses.add(id(item))
        return [i for i in out if not (isinstance(i, ir.Run) and not i.text)]

    def tokens(self, items: Sequence[ir.Inline]) -> list[_Tok]:
        out: list[_Tok] = []
        items = self._equation_references(_merge_runs(items))
        i = 0
        while i < len(items):
            item = items[i]
            if isinstance(item, ir.CommentStart):
                end = next(
                    (
                        j
                        for j in range(i + 1, len(items))
                        if isinstance(items[j], ir.CommentEnd)
                        and items[j].name == item.comment.name  # type: ignore[union-attr]
                    ),
                    None,
                )
                if end is None:
                    self.lost["comment range across paragraphs (kept as a point)"] += 1
                    out.append(_Tok(self.comment(item.comment, None)))
                else:
                    out.append(_Tok(self.comment(item.comment, items[i + 1 : end])))
                    i = end
                i += 1
                continue
            out.extend(self.item(item))
            i += 1
        return out

    def comment(self, comment: ir.Comment, inner: Sequence[ir.Inline] | None) -> str:
        opts = [f"text={{{self.body(comment.content, default_style='annotation text')}}}"]
        if comment.author:
            opts.append(f"author={quote(comment.author)}")
        if comment.initials:
            opts.append(f"initials={quote(comment.initials)}")
        if comment.date is not None:
            opts.append(f"date={_date(comment.date)}")
        body = "" if inner is None else "{" + line_start(self.inline(inner)) + "}"
        return f"\\comment{_opts(opts)}{body}"

    def item(self, item: ir.Inline) -> list[_Tok]:
        if isinstance(item, ir.Run):
            return self.run(item)
        if isinstance(item, ir.Break):
            return [_Tok("\\pagebreak" if item.kind == "page" else "\\columnbreak", bare=True)]
        if isinstance(item, ir.Hyperlink):
            target = "#" + item.anchor if item.anchor is not None else item.url
            content = [
                replace(r, style=None) if r.style and r.style.lower() == "hyperlink" else r
                for r in item.content
            ]
            inner = _join(self.tokens(content))
            runs = [r for r in item.content if isinstance(r, ir.Run)]
            plain = bool(runs) and all(r.style is None for r in runs)  # no Hyperlink look
            opts = f"to={_string(target)}" + (", style=none" if plain else "")
            return [_Tok(f"\\link[{opts}]{{{line_start(inner)}}}")]
        if isinstance(item, ir.Field):
            if (ref := self.labels.is_label_field(item)) is not None:
                if id(item) in self.in_parentheses:
                    ref = (ref[0], ref[1], "default")
                return [self._reference(*ref)]
            return [_Tok(self.field(item))]
        if isinstance(item, ir.BookmarkStart | ir.BookmarkEnd):
            return self.bookmark(item)
        if isinstance(item, ir.Note):
            return [_Tok(self.note(item))]
        if isinstance(item, ir.NoteMark):
            return []
        if isinstance(item, ir.Image):
            return [_Tok(self.image(item))]
        if isinstance(item, ir.TextBox):
            return [_Tok(self.textbox(item))]
        if isinstance(item, ir.ContentControl):
            return [_Tok(self.control(item))]
        if isinstance(item, ir.RawXml):
            return [_Tok(f"\\raw[ooxml]{{{item.xml}}}")]
        if isinstance(item, ir.Math):
            if item.display:
                self.lost["display equation inside text (printed inline)"] += 1
            return [_Tok(self.math(replace(item, display=False)))]
        if isinstance(item, ir.CommentEnd):
            return []  # its start was printed as a point comment
        self.lost[type(item).__name__.lower()] += 1
        return []

    def run(self, run: ir.Run) -> list[_Tok]:
        tokens: list[_Tok] = []
        for piece in re.split(r"([\t\n])", run.text):
            if piece == "\t":
                tokens.append(_Tok("\\tab", bare=True))
            elif piece == "\n":
                tokens.append(_Tok("\\br", bare=True))
            elif piece:
                # The document has smart-quotes=off, so quotes and dashes need no escape.
                tokens.append(_Tok(inline(piece, punctuation=False, edges=False)))
        props = run.props
        if self.run_base is not None and run.style is None:
            props = _differences(props, self.run_base)  # what the paragraph style already gives
        return self._formatted(tokens, props, run.style, run.text)

    def _formatted(
        self, tokens: list[_Tok], props: PropertySet, style: str | None, text: str = ""
    ) -> list[_Tok]:
        if not tokens:
            return []
        if self.sugar:
            props, wrappers = _simple_formatting(props)
        else:
            wrappers = []
        if props:
            tokens = [
                _Tok(f"\\font{_opts(options(Target.RUN, props))}{{{line_start(_join(tokens))}}}")
            ]
        for command in wrappers:
            tokens = [_Tok(f"\\{command}{{{line_start(_join(tokens))}}}")]
        if style is not None:
            self.styles_used.add(style.lower())
            shown = display_name(style)
            body = line_start(_join(tokens))
            marker = _EMPHASIS.get(shown) if self.sugar and not props and not wrappers else None
            if marker is not None:
                command = {"*": "strong", "_": "emph"}[marker]
                plain = text and text == text.strip() and not set(text) & set("*_\t\n\\")
                sugar = f"{marker}{body}{marker}" if plain else None
                return [_Tok(f"\\{command}{{{body}}}", sugar=sugar, kind="emphasis")]
            tokens = [_Tok(f"\\style[{quote(shown)}]{{{body}}}")]
        return tokens

    def field(self, item: ir.Field) -> str:
        code = item.code.strip()
        simple = _MERGEFORMAT_RE.sub("", code).strip().upper()
        if simple in _SIMPLE_FIELDS:
            text = _SIMPLE_FIELDS[simple] + "{}"
        else:
            opts = f"[result={quote(item.result)}]" if item.result else ""
            text = f"\\field{opts}{{{code}}}"
        if item.props or item.style:
            return _join(self._formatted([_Tok(text)], item.props, item.style))
        return text

    def _reference(self, field: str, label: str, form: str) -> _Tok:
        """A REF or PAGEREF to a label: `@label` where it reads the same way."""
        if field == "PAGEREF":
            return _Tok(f"\\pageref{{{label}}}")
        if form == "default":
            return _Tok(f"\\ref{{{label}}}", sugar=f"@{label}", kind="ref")
        return _Tok(f"\\ref[form={form}]{{{label}}}")

    def bookmark(self, item: ir.BookmarkStart | ir.BookmarkEnd) -> list[_Tok]:
        if item.name.startswith(_HIDDEN_BOOKMARKS) or item.name in self.labels.dropped:
            return []
        ident = self.bookmarks.setdefault(item.name, _RAW_BOOKMARK_IDS + len(self.bookmarks))
        if isinstance(item, ir.BookmarkStart):
            name = item.name.replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;")
            xml = f'<w:bookmarkStart w:id="{ident}" w:name="{name}"/>'
        else:
            xml = f'<w:bookmarkEnd w:id="{ident}"/>'
        return [_Tok(f"\\raw[ooxml]{{{xml}}}")]

    def note(self, note: ir.Note) -> str:
        blocks = list(note.content)
        if blocks and isinstance(blocks[0], ir.Paragraph):
            first = blocks[0]
            content = list(first.content)
            if content and isinstance(content[0], ir.NoteMark):
                content.pop(0)
                if content and isinstance(content[0], ir.Run) and content[0].text.startswith(" "):
                    rest = content[0].text[1:]
                    content[0] = replace(content[0], text=rest)
                    if not rest:
                        content.pop(0)
            blocks[0] = replace(first, content=tuple(content))
        default = f"{note.kind} text"
        return f"\\{note.kind}{{{self.body(tuple(blocks), default_style=default)}}}"

    def image(self, image: ir.Image) -> str:
        opts = [f"width={length(image.width, 'size')}", f"height={length(image.height, 'size')}"]
        if image.description:
            opts.append(f"alt={quote(image.description)}")
        if image.crop is not None:
            crop = image.crop
            sides = [
                f"{side}={number(Decimal(getattr(crop, side)) / 1000)}%"
                for side in ("left", "top", "right", "bottom")
                if getattr(crop, side)
            ]
            if sides:
                opts.append(f"crop=({', '.join(sides)})")
        if image.rotation:
            opts.append(f"rotate={image.rotation}")
        if image.border is not None:
            opts.append(f"border={border(image.border)}")
        if image.anchor is not None:
            opts.extend(self._anchor(image.anchor))
        if image.svg is not None:
            fallback = self._file(image.data, "media", ir.IMAGE_FORMATS[image.format])
            opts.append(f"fallback={quote(fallback)}")
            name = self._file(image.svg, "media", "svg")
        else:
            name = self._file(image.data, "media", ir.IMAGE_FORMATS[image.format])
        return f"\\image{_opts(opts)}{{{name}}}"

    def _file(self, data: bytes, folder: str, extension: str, original: str = "") -> str:
        digest = hashlib.sha256(data).hexdigest()
        if digest in self.media:
            return self.media[digest]
        stem = re.sub(r"[^A-Za-z0-9_-]+", "-", original.rsplit(".", 1)[0]).strip("-")
        base = stem or ("image" if folder == "media" else "document")
        name = f"{folder}/{base}{len(self.media) + 1}.{extension}"
        self.media[digest] = name
        self.files[name] = data
        return name

    def _anchor(self, anchor: ir.Anchor, table: bool = False) -> list[str]:
        default = ir.Anchor()
        opts: list[str] = []
        if table:
            opts.append("float")
        else:
            opts.append(f"wrap={anchor.wrap}")
            if anchor.wrap_side != default.wrap_side:
                opts.append(f"wrap-side={anchor.wrap_side}")
        for axis, at, origin, base in (
            ("x", anchor.x, anchor.x_from, default.x_from),
            ("y", anchor.y, anchor.y_from, default.y_from),
        ):
            where = at if isinstance(at, str) else length(at, "size")
            if origin != base:
                opts.append(f"{axis}=({where}, from={origin})")
            elif where != "0":
                opts.append(f"{axis}={where}")
        if anchor.distance != default.distance:
            sides = ("top", "bottom", "left", "right")
            parts = ", ".join(
                f"{s}={length(d, 'size')}" for s, d in zip(sides, anchor.distance, strict=True)
            )
            opts.append(f"distance=({parts})")
        return opts

    def textbox(self, box: ir.TextBox) -> str:
        default = ir.TextBox((), Length(1))
        opts = [f"width={length(box.width, 'size')}"]
        if box.height is not None:
            opts.append(f"height={length(box.height, 'size')}")
        if box.fill is not None:
            opts.append(f"shading={color(box.fill)}")
        if box.border is not None:
            opts.append(f"border={border(box.border)}")
        if box.padding != default.padding:
            sides = ("top", "bottom", "left", "right")
            parts = ", ".join(
                f"{s}={length(d, 'cm')}" for s, d in zip(sides, box.padding, strict=True)
            )
            opts.append(f"padding=({parts})")
        if box.valign != "top":
            opts.append(f"valign={box.valign}")
        if box.rotation:
            opts.append(f"rotate={box.rotation}")
        if box.name:
            opts.append(f"name={quote(box.name)}")
        if box.anchor is not None:
            opts.extend(self._anchor(box.anchor))
        return f"\\textbox{_opts(opts)}{{{self.body(box.content, self.part_style)}}}"

    def control(self, control: ir.ContentControl) -> str:
        opts = [control.kind]
        for key in ("tag", "title"):
            if value := getattr(control, key):
                opts.append(f"{key}={quote(value)}")
        if control.lock is not None:
            opts.append(f"lock={control.lock}")
        if control.items:
            opts.append("items=(" + ", ".join(quote(i) for i in control.items) + ")")
        if control.kind == "date":
            opts.append(f"format={quote(control.date_format)}")
            if control.date is not None:
                opts.append(f"date={control.date.strftime('%Y-%m-%d')}")
        if control.kind == "checkbox":
            if control.checked:
                opts.append("checked")
            return f"\\control{_opts(opts)}"
        if control.showing_placeholder:
            text = "".join(r.text for r in control.content if isinstance(r, ir.Run))
            opts.append(f"placeholder={quote(text)}")
            return f"\\control{_opts(opts)}"
        content = [
            i
            for i in control.content
            if not (isinstance(i, ir.Run) and i.style and i.style.lower() == "placeholder text")
        ]
        return f"\\control{_opts(opts)}{{{line_start(self.inline(content))}}}"

    # --- the style sheet --------------------------------------------------------------------

    def stylesheet(self) -> str:
        doc = self.doc
        names, numbering = self._closure()
        if self.template is not None:  # the template has the styles, theme and defaults
            lists = [
                self._numbering_rule(d)
                for d in doc.numbering
                if d.name in self.numbering_used and d.name not in self.template.lists
            ]
            if not lists:
                return ""
            head = "% Lists the template cannot give by name (it keeps the styles and theme)."
            return "\n\n".join([head, *lists]) + "\n"
        chunks: list[str] = ["% Styles and lists of the imported document."]
        # Where the original's defaults leave the font or size out, Word uses its legacy
        # Times New Roman 10pt; redocx would fill in its own (the theme font, 11pt).
        legacy = {"font": "Times New Roman", "size": Length.pt(10)}
        run = PropertySet(
            Target.RUN, {k: v for k, v in legacy.items() if k not in doc.default_run}
        ).merged(doc.default_run)
        defaults = [
            *self._decls((Target.RUN, Target.PARAGRAPH), Target.RUN, run, "defaults"),
            *self._decls(
                (Target.RUN, Target.PARAGRAPH), Target.PARAGRAPH, doc.default_paragraph, "defaults"
            ),
        ]
        if defaults:
            chunks.append(_rule("defaults", defaults))
        if doc.theme is not None and (theme := self._theme_rule(doc.theme)):
            chunks.append(theme)
        for definition in doc.numbering:
            if definition.name in numbering:
                chunks.append(self._numbering_rule(definition))
        linked = self._linked_pairs(names)
        skipped = {char.name.lower() for char in linked.values()}
        for style in doc.styles:
            if style.name.lower() in names and style.name.lower() not in skipped:
                chunks.append(self._style_rule(style, linked=style.name.lower() in linked))
        return "\n\n".join(chunks) + "\n"

    def _theme_rule(self, theme: ir.ThemeChoice) -> str | None:
        """The theme's colours and fonts that differ from redocx's own theme."""
        default = Theme()
        body = [
            f"{_THEME_KEYS[slot]}: #{rgb};"
            for slot, rgb in theme.colors
            if slot in _THEME_KEYS and default.colors.get(slot) != rgb
        ]
        for key, font, base in (
            ("major-font", theme.major_font, default.major_font),
            ("minor-font", theme.minor_font, default.minor_font),
        ):
            if font is not None and font != base:
                body.append(f"{key}: {_string(font)};")
        if not body:
            return None
        name = f" {_string(theme.name)}" if theme.name and theme.name != default.name else ""
        return _rule(f"theme{name}", body)

    def _linked_pairs(self, names: set[str]) -> dict[str, ir.Style]:
        """Paragraph styles whose "<name> Char" style formats text the same way: these are
        written as one `linked` rule (lower-case name -> the character style)."""
        pairs: dict[str, ir.Style] = {}
        for name in names:
            style = self.styles[name]
            if style.kind is not ir.StyleKind.PARAGRAPH or style.link is None:
                continue
            char = self.styles.get(style.link.lower())
            expected = f"{display_name(style.name)} Char".lower()
            if (
                char is not None
                and char.kind is ir.StyleKind.CHARACTER
                and display_name(char.name).lower() == expected
                and char.run == style.run
            ):
                pairs[name] = char
        return pairs

    def _closure(self) -> tuple[set[str], set[str]]:
        names = {n for n in self.styles_used if n in self.styles}
        numbering = {n for n in self.numbering_used if n in self.numbering}
        changed = True
        while changed:
            changed = False
            wanted: set[str] = set()
            for name in names:
                style = self.styles[name]
                wanted.update(n.lower() for n in (style.based_on, style.next) if n)
                ref = style.numbering
                if ref is not None and ref.name in self.numbering and ref.name not in numbering:
                    numbering.add(ref.name)
                    changed = True
            for name in numbering:
                wanted.update(lvl.style.lower() for lvl in self.numbering[name].levels if lvl.style)
            new = {n for n in wanted if n in self.styles} - names
            if new:
                names |= new
                changed = True
        return names, numbering

    def _decls(
        self,
        targets: Sequence[Target],
        target: Target,
        props: PropertySet,
        where: str,
    ) -> list[str]:
        """Declarations of `props` that the style-sheet compiler routes back to `target`."""
        skip: set[str] = set()
        for name in props:
            if _routed(name, targets) is not target:
                self.lost[f"{target.label} {name} in {where} (a style sheet sets the "
                          f"{_routed(name, targets).label}'s)"] += 1  # fmt: skip
                skip.add(name)
        return declarations(target, props, frozenset(skip))

    def _style_rule(self, style: ir.Style, linked: bool = False) -> str:
        shown = display_name(style.name)
        name = quote(shown)
        base = f" : {quote(display_name(style.based_on))}" if style.based_on else ""
        kind = style.kind
        if kind is ir.StyleKind.TABLE:
            return self._table_style(style, name, base)
        builtin = BUILTIN_STYLES[shown]() if shown in BUILTIN_STYLES else None
        modify = builtin is not None and builtin.kind is kind and _extends(style, builtin)
        run, paragraph, next_style = style.run, style.paragraph, style.next
        if modify:  # only what differs from redocx's own definition of the style
            assert builtin is not None
            run, paragraph = (
                _differences(run, builtin.run),
                _differences(paragraph, builtin.paragraph),
            )
            next_style = None if style.next == builtin.next else style.next
            base = "" if _same_name(style.based_on, builtin.based_on) else base
        prefix = "modify " if modify else ""
        if kind is ir.StyleKind.CHARACTER:
            body = self._decls((Target.RUN,), Target.RUN, run, "a character style")
            return _rule(f"{prefix}character {name}{base}", body)
        targets = (Target.RUN, Target.PARAGRAPH)
        body = [
            *self._decls(targets, Target.RUN, run, "a paragraph style"),
            *self._decls(targets, Target.PARAGRAPH, paragraph, "a paragraph style"),
        ]
        if next_style is not None and next_style.lower() in self.styles:
            body.append(f"next: {quote(display_name(next_style))};")
        if style.numbering is not None and style.numbering.name in self.numbering:
            body.append(f"numbering: {quote(style.numbering.name)} level {style.numbering.level};")
        if style.name == "Normal" and base:
            base = ""
        word = "linked" if linked else "paragraph"
        return _rule(f"{prefix}{word} {name}{base}", body)

    def _table_style(self, style: ir.Style, name: str, base: str) -> str:
        targets = (Target.RUN, Target.TABLE, Target.CELL, Target.PARAGRAPH)
        body = [
            *self._decls(targets, Target.RUN, style.run, "a table style"),
            *self._decls(targets, Target.PARAGRAPH, style.paragraph, "a table style"),
            *self._decls(targets, Target.TABLE, style.table, "a table style"),
            *self._decls(targets, Target.CELL, style.cell, "a table style"),
        ]
        conditions = {c.kind: c for c in style.conditions}
        if style.repeat_header and "firstRow" not in conditions:
            conditions["firstRow"] = ir.TableCondition("firstRow")
        bands: dict[str, list[str]] = {}
        for kind, condition in conditions.items():
            decls = [
                *self._decls(CONDITION_TARGETS, Target.RUN, condition.run, "a table style part"),
                *self._decls(CONDITION_TARGETS, Target.CELL, condition.cell, "a table style part"),
                *self._decls(
                    CONDITION_TARGETS, Target.PARAGRAPH, condition.paragraph, "a table style part"
                ),
            ]
            if kind == "firstRow" and style.repeat_header:
                decls.append("repeat;")
            if kind in _BAND_NAMES:
                block, index = _BAND_NAMES[kind]
                bands.setdefault(block, []).append(_rule(("odd", "even")[index], decls))
            else:
                body.append(_rule(_PART_NAMES[kind], decls))
        for block, parts in bands.items():
            body.append(_rule(block, parts))
        return _rule(f"table {name}{base}", body)

    def _numbering_rule(self, definition: ir.Numbering) -> str:
        levels: list[str] = []
        for level in definition.levels:
            fmt = re.sub(r"(?<!^)([A-Z])", r"-\1", level.format).lower()
            body = [f"format: {fmt};", f"text: {_string(level.text)};"]
            if level.start != 1:
                body.append(f"start: {level.start};")
            if level.style:
                body.append(f"style: {quote(display_name(level.style))};")
            if level.legal:
                body.append("legal;")
            if level.suffix != "tab":
                body.append(f"suffix: {level.suffix};")
            if level.marker_align != "left":
                body.append(f"marker-align: {level.marker_align};")
            if level.restart_after is not None:
                body.append(f"restart-after: {level.restart_after};")
            targets = (Target.RUN, Target.PARAGRAPH)
            body += self._decls(targets, Target.PARAGRAPH, level.paragraph, "a list level")
            body += self._decls(targets, Target.RUN, level.run, "a list level")
            levels.append(_rule(f"level {level.level}", body))
        return _rule(f"numbering {quote(definition.name)}", levels)


_THEME_KEYS = {slot: key for key, slot in reversed(ir.THEME_COLOR_NAMES.items())}
"""Theme slot -> its style-sheet name (dark1 rather than text1)."""


def _same_name(a: str | None, b: str | None) -> bool:
    return (a or "").lower() == (b or "").lower()


def _extends(style: ir.Style, builtin: ir.Style) -> bool:
    """Whether `modify` of the built-in can give `style`: it sets everything the built-in
    sets (a style sheet cannot take a property away), keeps its base, next style and list."""
    return (
        set(builtin.run) <= set(style.run)
        and set(builtin.paragraph) <= set(style.paragraph)
        and (builtin.based_on is None or style.based_on is not None)
        and (builtin.next is None or style.next is not None)
        and (builtin.numbering is None or style.numbering is not None)
    )


def _differences(props: PropertySet, base: PropertySet) -> PropertySet:
    """The properties of `props` that `base` does not already have with the same value."""
    return props.without(name for name, value in props.items() if base.get(name) == value)


def _string(text: str) -> str:
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _rule(head: str, body: Sequence[str]) -> str:
    if not body:
        return head + " {}"
    return head + " {\n" + _indent("\n".join(body), INDENT) + "\n}"


def _routed(name: str, targets: Sequence[Target]) -> Target:
    """The target the style-sheet compiler gives a key (`_assign` in stylesheet.compiler)."""
    ordered = list(targets)
    if name in RUN_LAST and len(ordered) > 1:
        ordered = [*(t for t in ordered if t is not Target.RUN), Target.RUN]
    return next(t for t in [*ordered, *Target] if name in REGISTRY[t])


def _key(table: dict[str, str], value: str) -> str:
    return next((k for k, v in table.items() if v == value), value)


def _same(a: Length, b: Length) -> bool:
    """Equal as Word stores page measures (twips)."""
    return a.in_twips == b.in_twips


def _page_size(page: ir.PageSetup) -> list[str]:
    for name, size in PAGE_SIZES.items():
        if _same(size.width, page.width) and _same(size.height, page.height):
            return [f"size={name}"]
        if _same(size.height, page.width) and _same(size.width, page.height):
            return [f"size={name}", "orientation=landscape"]
    return [f"size=({length(page.width, 'cm')}, {length(page.height, 'cm')})"]


def _line_numbers(numbers: ir.LineNumbers | None) -> str:
    if numbers is None:
        return "off"
    parts = [f"count-by={numbers.count_by}", f"start={numbers.start}", f"restart={numbers.restart}"]
    if numbers.distance is not None:
        parts.append(f"distance={length(numbers.distance, 'cm')}")
    return "(" + ", ".join(parts) + ")"


_SIMPLE_COMMANDS = (
    ("bold", True, "b"),
    ("italic", True, "i"),
    ("underline", "single", "u"),
    ("strike", True, "s"),
    ("vertical-align", "superscript", "sup"),
    ("vertical-align", "subscript", "sub"),
    ("hidden", True, "hidden"),
)
"""Run properties with a command of their own: \\b{…} for bold, ..."""
_EMPHASIS = {"Strong": "*", "Emphasis": "_"}
"""Character styles with sugar: *strong* and _emphasis_."""


def _simple_formatting(props: PropertySet) -> tuple[PropertySet, list[str]]:
    """The properties left for \\font, and the commands for the others (innermost first)."""
    commands = [cmd for name, value, cmd in _SIMPLE_COMMANDS if props.get(name) == value]
    names = {name for name, value, _ in _SIMPLE_COMMANDS if props.get(name) == value}
    return props.without(names), commands


_CHAPTER_NUMBER_RE = re.compile(r"\d+(\D{1,3})\d+")


def _chapter_separator(document: ir.Document) -> str | None:
    """The separator when every numbered equation has a number per chapter ("2.1", "2-1"):
    the document numbers equations per chapter (`captions=(chapter-numbers)`)."""
    numbers = [
        item.number
        for p in document.all_paragraphs()
        for item in p.content
        if isinstance(item, ir.Math) and item.numbered and item.number is not None
    ]
    separators = {m.group(1) if (m := _CHAPTER_NUMBER_RE.fullmatch(n)) else None for n in numbers}
    if len(separators) != 1:
        return None
    return next(iter(separators))


def _merge_runs(items: Sequence[ir.Inline]) -> list[ir.Inline]:
    """Adjacent runs with the same formatting as one run."""
    out: list[ir.Inline] = []
    for item in items:
        last = out[-1] if out else None
        if (
            isinstance(item, ir.Run)
            and isinstance(last, ir.Run)
            and (last.props, last.style) == (item.props, item.style)
        ):
            out[-1] = replace(last, text=last.text + item.text)
        else:
            out.append(item)
    return out


@dataclass(frozen=True)
class KeptStyles:
    """The original's styles kept as a template the source compiles into."""

    file: str
    """The template's file name, next to the source."""
    lists: frozenset[str]
    """The list definitions the template has by name."""


def print_document(
    document: ir.Document,
    stylesheet_name: str,
    template: KeptStyles | None = None,
    *,
    sugar: bool = True,
) -> Printed:
    """`document` as redocx source that `\\use`s the style sheet `stylesheet_name`, or
    that compiles into `template` (and uses a style sheet only for lists it lacks)."""
    return Printer(document, stylesheet_name, template, sugar).print()
