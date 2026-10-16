"""The index: \\ix marks (XE fields) and \\index (an INDEX field with its entries filled in).

Word computes an index from the XE fields when it updates the INDEX field. The compiler
writes the entries it can know already (the terms, sorted and nested, with letter headings
and cross-references) so the document reads sensibly before that; the page numbers are left
for Word, which updates the field when the document opens.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING

from wordgenx import ir
from wordgenx.commands.bind import Args
from wordgenx.lower.common import BlockHandler, Format, InlineHandler, Piece, verbatim
from wordgenx.props import EMPTY_PARAGRAPH, PropertySet, Target
from wordgenx.syntax import ast
from wordgenx.syntax.diagnostics import Code
from wordgenx.values import Length, TabStop

if TYPE_CHECKING:
    from wordgenx.lower.lowering import Lowering

MAX_LEVELS = 9
"""Word's index styles go to index 9."""
COLUMN_GAP = Length.twips(720)
"""The gap Word leaves between index columns (for the width of right-aligned entries)."""


@dataclass(frozen=True)
class IndexMark(ir.Field):
    """An XE field: where a term is discussed."""

    terms: tuple[str, ...] = ()
    """The entry and its subentries: ("traffic", "flow")."""
    see: str | None = None
    """A cross-reference instead of a page number ("See also queueing")."""


@dataclass(frozen=True)
class PendingIndex(ir.FieldBlock):
    """An INDEX field whose entries are filled in once every \\ix is known."""

    headings: bool = False
    right_align: bool = False
    column_width: Length | None = None
    """For the page-number tab stop of right-aligned entries."""


def xe_instruction(
    terms: tuple[str, ...], see: str | None, bold: bool, italic: bool
) -> tuple[str, ...]:
    """` XE "traffic:flow" \\b `: subentries after colons; colons and quotes escaped."""
    text = ":".join(_escape(term) for term in terms)
    code = f' XE "{text}"'
    if see is not None:
        code += f' \\t "{_escape(see)}"'
    if bold:
        code += " \\b"
    if italic:
        code += " \\i"
    return (code + " ",)


def index_instruction(columns: int, headings: bool, right_align: bool) -> tuple[str, ...]:
    code = " INDEX"
    if right_align:
        code += ' \\e "\t"'
    if headings:
        code += ' \\h "A"'
    if columns > 1:
        code += f' \\c "{columns}"'
    return (code + " ",)


def _escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace('"', '\\"').replace(":", "\\:")


@dataclass
class _Entry:
    text: str
    see: list[str] = field(default_factory=lambda: [])
    children: dict[str, _Entry] = field(default_factory=lambda: {})


def collect(blocks: tuple[ir.Block, ...]) -> list[IndexMark]:
    """Every \\ix in document order: body, tables, notes, headers and footers."""
    marks: list[IndexMark] = []

    def visit(paragraph: ir.Paragraph) -> ir.Paragraph:
        for item in paragraph.content:
            inner = item.content if isinstance(item, ir.Hyperlink) else (item,)
            marks.extend(i for i in inner if isinstance(i, IndexMark))
        return paragraph

    ir.map_paragraphs(blocks, visit)
    return marks


def sort_key(text: str) -> tuple[str, str]:
    """Word's order, roughly: letters without accents, case-insensitive."""
    plain = "".join(
        c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c)
    ).casefold()
    return plain, text


