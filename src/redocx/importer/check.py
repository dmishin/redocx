"""The oracle (M6 step 5): does the imported source compile to what the original was?

`compare` puts the original document and the one its imported source compiles to side by
side: their text, their blocks (each paragraph's style, formatting and list level, each
table's spans and header rows), the effective formatting of each body paragraph's runs, and
document-wide counts (sections, notes, comments, pictures, text boxes). What the importer
is known to leave out is not counted as a difference: drop caps (rebuilt by the lowering),
generated blocks (a TOC is rebuilt; an INDEX comes back as its field) and headers Word does
not show. Layout (pages, line breaks) is Word's and is not compared.
"""

from __future__ import annotations

import difflib
import re
from collections import Counter
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from typing import cast

from redocx import ir
from redocx.math.nodes import canonical
from redocx.ooxml.style_names import stored_name
from redocx.props import PropertySet, Target
from redocx.values import Length, LineSpacing, TabStop

_SHOWN = 8
"""Differences listed in the report (the rest are counted)."""


@dataclass(frozen=True)
class Comparison:
    text: str | None
    """Where the text first differs (None: the same)."""
    blocks: tuple[int, int]
    """Blocks that match, of the original's."""
    paragraphs: tuple[int, int]
    """Body paragraphs whose runs look the same, of the original's."""
    differences: tuple[str, ...]

    @property
    def same(self) -> bool:
        return self.text is None and not self.differences

    def render(self) -> str:
        lines = [
            "Its text is the original's."
            if self.text is None
            else f"Its text differs from the original's: {self.text}",
            f"Blocks that match the original: {self.blocks[0]} of {self.blocks[1]}.",
            f"Paragraphs whose text looks the same: {self.paragraphs[0]} of {self.paragraphs[1]}.",
        ]
        if self.differences:
            lines.append("Differences:")
            lines += [f"  {d}" for d in self.differences[:_SHOWN]]
            if len(self.differences) > _SHOWN:
                lines.append(f"  ... and {len(self.differences) - _SHOWN} more")
        return "\n".join(lines)


def compare(
    original: ir.Document, rebuilt: ir.Document, rebuilt_styles: ir.Document | None = None
) -> Comparison:
    """`rebuilt_styles` gives the styles and defaults the rebuilt document is resolved with
    when they are not its own (compiled into a template: the original's)."""
    differences: list[str] = []
    a, b = blocks_of(original), blocks_of(rebuilt)
    matched = _matched(a, b, "block", _describe_block, differences)
    looks_a = looks_of(original, original)
    looks_b = looks_of(rebuilt, rebuilt_styles or rebuilt)
    looking = _matched(looks_a, looks_b, "paragraph", _describe_look, differences)
    counts_a, counts_b = counts_of(original), counts_of(rebuilt)
    for name in counts_a | counts_b:
        if counts_a[name] != counts_b[name]:
            differences.append(f"{name}: {counts_a[name]} in the original, {counts_b[name]} here")
    return Comparison(
        text_difference(original, rebuilt),
        (matched, len(a)),
        (looking, len(looks_a)),
        tuple(differences),
    )


# --- blocks ------------------------------------------------------------------------------

Block = tuple[object, ...]


