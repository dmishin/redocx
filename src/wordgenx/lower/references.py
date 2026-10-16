"""Cross-references, captions, counters and tables of contents (design §3.9-3.13).

Lowering emits placeholders (PendingRef, PendingToc) and bookmarks; `resolve` runs
once styles and numbering are final. It numbers SEQ fields (captions, counters) the way
Word will, simulates list numbering to know what "clause 4.2" reads, replaces each
reference placeholder with a REF/PAGEREF field whose cached result is already right,
and fills tables of contents and lists of figures with entries.
"""

from __future__ import annotations

import difflib
import hashlib
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace

from wordgenx import ir
from wordgenx.ooxml.parts.numbering import complete_levels
from wordgenx.ooxml.style_names import style_id
from wordgenx.props import EMPTY_PARAGRAPH, PropertySet, Target
from wordgenx.syntax.diagnostics import Code, Diagnostic, Severity
from wordgenx.syntax.source import Source, Span
from wordgenx.values import Length, TabStop

Located = tuple[Source, Diagnostic]

REF_FORMS = ("default", "number", "text", "full", "page", "above-below", "relative")
SEQ_SWITCHES = {
    "ARABIC": "decimal",
    "alphabetic": "lowerLetter",
    "ALPHABETIC": "upperLetter",
    "roman": "lowerRoman",
    "ROMAN": "upperRoman",
}
"""SEQ \\* format switches -> list number formats."""
TOC_NUMBER_TAB = 440
"""Twips per level for the tab after a heading number in a TOC entry (Word's own value)."""
STYLEREF_RE = re.compile(r'^\s*STYLEREF\s+"([^"]+)"\s+\\s\b', re.IGNORECASE)
"""A chapter number in a caption: the paragraph number of the latest heading."""
SEQ_RE = re.compile(r"^\s*SEQ\s+(\S+)(.*)$", re.IGNORECASE)


# --- placeholders ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PendingRef(ir.Field):
    """A reference to a label, resolved into a REF/PAGEREF field."""

    label: str = ""
    form: str = "default"
    ref_id: int = 0


@dataclass(frozen=True)
class PendingToc(ir.FieldBlock):
    """A TOC field whose entries are filled in once all headings and captions are known."""

    levels: tuple[int, int] | None = None
    """Heading levels for a table of contents."""
    caption_kind: str | None = None
    """Caption label (Figure, Table, ...) for a list of figures."""


@dataclass
class Label:
    name: str
    bookmark: str
    """The main bookmark: the whole heading or paragraph, a caption's "Figure 3", a counter."""
    kind: str
    """heading | item | paragraph | caption | seq | note"""
    span: Span
    source: Source
    parts: dict[str, str] = field(default_factory=lambda: {})
    """Captions only: bookmarks for the "number", "text" and "full" reference forms."""


@dataclass
class References:
    """What lowering collects for the resolve step."""

    labels: dict[str, Label] = field(default_factory=lambda: {})
    refs: dict[int, tuple[Span, Source]] = field(default_factory=lambda: {})
    bookmark_names: set[str] = field(default_factory=lambda: set[str]())
    generated: int = 0

    def bookmark(self, label: str, suffix: str = "") -> str:
        """A unique Word bookmark name (at most 40 characters) for a label."""
        base = "_Ref_" + re.sub(r"[^A-Za-z0-9]", "_", label)
        name = base + suffix
        if len(name) > 40:
            digest = hashlib.sha1(label.encode()).hexdigest()[:8]
            name = base[: 40 - len(suffix) - 9] + "_" + digest + suffix
        unique, n = name, 1
        while unique in self.bookmark_names:
            n += 1
            unique = f"{name[: 40 - len(str(n))]}{n}"
        self.bookmark_names.add(unique)
        return unique

    def hidden_bookmark(self, prefix: str = "_Toc") -> str:
        self.generated += 1
        name = f"{prefix}{self.generated:09d}"
        self.bookmark_names.add(name)
        return name


# --- number formatting ---------------------------------------------------------------------


def _roman(n: int) -> str:
    values = [(1000, "m"), (900, "cm"), (500, "d"), (400, "cd"), (100, "c"), (90, "xc"),
              (50, "l"), (40, "xl"), (10, "x"), (9, "ix"), (5, "v"), (4, "iv"),
              (1, "i")]  # fmt: skip
    out = ""
    for value, letters in values:
        while n >= value:
            out += letters
            n -= value
    return out


