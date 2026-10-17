"""Labels from bookmarks (M6 step 4): which bookmarks the document's REF and PAGEREF
fields point at, and which of them the language can name.

A bookmark around a whole heading becomes `{#sec:…}` on the heading. In a caption Word
writes as `Figure {SEQ Figure \\* ARABIC}: text`, a bookmark around "Figure 1" becomes the
caption's `id` (what `@fig:…` prints), one around the whole caption its `full` form, one
around the number its `number` form. The fields that point at them become `@label`,
`\\ref[form=…]{label}` and `\\pageref{label}`. Hidden `_Ref` bookmarks nothing points at
are dropped; any other bookmark stays as raw XML.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterator
from dataclasses import dataclass, field

from wordgenx import ir

_REF_RE = re.compile(r"(REF|PAGEREF)\s+(\S+)((?:\s+\\[hp*]\s*(?:MERGEFORMAT)?)*)\s*", re.IGNORECASE)
_SEQ_RE = re.compile(r"SEQ\s+(\S+)\s+\\\*\s*ARABIC(?:\s+\\\*\s*MERGEFORMAT)?\s*", re.IGNORECASE)
_HEADING_RE = re.compile(r"heading [1-9]", re.IGNORECASE)
_KIND_PREFIXES = {"figure": "fig", "table": "tbl", "listing": "lst", "equation": "eq"}
HIDDEN = ("_Toc", "_GoBack")
"""Bookmarks Word keeps for itself (the TOC's, the last edit's): never printed."""


@dataclass(frozen=True)
class Caption:
    """A caption paragraph the language writes as `\\caption[kind]{text}`."""

    kind: str
    text: tuple[ir.Inline, ...]
    """What follows "Figure 1: " (bookmarks left out)."""
    label: str | None = None


@dataclass
class Labels:
    references: dict[str, tuple[str, str]] = field(default_factory=lambda: {})
    """Bookmark -> (label, form): what a REF to it prints (form "default", "full",
    "number" or "text")."""
    headings: dict[int, str] = field(default_factory=lambda: {})
    """id() of a heading paragraph -> its label."""
    captions: dict[int, Caption] = field(default_factory=lambda: {})
    """id() of a caption paragraph -> how to print it."""
    dropped: set[str] = field(default_factory=lambda: set[str]())
    """Bookmarks not to print: labels now, or hidden ones nothing refers to."""

    def is_label_field(self, item: ir.Field) -> tuple[str, str, str] | None:
        """(REF or PAGEREF, label, form) for a field that points at a label."""
        if item.props or item.style or any(isinstance(p, ir.Field) for p in item.instruction):
            return None
        m = _REF_RE.fullmatch(item.code.strip())
        if m is None or "\\p" in m.group(3).lower() or m.group(2) not in self.references:
            return None
        label, form = self.references[m.group(2)]
        return m.group(1).upper(), label, form


def analyse(document: ir.Document) -> Labels:
    labels = Labels()
    targets = set(_referenced(document))
    styles = {s.name.lower(): s for s in document.styles}
    taken: set[str] = set()
    for blocks in _block_lists(document):
        for index, block in enumerate(blocks):
            if not isinstance(block, ir.Paragraph):
                continue
            following = blocks[index + 1] if index + 1 < len(blocks) else None
            _heading(block, targets, labels, taken, numbered=_numbered(block, styles))
            _caption(block, following, targets, labels, taken)
    for paragraph in document.all_paragraphs():
        for item in paragraph.content:
            if not isinstance(item, ir.BookmarkStart | ir.BookmarkEnd):
                continue
            if item.name.startswith("_Ref") and item.name not in targets:
                labels.dropped.add(item.name)
    return labels


def _referenced(document: ir.Document) -> Iterator[str]:
    for paragraph in document.all_paragraphs():
        for item in paragraph.content:
            fields = [item] if isinstance(item, ir.Field) else []
            if isinstance(item, ir.Hyperlink):
                fields = [f for f in item.content if isinstance(f, ir.Field)]
                if item.anchor is not None:
                    yield item.anchor
            for each in fields:
                if (m := _REF_RE.fullmatch(each.code.strip())) is not None:
                    yield m.group(2)


def _block_lists(document: ir.Document) -> Iterator[tuple[ir.Block, ...]]:
    """Every list of sibling blocks: the body, cells, notes, text boxes, headers."""
    pending: list[tuple[ir.Block, ...]] = [document.body]
    for section in document.sections():
        pending += [blocks for _, blocks in (*section.headers, *section.footers)]
    while pending:
        blocks = pending.pop()
        yield blocks
        for block in blocks:
            if isinstance(block, ir.Table):
                pending += [cell.content for row in block.rows for cell in row.cells]
            elif isinstance(block, ir.Paragraph):
                for item in block.content:
                    if isinstance(item, ir.Note | ir.TextBox):
                        pending.append(item.content)


def _visible(content: tuple[ir.Inline, ...]) -> tuple[list[ir.Inline], dict[str, tuple[int, int]]]:
    """The content without bookmarks, and each bookmark's (start, end) in it."""
    items: list[ir.Inline] = []
    starts: dict[str, int] = {}
    spans: dict[str, tuple[int, int]] = {}
    for item in content:
        if isinstance(item, ir.BookmarkStart):
            starts[item.name] = len(items)
        elif isinstance(item, ir.BookmarkEnd):
            if item.name in starts:
                spans[item.name] = (starts[item.name], len(items))
        else:
            items.append(item)
    return items, spans