def blocks_of(document: ir.Document) -> list[Block]:
    """Each paragraph's style, formatting, list level and equations, and each table's spans
    and header rows, in order: the body (with notes and text boxes where they are anchored),
    then the headers and footers Word shows."""
    out: list[Block] = []

    def walk(items: tuple[ir.Block, ...]) -> None:
        for block in items:
            only = (
                block.content[0]
                if isinstance(block, ir.Paragraph) and len(block.content) == 1
                else None
            )
            if isinstance(block, ir.FieldBlock) or (
                isinstance(only, ir.Field) and only.code.split()[:1] == ["INDEX"]
            ):
                out.append(("field block",))  # an INDEX block comes back as its field
            elif isinstance(block, ir.Paragraph) and block.drop_cap is None:
                style = stored_name(block.style or "Normal").lower()
                level = block.list_item.level if block.list_item else None
                equations = tuple(
                    (canonical(i.expr), i.display, i.number)
                    for i in block.content
                    if isinstance(i, ir.Math)
                )
                out.append(("paragraph", style, in_twips(block.props), level, equations))
                for item in block.content:
                    if isinstance(item, ir.Note | ir.TextBox):
                        walk(item.content)
            elif isinstance(block, ir.Table):
                spans = [[(c.colspan, c.rowspan) for c in row.cells] for row in block.rows]
                out.append(("table", spans, [row.header for row in block.rows]))
                for row in block.rows:
                    for cell in row.cells:
                        walk(cell.content)

    walk(document.body)
    for section in document.sections():
        for kind, parts in (*section.headers, *section.footers):
            shown = kind == "default" or (
                section.title_page if kind == "first" else document.settings.even_and_odd_headers
            )
            if shown:  # the parts Word does not show are not imported (and reported)
                walk(parts)
    return out


def in_twips(props: PropertySet) -> dict[str, object]:
    """Lengths as Word stores them (twips), so 1.02cm and 578 twips are the same; line
    spacing too (240ths of a line), so 1.08 and 259/240 lines are the same."""

    def value(v: object) -> object:
        if isinstance(v, Length):
            return v.in_twips
        if isinstance(v, LineSpacing):
            return (v.rule, v.line_attr)
        if isinstance(v, tuple):  # tab stops
            stops = cast(tuple[TabStop, ...], v)
            return tuple((t.position.in_twips, t.align, t.leader) for t in stops)
        return v

    return {name: value(v) for name, v in props.items()}


def _describe_block(a: Block | None, b: Block | None) -> str:
    if a is None or b is None:
        kind = (a or b or ("block",))[0]
        return f"a {kind} " + ("missing" if b is None else "that the original does not have")
    if a[0] != b[0]:
        return f"a {a[0]} became a {b[0]}"
    if a[0] == "paragraph":
        parts: list[str] = []
        if a[1] != b[1]:
            parts.append(f"style {a[1]} became {b[1]}")
        props_a, props_b = cast(dict[str, object], a[2]), cast(dict[str, object], b[2])
        if changed := sorted(k for k in {*props_a, *props_b} if props_a.get(k) != props_b.get(k)):
            parts.append("formatting differs: " + ", ".join(changed))
        if a[3] != b[3]:
            parts.append(f"list level {a[3]} became {b[3]}")
        if a[4] != b[4]:
            parts.append("an equation differs")
        return f"paragraph ({a[1]}): " + "; ".join(parts)
    return "table: " + ("its spans differ" if a[1] != b[1] else "its header rows differ")


# --- looks ---------------------------------------------------------------------------------

Look = list[tuple[str, dict[str, object]]]


def looks_of(document: ir.Document, styled_by: ir.Document) -> list[Look]:
    """For each body paragraph: its text in pieces of one effective run formatting (the
    defaults, the paragraph style's chain, the character style's, then direct formatting),
    whitespace left out. Styles and defaults come from `styled_by`."""
    styles = {stored_name(s.name).lower(): s for s in styled_by.styles}

    def chain(name: str | None) -> PropertySet:
        found: list[ir.Style] = []
        while name is not None and (style := styles.get(stored_name(name).lower())) is not None:
            if style in found:
                break
            found.append(style)
            name = style.based_on
        props = PropertySet(Target.RUN)
        for style in reversed(found):
            props = props.merged(style.run)
        return props

    # Word's own defaults where a document's leave the font or size out.
    legacy = PropertySet(Target.RUN, {"font": "Times New Roman", "size": "10pt"})
    base = legacy.merged(styled_by.default_run)
    out: list[Look] = []
    for block in document.body:
        if not isinstance(block, ir.Paragraph) or block.drop_cap is not None:
            continue
        paragraph = base.merged(chain(block.style or "Normal"))
        pieces: Look = []
        for run in block.runs():
            look = paragraph.merged(chain(run.style)).merged(run.props)
            text, key = "".join(run.text.split()), in_twips(look)
            if not text:
                continue
            if pieces and pieces[-1][1] == key:
                pieces[-1] = (pieces[-1][0] + text, key)
            else:
                pieces.append((text, key))
        if pieces:
            out.append(pieces)
    return out