def format_number(n: int, number_format: str) -> str:
    """A list counter as Word shows it (common formats; others fall back to decimal)."""
    if number_format == "none":
        return ""
    if number_format in ("lowerLetter", "upperLetter"):
        letter = chr(ord("a") + (n - 1) % 26) * ((n - 1) // 26 + 1) if n > 0 else ""
        return letter.upper() if number_format == "upperLetter" else letter
    if number_format in ("lowerRoman", "upperRoman"):
        roman = _roman(n)
        return roman.upper() if number_format == "upperRoman" else roman
    if number_format == "decimalZero":
        return f"{n:02d}"
    if number_format == "chicago":  # *, †, ‡, §, then **, ††, ...
        return "*†‡§"[(n - 1) % 4] * ((n - 1) // 4 + 1) if n > 0 else ""
    return str(n)


# --- the resolve step ------------------------------------------------------------------------


class _Numbering:
    """Simulates Word's list counters in document order."""

    def __init__(
        self, styles: Mapping[str, ir.Style], numbering: Mapping[str, ir.Numbering]
    ) -> None:
        self.styles = styles
        self.levels = {name: complete_levels(n) for name, n in numbering.items()}
        self.counters: dict[tuple[object, ...], list[int | None]] = {}
        self.separator = ""
        """What follows the last number returned: the level's tab, space or nothing."""
        self.linked = {
            style_id(level.style): ir.NumberingRef(n.name, level.level)
            for n in numbering.values()
            for level in n.levels
            if level.style is not None
        }
        """Styles numbered by a list level that names them (the level -> style link)."""

    def style_chain(self, name: str | None) -> list[ir.Style]:
        chain: list[ir.Style] = []
        current = self.styles.get(style_id(name or "Normal"))
        while current is not None and current not in chain:
            chain.append(current)
            current = self.styles.get(style_id(current.based_on)) if current.based_on else None
        return chain

    def outline_level(self, paragraph: ir.Paragraph) -> int | None:
        level = paragraph.props.get("outline-level")
        if isinstance(level, int):
            return level
        for style in self.style_chain(paragraph.style):
            value = style.paragraph.get("outline-level")
            if isinstance(value, int):
                return value
        return None

    def number(self, paragraph: ir.Paragraph) -> str | None:
        """Advance the counters for this paragraph; return its rendered number, if numbered."""
        if paragraph.unnumbered:
            return None
        item = paragraph.list_item
        if item is not None:
            key: tuple[object, ...] = ("list", item.numbering, item.instance)
            name, level, start = item.numbering, item.level, item.start
        else:
            ref = next(
                (
                    s.numbering or self.linked.get(style_id(s.name))
                    for s in self.style_chain(paragraph.style)
                    if s.numbering or style_id(s.name) in self.linked
                ),
                None,
            )
            if ref is None:
                return None
            key, name, level, start = ("style", ref.name), ref.name, ref.level, None
        levels = self.levels.get(name)
        if levels is None:
            return None
        counts = self.counters.setdefault(key, [None] * 10)
        definition = levels[level - 1]
        if counts[level] is None:
            counts[level] = start if (start is not None and level == 1) else definition.start
        else:
            counts[level] = (counts[level] or 0) + 1
        for deeper in range(level + 1, 10):
            counts[deeper] = None
        for upper in range(1, level):
            if counts[upper] is None:
                counts[upper] = levels[upper - 1].start
        self.separator = {"tab": "\t", "space": " "}.get(definition.suffix, "")
        if definition.format == "bullet":
            return definition.text

        def placeholder(match: re.Match[str]) -> str:
            k = int(match.group(1))
            value = counts[k] or 0
            fmt = "decimal" if definition.legal else levels[k - 1].format
            return format_number(value, fmt)

        return re.sub(r"%(\d)", placeholder, definition.text)


@dataclass
class _Target:
    paragraph_index: int
    texts: dict[str, str]
    """Text inside each bookmark of the paragraph."""
    number: str | None
    paragraph_text: str


def _bookmark_texts(paragraph: ir.Paragraph, notes: Mapping[int, str]) -> dict[str, str]:
    """Text inside each bookmark of the paragraph; a note counts as its number (`notes`
    maps id(note) to it)."""
    open_names: list[str] = []
    texts: dict[str, list[str]] = {}
    for item in paragraph.content:
        if isinstance(item, ir.BookmarkStart):
            open_names.append(item.name)
            texts.setdefault(item.name, [])
        elif isinstance(item, ir.BookmarkEnd):
            if item.name in open_names:
                open_names.remove(item.name)
        else:
            text = (
                item.text
                if isinstance(item, ir.Run)
                else item.result
                if isinstance(item, ir.Field)
                else notes.get(id(item), "")
            )
            for name in open_names:
                texts[name].append(text)
    return {name: "".join(parts) for name, parts in texts.items()}


def _seq_name(item: ir.Field) -> str | None:
    match = SEQ_RE.match(item.code)
    return match.group(1) if match else None


def resolve(
    body: tuple[ir.Block, ...],
    styles: Mapping[str, ir.Style],
    numbering: Mapping[str, ir.Numbering],
    settings: ir.Settings,
    references: References,
    report: Callable[[Source, Span, str, str], None],
    text_width: Length | None = None,
) -> tuple[tuple[ir.Block, ...], set[str], bool]:
    """Resolve references and fill TOCs. Returns (body, styles used by TOC entries, whether
    Word should update fields when the document opens)."""
    has_toc = any(isinstance(b, PendingToc) for b in body)
    simulator = _Numbering(styles, numbering)

    # 1. Headings get hidden _Toc bookmarks when there is a table of contents.
    if has_toc:

        def mark_heading(paragraph: ir.Paragraph) -> ir.Paragraph:
            if (
                paragraph.style in ("TOC Heading", "Caption")
                or simulator.outline_level(paragraph) is None
            ):
                return paragraph
            name = references.hidden_bookmark()
            return replace(
                paragraph,
                content=(ir.BookmarkStart(name), *paragraph.content, ir.BookmarkEnd(name)),
            )

        body = ir.map_paragraphs(body, mark_heading)

    # 2. In document order: list numbers, SEQ results, and what each bookmark covers.
    targets: dict[str, _Target] = {}
    entries: list[tuple[int, ir.Paragraph, str | None]] = []
    """(index, paragraph, number with its separator) in document order."""
    seq_counts: dict[str, int] = {}
    seq_restarts: dict[str, int] = {}
    """SEQ identifier -> heading level that restarts it (the \\s switch)."""
    index = 0
    note_counts: dict[str, int] = dict.fromkeys(ir.NOTE_KINDS, 0)
    chapter_numbers: dict[str, str] = {}
    """Lower-cased style name -> the number of its latest paragraph (STYLEREF ... \\s)."""
    note_numbers: dict[int, str] = {}
    """id(note) -> its number as Word shows it."""
    equations: dict[str, object] = {"chapter": None, "count": 0}

    def number_pass(paragraph: ir.Paragraph) -> ir.Paragraph:
        nonlocal index
        index += 1
        number = simulator.number(paragraph)
        if number:
            chapter_numbers[(paragraph.style or "Normal").lower()] = number.rstrip(".")
        level = simulator.outline_level(paragraph)
        if level is not None and level >= 1:
            for name in list(seq_counts):
                restart = seq_restarts.get(name)
                if restart is not None and level <= restart:
                    seq_counts[name] = 0
        content: list[ir.Inline] = []
        for item in paragraph.content:
            if (
                isinstance(item, ir.Field)
                and not isinstance(item, PendingRef)
                and (seq := _seq_name(item))
            ):
                reset = re.search(r"\\r\s*(\d+)", item.code)
                seq_counts[seq] = int(reset.group(1)) if reset else seq_counts.get(seq, 0) + 1
                restart_level = re.search(r"\\s\s*(\d)", item.code)
                if restart_level:
                    seq_restarts[seq] = int(restart_level.group(1))
                switch = re.search(r"\\\*\s*(\w+)", item.code)
                number_format = (
                    SEQ_SWITCHES.get(switch.group(1), "decimal") if switch else "decimal"
                )
                item = replace(item, result=format_number(seq_counts[seq], number_format))
            elif isinstance(item, ir.Field) and (styleref := STYLEREF_RE.match(item.code)):
                item = replace(item, result=chapter_numbers.get(styleref.group(1).lower(), "0"))
            elif isinstance(item, ir.Math) and item.numbered:
                chapter = None
                if item.chapter is not None:
                    chapter = chapter_numbers.get(item.chapter[0].lower(), "0")
                if chapter != equations["chapter"]:
                    equations["chapter"], equations["count"] = chapter, 0
                equations["count"] = int(equations["count"]) + 1  # type: ignore[call-overload]
                count = equations["count"]
                eq_number = (
                    f"{chapter}{item.chapter[1]}{count}" if item.chapter and chapter else str(count)
                )
                item = replace(item, number=eq_number)
            elif isinstance(item, ir.Note):
                notes = settings.notes(item.kind)
                note_counts[item.kind] += 1
                value = (notes.start or 1) + note_counts[item.kind] - 1
                note_numbers[id(item)] = format_number(value, notes.number_format(item.kind))
            content.append(item)
        paragraph = replace(paragraph, content=tuple(content))
        texts = _bookmark_texts(paragraph, note_numbers)
        for item in paragraph.content:  # an equation's bookmark is inside the formula
            if isinstance(item, ir.Math) and item.bookmark and item.number:
                texts[item.bookmark] = item.number  # Word's REF shows it without the ( )
        for name in texts:
            targets[name] = _Target(index, texts, number, paragraph.text())
        entries.append((index, paragraph, number + simulator.separator if number else None))
        return paragraph

    numbered: list[ir.Block] = []
    for block in body:
        numbered.extend(ir.map_paragraphs((block,), number_pass))
        if isinstance(block, ir.SectionBreak):
            for kind in ir.NOTE_KINDS:
                if settings.notes(kind).restart == "eachSect":
                    note_counts[kind] = 0
    body = tuple(numbered)
    order = {id(p): i for i, p, _ in entries}

    # 3. References.
    needs_update = has_toc
    ref_index = 0

    def resolve_refs(paragraph: ir.Paragraph) -> ir.Paragraph:
        nonlocal ref_index, needs_update
        ref_index += 1
        if not any(isinstance(item, PendingRef) for item in paragraph.content):
            return paragraph
        here = order.get(id(paragraph), ref_index)
        content: list[ir.Inline] = []
        for item in paragraph.content:
            if isinstance(item, PendingRef):
                resolved = _resolve_ref(item, references, targets, here, settings, report)
                needs_update = needs_update or resolved.dirty
                label = references.labels.get(item.label)
                if label is not None and label.kind == "equation" and item.form == "default":
                    # "(3.2)": the parentheses around the field, as LaTeX's \eqref
                    content += [
                        ir.Run("(", item.props, item.style),
                        resolved,
                        ir.Run(")", item.props, item.style),
                    ]
                else:
                    content.append(resolved)
            else:
                content.append(item)
        return replace(paragraph, content=tuple(content))

    body = ir.map_paragraphs(body, resolve_refs)

    # 4. Tables of contents and lists of figures.
    used_styles: set[str] = set()
    filled: list[ir.Block] = []
    for block in body:
        if isinstance(block, PendingToc):
            content = _toc_entries(block, entries, simulator, used_styles, text_width)
            filled.append(ir.FieldBlock(replace(block.field), content))
        else:
            filled.append(block)
    return tuple(filled), used_styles, needs_update


def _resolve_ref(
    ref: PendingRef,
    references: References,
    targets: dict[str, _Target],
    here: int,
    settings: ir.Settings,
    report: Callable[[Source, Span, str, str], None],
) -> ir.Field:
    label = references.labels.get(ref.label)
    if label is None:
        span, source = references.refs.get(ref.ref_id, (Span(0, 0), None))
        close = difflib.get_close_matches(ref.label, list(references.labels), n=1)
        if source is not None:
            report(
                source,
                span,
                f"unknown label `{ref.label}`",
                f"did you mean `{close[0]}`?" if close else "",
            )
        return ir.Field((f" REF {ref.label} ",), f"[{ref.label}]", ref.props, ref.style)
    bookmark = label.bookmark
    target = targets.get(bookmark)
    texts = target.texts if target else {}

    def field(code: str, result: str, dirty: bool = False) -> ir.Field:
        return ir.Field((code,), result, ref.props, ref.style, dirty)

    form = ref.form
    if form == "page":
        return field(f" PAGEREF {bookmark} \\h ", "1", dirty=True)
    if form == "above-below":
        below = target is not None and target.paragraph_index > here
        return field(f" REF {bookmark} \\p \\h ", "below" if below else "above")
    if form == "relative" and label.kind in ("heading", "item", "paragraph"):
        return field(f" REF {bookmark} \\r \\h ", (target.number or "") if target else "")
    if label.kind == "note":  # footnote numbers restarting on each page are Word's to know
        per_page = settings.footnotes.restart == "eachPage"
        return field(f" NOTEREF {bookmark} \\h ", texts.get(bookmark, ""), dirty=per_page)
    if label.kind == "equation":  # the number; "(3.2)" has parentheses around the field
        return field(f" REF {bookmark} \\h ", texts.get(bookmark, ""))
    if label.kind == "caption":
        name = label.parts.get(form, bookmark)
        return field(f" REF {name} \\h ", texts.get(name, ""))
    if label.kind == "seq":
        return field(f" REF {bookmark} \\h ", texts.get(bookmark, ""))
    number = target.number if target else None
    text = texts.get(bookmark, target.paragraph_text if target else "")
    if form == "text" or (form == "default" and not number):
        return field(f" REF {bookmark} \\h ", text)
    if form == "full" and number:
        return field(f" REF {bookmark} \\h ", text)
    return field(f" REF {bookmark} \\w \\h ", (number or "").rstrip("."))


def _toc_entries(
    toc: PendingToc,
    entries: list[tuple[int, ir.Paragraph, str | None]],
    simulator: _Numbering,
    used_styles: set[str],
    text_width: Length | None,
) -> tuple[ir.Paragraph, ...]:
    """Entry paragraphs, with the tab stops Word writes into each: one after a heading
    number, and the dotted one for the page number (template TOC styles have none)."""
    page_tab = (TabStop(text_width, "right", "dot"),) if text_width is not None else ()
    result: list[ir.Paragraph] = []
    for _, paragraph, number in entries:
        number_tab: tuple[TabStop, ...] = ()
        first = paragraph.content[0] if paragraph.content else None
        if not isinstance(first, ir.BookmarkStart):
            continue
        if toc.levels is not None:
            level = simulator.outline_level(paragraph)
            if (
                level is None
                or not toc.levels[0] <= level <= toc.levels[1]
                or paragraph.style == "TOC Heading"
            ):
                continue
            text = paragraph.text()
            if number:  # as Word writes it: the number, then the level's tab or space
                text = number + text
            if number and number.endswith("\t"):
                # Word gives such entries a tab stop for the text after the number.
                number_tab = (TabStop(Length.twips(TOC_NUMBER_TAB * level), "left"),)
            style = f"TOC {level}"
        else:
            if paragraph.style != "Caption" or not _is_caption_of(
                paragraph, toc.caption_kind or ""
            ):
                continue
            text = paragraph.text()
            style = "table of figures"
        bookmark = _toc_bookmark(paragraph)
        page = ir.Field((f" PAGEREF {bookmark} \\h ",), "", dirty=True)
        link = ir.Hyperlink("", (ir.Run(text), ir.Run("\t"), page), anchor=bookmark)
        used_styles.add(style)
        tabs = (*number_tab, *page_tab)
        props = PropertySet(Target.PARAGRAPH, {"tabs": tabs}) if tabs else EMPTY_PARAGRAPH
        result.append(ir.Paragraph((link,), style, props))
    if not result:
        result.append(ir.Paragraph((ir.Run("No entries found: update the field (F9) in Word."),)))
    return tuple(result)


def _toc_bookmark(paragraph: ir.Paragraph) -> str:
    """The outermost bookmark of a heading or caption (its _Toc or full-caption bookmark)."""
    first = paragraph.content[0]
    assert isinstance(first, ir.BookmarkStart)
    return first.name


def _is_caption_of(paragraph: ir.Paragraph, kind: str) -> bool:
    return any(
        isinstance(item, ir.Field) and _seq_name(item) == kind.replace(" ", "_")
        for item in paragraph.content
    )


def report_to(diagnostics: list[Located]) -> Callable[[Source, Span, str, str], None]:
    def report(source: Source, span: Span, message: str, label: str) -> None:
        diagnostics.append(
            (source, Diagnostic(Severity.ERROR, Code.UNDEFINED_LABEL, message, span, label))
        )

    return report
