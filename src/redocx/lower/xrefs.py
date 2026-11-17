r"""References and generated text: \ref, \pageref, \seq, \footnote, \endnote, \caption,
\figure, \toc and \listof (design §3.9, §3.11-3.13)."""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from redocx import ir
from redocx.commands.bind import Args
from redocx.commands.types import Levels, Percent
from redocx.lower.common import (
    BlockHandler,
    Format,
    InlineHandler,
    Piece,
    bookmarked,
    is_blank,
    marked,
    markup,
    verbatim,
)
from redocx.lower.media import resolve_size
from redocx.lower.references import PendingRef, PendingToc
from redocx.props import EMPTY_PARAGRAPH, PropertySet, Target
from redocx.syntax import ast
from redocx.syntax.diagnostics import Code
from redocx.syntax.source import Span
from redocx.values import Length

if TYPE_CHECKING:
    from redocx.lower.lowering import Lowering

SEQ_NAME_RE = re.compile(r"[A-Za-z][A-Za-z0-9_]*")


# --- references and counters ---------------------------------------------------------------


def make_ref(lw: Lowering, label: str, form: str, span: Span, fmt: Format) -> list[Piece]:
    """A placeholder REF field, resolved once every label is known."""
    ref_id = len(lw.references.refs) + 1
    lw.references.refs[ref_id] = (span, lw.source_of(span))
    ref = PendingRef(
        (f" REF {label} ",), "", fmt.props, fmt.style, label=label, form=form, ref_id=ref_id
    )
    return [Piece("", fmt, "field", field=ref)]


def ref(lw: Lowering, command: ast.Command, args: Args, fmt: Format) -> list[Piece]:
    r"""\ref[form=number]{label} and \pageref{label}."""
    label = verbatim(command)
    if not label:
        return []
    form = "page" if command.name == "pageref" else args.text("form", "default")
    assert form is not None
    return make_ref(lw, label, form, command.span, fmt)


def seq(lw: Lowering, command: ast.Command, args: Args, fmt: Format) -> list[Piece]:
    r"""\seq[Req, id=req:x, restart=heading-1 | 1, format=decimal]: a SEQ counter."""
    name = args.text("name")
    if name is None:
        return []
    if not SEQ_NAME_RE.fullmatch(name):
        lw.report(
            Code.INVALID_ARGUMENT,
            "a counter name is letters, digits and _, starting with a letter",
            args.span_of("name"),
        )
        return []
    restart = args.text("restart")
    code = f" SEQ {name} \\* {args.text('format', 'ARABIC')} " + (f"{restart} " if restart else "")
    pieces = [Piece("", fmt, "field", field=ir.Field((code,), "1", fmt.props, fmt.style))]
    if (label := args.text("id")) is not None:
        bookmark = lw.register_label(label, args.span_of("id"), "seq").bookmark
        pieces = bookmarked(bookmark, pieces, fmt)
    return pieces


# --- notes ---------------------------------------------------------------------------------


def note(lw: Lowering, command: ast.Command, args: Args, fmt: Format) -> list[Piece]:
    r"""\footnote[id=fn:x]{text} and \endnote{...}: the reference mark here, the text in the
    footnotes or endnotes part. The body may hold several paragraphs, lists and tables."""
    kind = command.name
    if lw.part is not None:
        where = "another note" if lw.part in ir.NOTE_KINDS else f"a {lw.part}"
        article = "an" if kind == "endnote" else "a"
        lw.report(Code.MISPLACED, f"{article} {kind} cannot be inside {where}", command.span)
        return []
    if not isinstance(command.body, ast.MarkupBody):
        return []
    title = kind.capitalize()
    text_style, mark_style = f"{title} Text", f"{title} Reference"
    lw.use_style(text_style, ir.StyleKind.PARAGRAPH, command.span)
    lw.use_style(mark_style, ir.StyleKind.CHARACTER, command.span)
    with lw.contained(part=kind):
        lowered = lw.blocks(command.body.blocks)
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
    pieces = [Piece("", fmt, "note", note=ir.Note(kind, tuple(blocks), fmt.props, mark_style))]
    if (label := args.text("id")) is not None:
        bookmark = lw.register_label(label, args.span_of("id"), "note").bookmark
        pieces = bookmarked(bookmark, pieces, fmt)
    return pieces


# --- captions and figures ------------------------------------------------------------------


@dataclass
class FigureContext:
    label: str | None
    label_span: Span
    width: Length | None
    captioned: bool = False


