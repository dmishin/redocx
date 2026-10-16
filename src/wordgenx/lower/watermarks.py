r"""Watermarks: \watermark (design §3.16).

Word keeps a watermark in the headers: a shape behind the text, centred on the margins.
`\watermark` sets one for the current section and the ones after it; once the document is
lowered, `apply_watermarks` puts it into every header those sections show (default, first
page, even pages), taking the header content they would otherwise inherit.
"""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from typing import TYPE_CHECKING

from wordgenx import ir
from wordgenx.commands.bind import Args
from wordgenx.commands.types import Percent
from wordgenx.lower.common import BlockHandler
from wordgenx.lower.media import raster, read_file, resolve_size
from wordgenx.syntax import ast
from wordgenx.syntax.diagnostics import Code
from wordgenx.values import Color, Length

if TYPE_CHECKING:
    from wordgenx.lower.lowering import Lowering

CAPS_WIDTH = Decimal("0.62")
"""The width of a capital letter in ems, roughly: a text watermark's proportions."""


def watermark(lw: Lowering, command: ast.Command, args: Args) -> list[ir.Block]:
    r"""\watermark[DRAFT], \watermark[image=logo.png], \watermark[none]."""
    if lw.container_depth > 0:
        lw.report(
            Code.MISPLACED,
            "\\watermark belongs to a section, not to a table, header, footer or note",
            command.span,
        )
        return []
    if args.flag("none"):
        lw.section = replace(lw.section, watermark=None)
        return []
    text, image_name = args.text("text"), args.text("image")
    if (text is None) == (image_name is None):
        lw.report(
            Code.INVALID_ARGUMENT,
            "\\watermark takes text or an image",
            command.span,
            help='\\watermark[DRAFT], \\watermark[image="logo.png"] or \\watermark[none]',
        )
        return []
    size = args.values.get("width")
    width = resolve_size(lw, size) if isinstance(size, Length | Percent) else None
    page_width = lw.section.page.text_width
    made: ir.Watermark
    if text is not None:
        width = width or page_width
        height = Length(int(width.emu / max(Decimal(1), CAPS_WIDTH * len(text))))
        color = args.get("color", Color) or ir.Watermark(width, height, text).color
        made = ir.Watermark(
            width,
            height,
            text=text,
            color=color,
            font=args.text("font") or "Calibri",
            diagonal=args.text("layout", "diagonal") == "diagonal",
            semitransparent=args.values.get("semitransparent", True) is True,
        )
    else:
        assert image_name is not None
        data = read_file(lw, command, image_name, "image")
        found = raster(lw, command, image_name, data) if data is not None else None
        if data is None or found is None:
            return []
        image_format, natural = found
        if width is None:
            width = natural[0] if natural[0].emu <= page_width.emu else page_width
        height = Length(natural[1].emu * width.emu // natural[0].emu)
        picture = ir.Image(data, image_format, width, height, name=image_name)
        made = ir.Watermark(
            width,
            height,
            image=picture,
            washout=args.values.get("washout", True) is True,
        )
    lw.section = replace(lw.section, watermark=made)
    return []


def apply_watermarks(
    body: tuple[ir.Block, ...], final: ir.Section, even_pages: bool
) -> tuple[tuple[ir.Block, ...], ir.Section, bool]:
    """Every section with a watermark gets it in each header it shows; a section after one
    with a watermark but without its own gets plain copies of the inherited headers.
    Returns the body, the final section, and whether a new Header paragraph was made."""
    inherited: dict[str, tuple[ir.Block, ...]] = {}
    previous: ir.Watermark | None = None
    made_paragraph = False

    def apply(section: ir.Section) -> ir.Section:
        nonlocal previous, made_paragraph
        for kind, blocks in section.headers:
            inherited[kind] = blocks
        mark = section.watermark
        if mark is None and previous is None:
            return section
        previous = mark
        kinds = [
            "default",
            *(["first"] if section.title_page else []),
            *(["even"] if even_pages else []),
        ]
        headers = dict(section.headers)
        for kind in kinds:
            content = inherited.get(kind, ())
            if mark is not None:
                content, new = _with_watermark(content, mark)
                made_paragraph = made_paragraph or new
            headers[kind] = content
        ordered = tuple((k, headers[k]) for k in ir.HEADER_KINDS if k in headers)
        return replace(section, headers=ordered)

    out: list[ir.Block] = []
    for block in body:
        out.append(
            ir.SectionBreak(apply(block.section)) if isinstance(block, ir.SectionBreak) else block
        )
    return tuple(out), apply(final), made_paragraph


def _with_watermark(
    content: tuple[ir.Block, ...], mark: ir.Watermark
) -> tuple[tuple[ir.Block, ...], bool]:
    """The header content with the watermark at the start of its first paragraph (where
    Word puts it), or in a paragraph of its own."""
    first = content[0] if content else None
    if isinstance(first, ir.Paragraph):
        return (replace(first, content=(mark, *first.content)), *content[1:]), False
    return (ir.Paragraph((mark,), "Header"), *content), True


BLOCKS: dict[str, BlockHandler] = {"watermark": watermark}