def _numbered(paragraph: ir.Paragraph, styles: dict[str, ir.Style]) -> bool:
    """Whether the paragraph shows a number (its own list item, or its style's list)."""
    if paragraph.list_item is not None:
        return True
    if paragraph.unnumbered:
        return False
    name, seen = paragraph.style, set[str]()
    while name is not None and name.lower() in styles and name.lower() not in seen:
        seen.add(name.lower())
        style = styles[name.lower()]
        if style.numbering is not None:
            return True
        name = style.based_on
    return False


def _heading(
    paragraph: ir.Paragraph, targets: set[str], labels: Labels, taken: set[str], numbered: bool
) -> None:
    if paragraph.style is None or not _HEADING_RE.fullmatch(paragraph.style):
        return
    items, spans = _visible(paragraph.content)
    whole = [name for name, span in spans.items() if name in targets and span == (0, len(items))]
    if not whole or not items:
        return
    label = _unique("sec:" + _slug(_text(items)), taken)
    labels.headings[id(paragraph)] = label
    for name in whole:  # a REF to a heading prints its text (@label: its number, if any)
        labels.references[name] = (label, "text" if numbered else "default")
        labels.dropped.add(name)


def _caption(
    paragraph: ir.Paragraph,
    following: ir.Block | None,
    targets: set[str],
    labels: Labels,
    taken: set[str],
) -> None:
    if (paragraph.style or "").lower() != "caption" or paragraph.list_item or paragraph.drop_cap:
        return
    keep = {"keep-with-next": True}
    above = isinstance(following, ir.Table)  # the lowering keeps a caption above its table
    if dict(paragraph.props) != (keep if above else {}):
        return
    items, spans = _visible(paragraph.content)
    seq = next((i for i, item in enumerate(items) if isinstance(item, ir.Field)), None)
    if seq is None or not all(_plain(item) for item in items[:seq]):
        return
    head = "".join(item.text for item in items[:seq] if isinstance(item, ir.Run))
    field = items[seq]
    assert isinstance(field, ir.Field)
    m = _SEQ_RE.fullmatch(field.code.strip())
    kind = head[:-1]
    if (
        m is None
        or not head.endswith(" ")
        or not kind.strip()
        or m.group(1) != kind.replace(" ", "_")
        or field.props
        or field.style
    ):
        return
    rest = items[seq + 1 :]
    if rest:  # ": text": the separator the lowering writes
        first = rest[0]
        if not (_plain(first) and isinstance(first, ir.Run) and first.text.startswith(": ")):
            return
        rest = [ir.Run(first.text[2:]), *rest[1:]] if first.text != ": " else rest[1:]
    forms = {(0, seq + 1): "default", (0, len(items)): "full", (seq, seq + 1): "number"}
    named = {name: forms[span] for name, span in spans.items() if name in targets and span in forms}
    if any(name not in named and not name.startswith("_") for name in spans):
        return  # a bookmark of the author's own: the caption stays a paragraph that keeps it
    label = None
    if named:
        prefix = _KIND_PREFIXES.get(kind.lower(), _slug(kind) or "cap")
        label = _unique(f"{prefix}:{_slug(_text(rest)) or 'caption'}", taken)
        for name, form in named.items():
            labels.references[name] = (label, form)
            labels.dropped.add(name)
    labels.captions[id(paragraph)] = Caption(kind, tuple(rest), label)
    # Bookmarks in a caption that nothing refers to are Word's own: the caption makes its own.
    labels.dropped.update(name for name in spans if name not in targets and name[:1] == "_")


def _plain(item: ir.Inline) -> bool:
    return isinstance(item, ir.Run) and not item.props and item.style is None


def _text(items: list[ir.Inline]) -> str:
    return "".join(item.text for item in items if isinstance(item, ir.Run))


def _slug(text: str) -> str:
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    words = re.findall(r"[a-z0-9]+", ascii_text.lower())
    slug = "-".join(words)[:32].strip("-")
    return slug if slug[:1].isalpha() else ("x-" + slug if slug else "")


def _unique(label: str, taken: set[str]) -> str:
    label = label.rstrip(":-") or "label"
    candidate, n = label, 2
    while candidate in taken:
        candidate, n = f"{label}-{n}", n + 1
    taken.add(candidate)
    return candidate