def _describe_look(a: Look | None, b: Look | None) -> str:
    if a is None or b is None:
        return "missing" if b is None else "not in the original"
    for (text_a, look_a), (text_b, look_b) in zip(a, b, strict=False):
        if text_a != text_b:
            return "its text is split into differently formatted pieces"
        if look_a != look_b:
            changed = sorted(k for k in {*look_a, *look_b} if look_a.get(k) != look_b.get(k))
            return "text formatted differently: " + ", ".join(changed)
    return "its text is split into differently formatted pieces"


def _matched[T](
    a: Sequence[T],
    b: Sequence[T],
    what: str,
    describe: Callable[[T | None, T | None], str],
    differences: list[str],
) -> int:
    """How many of `a` match `b` in order; the others are described in `differences`."""
    keys_a, keys_b = [repr(x) for x in a], [repr(x) for x in b]
    matcher = difflib.SequenceMatcher(None, keys_a, keys_b, autojunk=False)
    matched = 0
    for op, i1, i2, j1, j2 in matcher.get_opcodes():
        if op == "equal":
            matched += i2 - i1
            continue
        for k in range(max(i2 - i1, j2 - j1)):
            x = a[i1 + k] if i1 + k < i2 else None
            y = b[j1 + k] if j1 + k < j2 else None
            index = i1 + k + 1 if x is not None else j1 + k + 1
            differences.append(f"{what} {index}: {describe(x, y)}")
    return matched


# --- counts and text -------------------------------------------------------------------------


def counts_of(document: ir.Document) -> Counter[str]:
    counts: Counter[str] = Counter(sections=len(document.sections()))
    for paragraph in document.all_paragraphs():
        for item in paragraph.content:
            if isinstance(item, ir.Note):
                counts[f"{item.kind}s"] += 1
            elif isinstance(item, ir.CommentStart):
                counts["comments"] += 1
            elif isinstance(item, ir.Image):
                counts["pictures"] += 1
            elif isinstance(item, ir.TextBox):
                counts["text boxes"] += 1
    return counts


def visible_text(document: ir.Document) -> str:
    """The text of every run, in document order (notes, comments and text boxes where they
    are anchored; headers and footers last), without whitespace. Field results and
    generated blocks (a table of contents) are left out: Word recomputes them."""
    return "".join(_texts_of_blocks(document.body)) + "".join(
        text
        for section in document.sections()
        for _, blocks in (*section.headers, *section.footers)
        for text in _texts_of_blocks(blocks)
    )


def text_difference(original: ir.Document, rebuilt: ir.Document) -> str | None:
    a, b = visible_text(original), visible_text(rebuilt)
    if a == b:
        return None
    at = next(
        (i for i, (x, y) in enumerate(zip(a, b, strict=False)) if x != y), min(len(a), len(b))
    )
    return f"after {at} of {len(a)} characters ({len(b)} in the import)"


def _texts_of_blocks(blocks: tuple[ir.Block, ...]) -> Iterator[str]:
    for block in blocks:
        if isinstance(block, ir.Paragraph):
            yield from _texts_of_inlines(block.content)
        elif isinstance(block, ir.Table):
            for row in block.rows:
                for cell in row.cells:
                    yield from _texts_of_blocks(cell.content)


def _texts_of_inlines(items: tuple[ir.Inline, ...]) -> Iterator[str]:
    for item in items:
        if isinstance(item, ir.Run):
            yield re.sub(r"\s+", "", item.text)
        elif isinstance(item, ir.Hyperlink):
            yield from _texts_of_inlines(tuple(r for r in item.content if isinstance(r, ir.Run)))
        elif isinstance(item, ir.ContentControl):
            yield from _texts_of_inlines(item.content)
        elif isinstance(item, ir.Note | ir.TextBox):
            yield from _texts_of_blocks(item.content)
        elif isinstance(item, ir.CommentStart):
            yield from _texts_of_blocks(item.comment.content)