def make_caption(
    lw: Lowering,
    kind: str,
    label: str | None,
    span: Span,
    text: tuple[ir.Inline, ...],
    keep_with_next: bool,
) -> ir.Paragraph:
    """A Caption paragraph, "Figure {SEQ Figure} : text", with the bookmarks Word's
    Insert Cross-reference would use: label and number, number only, text only, whole."""
    lw.use_style("Caption", ir.StyleKind.PARAGRAPH, span)
    seq_name = kind.replace(" ", "_")
    number: tuple[ir.Inline, ...] = (ir.Field((f" SEQ {seq_name} \\* ARABIC ",), "1"),)
    if lw.caption_chapters is not None:
        style, separator = lw.caption_chapters
        match = re.fullmatch(r"[Hh]eading ([1-9])", style)
        level = match.group(1) if match else "1"
        number = (
            ir.Field((f' STYLEREF "{style}" \\s ',), "1"),
            ir.Run(separator),
            ir.Field((f" SEQ {seq_name} \\* ARABIC \\s {level} ",), "1"),
        )
    body = text
    separator = (ir.Run(": "),) if text else ()
    if label is not None:
        entry = lw.register_label(label, span, "caption")
        refs = lw.references
        entry.parts.update(
            number=refs.bookmark(label, "_n"),
            text=refs.bookmark(label, "_t"),
            full=refs.bookmark(label, "_f"),
        )
        number = marked(entry.parts["number"], number)
        head = marked(entry.bookmark, (ir.Run(f"{kind} "), *number))
        if text:
            body = marked(entry.parts["text"], text)
        content = marked(entry.parts["full"], (*head, *separator, *body))
    else:
        content = marked(
            lw.references.hidden_bookmark(), (ir.Run(f"{kind} "), *number, *separator, *body)
        )
    props = (
        PropertySet(Target.PARAGRAPH, {"keep-with-next": True})
        if keep_with_next
        else EMPTY_PARAGRAPH
    )
    return ir.Paragraph(content, "Caption", props)


def _neighbours(lw: Lowering) -> tuple[str, bool]:
    """(kind, caption is above its subject) inferred from the blocks around a caption."""
    previous, following = lw.previous, lw.following
    if isinstance(previous, ir.Table):
        return "Table", False
    if isinstance(previous, ir.Paragraph) and previous.style == "Code Block":
        return "Listing", False
    if isinstance(previous, ir.Paragraph) and any(
        isinstance(i, ir.Image) for i in previous.content
    ):
        return "Figure", False
    if isinstance(following, ast.PipeTable) or _starts_with(following, "table"):
        return "Table", True
    if isinstance(following, ast.CodeBlock):
        return "Listing", True
    return "Figure", True


def _starts_with(item: ast.Inline | ast.Block | None, command: str) -> bool:
    """Whether `item` is (or is a paragraph starting with) the block command `command`."""
    if isinstance(item, ast.Paragraph):
        item = next((c for c in item.content if not is_blank(c)), None)
    return isinstance(item, ast.Command) and item.name == command


def caption(lw: Lowering, command: ast.Command, args: Args) -> list[ir.Block]:
    r"""\caption[Table, id=tbl:x]{text}; the kind defaults from the neighbouring block."""
    lw.has_content = True
    kind = (args.text("kind") or "").strip() or None
    label, label_span = args.text("id"), args.span_of("id")
    text = lw.paragraph_content(command)
    inferred, above = _neighbours(lw)
    figure = lw.figure
    if figure is not None:
        inferred = "Figure"
        if figure.label is not None:
            if label is not None:
                lw.report(
                    Code.INVALID_ARGUMENT,
                    "both the \\figure and its \\caption have an id",
                    label_span,
                    help="keep one of them",
                )
            else:
                label, label_span = figure.label, figure.label_span
        figure.captioned = True
    return [make_caption(lw, kind or inferred, label, label_span, text, above)]


def figure(lw: Lowering, command: ast.Command, args: Args) -> list[ir.Block]:
    r"""\figure[id=fig:x, width=12cm, align=center]{\image{...} \caption{...}}: the
    image and caption paragraphs, kept together on one page."""
    lw.has_content = True
    if lw.figure is not None:
        lw.report(Code.MISPLACED, "a \\figure cannot contain another \\figure", command.span)
        return []
    label = args.text("id")
    size = args.values.get("width")
    width = resolve_size(lw, size) if isinstance(size, Length | Percent) else None
    context = FigureContext(label, args.span_of("id"), width)
    lw.figure = context
    try:
        blocks = lw.blocks(markup(command))
    finally:
        lw.figure = None
    if label is not None and not context.captioned:
        lw.report(
            Code.INVALID_ARGUMENT,
            "a labelled \\figure needs a \\caption: references point at the caption",
            context.label_span,
        )
    centred = PropertySet(Target.PARAGRAPH, {"align": "center"})
    base = centred.merged(args.props or EMPTY_PARAGRAPH)
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


# --- tables of contents --------------------------------------------------------------------


def contents(lw: Lowering, command: ast.Command, args: Args) -> list[ir.Block]:
    r"""\toc[levels=1-3, title="Contents"] and \listof[Figure, title=...]."""
    lw.has_content = True
    if not lw.in_body(command):
        return []
    out: list[ir.Block] = []
    if (title := args.text("title")) is not None:
        lw.use_style("TOC Heading", ir.StyleKind.PARAGRAPH, command.span)
        out.append(ir.Paragraph(lw.assemble([Piece(title, Format())]), "TOC Heading"))
    if command.name == "toc":
        levels = args.get("levels", Levels) or Levels(1, 3)
        code = f' TOC \\o "{levels.first}-{levels.last}" \\h \\z \\u '
        out.append(
            PendingToc(ir.Field((code,), dirty=True), (), levels=(levels.first, levels.last))
        )
    else:
        kind = args.text("kind", "Figure") or "Figure"
        field = ir.Field((f' TOC \\h \\z \\c "{kind.replace(" ", "_")}" ',), dirty=True)
        out.append(PendingToc(field, (), caption_kind=kind))
    return out


BLOCKS: dict[str, BlockHandler] = {
    "caption": caption,
    "figure": figure,
    "toc": contents,
    "listof": contents,
}
INLINES: dict[str, InlineHandler] = {
    "ref": ref,
    "pageref": ref,
    "seq": seq,
    "footnote": note,
    "endnote": note,
}