def entries(index: PendingIndex, marks: list[IndexMark]) -> tuple[ir.Paragraph, ...]:
    """The index paragraphs Word would write, without the page numbers."""
    root = _Entry("")
    for mark in marks:
        node = root
        for term in mark.terms:
            node = node.children.setdefault(term, _Entry(term))
        if mark.see is not None and mark.see not in node.see:
            node.see.append(mark.see)
    tabs = (
        (TabStop(index.column_width, "right", "dot"),)
        if index.right_align and index.column_width is not None
        else ()
    )
    props = PropertySet(Target.PARAGRAPH, {"tabs": tabs}) if tabs else EMPTY_PARAGRAPH
    out: list[ir.Paragraph] = []

    def emit(node: _Entry, level: int) -> None:
        text = node.text + "".join(f". {see}" for see in node.see)  # as Word: "x. See y"
        out.append(ir.Paragraph((ir.Run(text),), f"index {level}", props))
        for child in sorted(node.children.values(), key=lambda e: sort_key(e.text)):
            emit(child, level + 1)

    letter: str | None = None
    for node in sorted(root.children.values(), key=lambda e: sort_key(e.text)):
        first = sort_key(node.text)[0][:1].upper()
        if index.headings and first != letter:
            letter = first
            out.append(ir.Paragraph((ir.Run(first),), "index heading", props))
        emit(node, 1)
    if not out:
        out.append(ir.Paragraph((ir.Run("No index entries found."),)))
    return tuple(out)


def expand(blocks: tuple[ir.Block, ...]) -> tuple[tuple[ir.Block, ...], set[str]]:
    """The body with every \\index filled in, and the styles its entries use."""
    if not any(isinstance(b, PendingIndex) for b in blocks):
        return blocks, set()
    marks = collect(blocks)
    used: set[str] = set()
    out: list[ir.Block] = []
    for block in blocks:
        if isinstance(block, PendingIndex):
            content = entries(block, marks)
            used.update(p.style for p in content if p.style)
            out.append(ir.FieldBlock(replace(block.field), content))
        else:
            out.append(block)
    return tuple(out), used


__all__ = ["IndexMark", "PendingIndex", "expand", "index_instruction", "xe_instruction"]


# --- commands ------------------------------------------------------------------------------


def ix(lw: Lowering, command: ast.Command, args: Args, fmt: Format) -> list[Piece]:
    r"""\ix[see="queueing", bold]{traffic!flow}: an index entry (XE field) at this point;
    `!` separates subentries."""
    body = verbatim(command)
    if not body:
        return []
    terms = tuple(t.strip() for t in body.split("!"))
    if not all(terms):
        lw.report(
            Code.INVALID_ARGUMENT,
            "\\ix needs a term, with `!` before each subentry: \\ix{traffic!flow}",
            command.span,
        )
        return []
    if len(terms) > MAX_LEVELS:
        lw.report(
            Code.INVALID_ARGUMENT,
            f"index entries nest at most {MAX_LEVELS} levels deep",
            command.span,
        )
        terms = terms[:MAX_LEVELS]
    see: str | None = None
    if (text := args.text("see")) is not None:
        see = f"See {text}"
    if (text := args.text("see-also")) is not None:
        see = f"See also {text}"
    if (text := args.text("xref")) is not None:
        see = text
    instruction = xe_instruction(terms, see, args.flag("bold"), args.flag("italic"))
    mark = IndexMark(instruction, has_result=False, terms=terms, see=see)
    return [Piece("", Format(), "field", field=mark)]


def index(lw: Lowering, command: ast.Command, args: Args) -> list[ir.Block]:
    r"""\index[columns=2, headings, right-align, title="Index", level=1]: the index of
    the \ix entries."""
    if not lw.in_body(command):
        return []
    columns = args.number("columns", 2) or 2
    headings, right_align = args.flag("headings"), args.flag("right-align")
    out: list[ir.Block] = []
    if title := args.text("title"):
        heading = f"Heading {args.number('level', 1)}"
        lw.use_style(heading, ir.StyleKind.PARAGRAPH, command.span)
        out.append(ir.Paragraph((ir.Run(title),), heading, unnumbered=True))
    lw.has_content = True
    # Word puts a multi-column index into continuous section breaks of its own when it
    # updates the field (on open), so the entries are written in the current section.
    width = lw.section.page.text_width.emu
    if columns > 1:
        width = (width - COLUMN_GAP.emu * (columns - 1)) // columns
    field = ir.Field(index_instruction(columns, headings, right_align), dirty=True)
    pending = PendingIndex(
        field, (), headings=headings, right_align=right_align, column_width=Length(width)
    )
    return [*out, pending]


BLOCKS: dict[str, BlockHandler] = {"index": index}
INLINES: dict[str, InlineHandler] = {"ix": ix}
