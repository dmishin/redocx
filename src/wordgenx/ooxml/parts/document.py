"""The main document part (word/document.xml)."""

from __future__ import annotations

import re
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass

from wordgenx.ir import (
    Block,
    Break,
    Cell,
    Document,
    Hyperlink,
    Image,
    Inline,
    PageSetup,
    Paragraph,
    Row,
    Run,
    Slot,
    Table,
    table_layout,
)
from wordgenx.ooxml.parts.properties import paragraph_properties, run_properties
from wordgenx.ooxml.schema_order import SECT_PR, TBL_BORDERS, TBL_PR, TC_PR, TR_PR, ordered
from wordgenx.ooxml.style_names import style_id
from wordgenx.ooxml.xml import E, Element, nsmap
from wordgenx.props import EmitContext

_SPLIT_RE = re.compile(r"(\t|\n)")
PICTURE_URI = "http://schemas.openxmlformats.org/drawingml/2006/picture"


@dataclass(frozen=True)
class BodyContext:
    emit: EmitContext
    hyperlink: Callable[[str], str]
    """Returns the relationship id for an external hyperlink target."""
    list_num_ids: Mapping[tuple[str, int], int]
    """(numbering name, list instance) -> w:numId."""
    image: Callable[[Image], str] = lambda image: ""
    """Stores the image as a media part; returns its relationship id."""
    drawing_id: Callable[[], int] = lambda: 1
    """A new document-wide unique id for wp:docPr."""


# --- runs and inline content --------------------------------------------------------------


def _run_content(text: str) -> Iterator[Element]:
    for piece in _SPLIT_RE.split(text):
        if piece == "\t":
            yield E("w:tab")
        elif piece == "\n":
            yield E("w:br")
        elif piece:
            preserve = piece != piece.strip() or "  " in piece
            yield E("w:t", {"xml:space": "preserve"} if preserve else None, text=piece)


def _run(run: Run, ctx: BodyContext) -> Element:
    style = style_id(run.style) if run.style else None
    return E("w:r", run_properties(run.props, ctx.emit, style_id=style), *_run_content(run.text))


def _drawing(image: Image, ctx: BodyContext) -> Element:
    rel_id = ctx.image(image)
    drawing_id = ctx.drawing_id()
    size = {"cx": str(image.width.emu), "cy": str(image.height.emu)}
    name = image.name or f"Picture {drawing_id}"
    description = {"descr": image.description} if image.description else None
    picture = E(
        "pic:pic",
        E(
            "pic:nvPicPr",
            E("pic:cNvPr", {"id": "0", "name": name}, description),
            E("pic:cNvPicPr"),
        ),
        E(
            "pic:blipFill",
            E("a:blip", {"r:embed": rel_id}),
            E("a:stretch", E("a:fillRect")),
        ),
        E(
            "pic:spPr",
            E("a:xfrm", E("a:off", {"x": "0", "y": "0"}), E("a:ext", size)),
            E("a:prstGeom", {"prst": "rect"}, E("a:avLst")),
        ),
    )
    inline = E(
        "wp:inline",
        {"distT": "0", "distB": "0", "distL": "0", "distR": "0"},
        E("wp:extent", size),
        E("wp:effectExtent", {"l": "0", "t": "0", "r": "0", "b": "0"}),
        E("wp:docPr", {"id": str(drawing_id), "name": name}, description),
        E("wp:cNvGraphicFramePr", E("a:graphicFrameLocks", {"noChangeAspect": "1"})),
        E("a:graphic", E("a:graphicData", {"uri": PICTURE_URI}, picture)),
    )
    return E("w:r", E("w:drawing", inline))


def _inline(item: Inline, ctx: BodyContext) -> Element:
    if isinstance(item, Run):
        return _run(item, ctx)
    if isinstance(item, Break):
        return E("w:r", E("w:br", {"w:type": item.kind}))
    if isinstance(item, Image):
        return _drawing(item, ctx)
    assert isinstance(item, Hyperlink)
    return E(
        "w:hyperlink",
        {"r:id": ctx.hyperlink(item.url), "w:history": "1"},
        *(_run(run, ctx) for run in item.content),
    )


def _paragraph(paragraph: Paragraph, ctx: BodyContext) -> Element:
    style = style_id(paragraph.style) if paragraph.style else None
    numbering: tuple[int, int] | None = None
    if paragraph.list_item is not None:
        ref = paragraph.list_item
        numbering = (ctx.list_num_ids[(ref.numbering, ref.instance)], ref.level - 1)
    elif paragraph.unnumbered:
        numbering = (0, 0)  # numId 0 removes numbering inherited from the style
    return E(
        "w:p",
        paragraph_properties(paragraph.props, ctx.emit, style_id=style, numbering=numbering),
        *(_inline(item, ctx) for item in paragraph.content),
    )


# --- tables -----------------------------------------------------------------------------------


def _blocks(blocks: tuple[Block, ...], ctx: BodyContext) -> list[Element]:
    """Blocks of a body or cell.

    Word needs a paragraph between two tables (adjacent tables merge into one) and after
    a table that ends a cell or the body, so empty paragraphs are added there.
    """
    elements: list[Element] = []
    previous: Block | None = None
    for block in blocks:
        if isinstance(block, Table) and isinstance(previous, Table):
            elements.append(E("w:p"))
        elements.append(
            _paragraph(block, ctx) if isinstance(block, Paragraph) else _table(block, ctx)
        )
        previous = block
    if previous is None or isinstance(previous, Table):
        elements.append(E("w:p"))
    return elements


def _twips(value: int) -> dict[str, str]:
    return {"w:w": str(value), "w:type": "dxa"}


def _table(table: Table, ctx: BodyContext) -> Element:
    widths = [w.in_twips for w in table.widths]
    properties: dict[str, Element] = {
        "tblW": E("w:tblW", _twips(sum(widths))),
        "tblLayout": E("w:tblLayout", {"w:type": "fixed"}),
        "tblLook": E(
            "w:tblLook",
            {
                "w:val": "04A0",
                "w:firstRow": "1",
                "w:lastRow": "0",
                "w:firstColumn": "1",
                "w:lastColumn": "0",
                "w:noHBand": "0",
                "w:noVBand": "1",
            },
        ),
    }
    if table.style:
        properties["tblStyle"] = E("w:tblStyle", {"w:val": style_id(table.style)})
    else:
        border = {"w:val": "nil"}
        properties["tblBorders"] = E(
            "w:tblBorders",
            *ordered(
                {
                    side: E(f"w:{side}", border)
                    for side in ("top", "left", "bottom", "right", "insideH", "insideV")
                },
                TBL_BORDERS,
            ),
        )
    if table.align:
        properties["jc"] = E("w:jc", {"w:val": table.align})
    grid = E("w:tblGrid", *(E("w:gridCol", {"w:w": str(w)}) for w in widths))
    layout = table_layout(table)
    rows = [_row(row, slots, widths, ctx) for row, slots in zip(table.rows, layout, strict=True)]
    return E("w:tbl", E("w:tblPr", *ordered(properties, TBL_PR)), grid, *rows)


def _row(row: Row, slots: list[Slot], widths: list[int], ctx: BodyContext) -> Element:
    properties: dict[str, Element] = {}
    if row.cant_split:
        properties["cantSplit"] = E("w:cantSplit")
    if row.height is not None:
        rule = "exact" if row.exact_height else "atLeast"
        properties["trHeight"] = E(
            "w:trHeight", {"w:val": str(row.height.in_twips), "w:hRule": rule}
        )
    if row.header:
        properties["tblHeader"] = E("w:tblHeader")
    trpr = E("w:trPr", *ordered(properties, TR_PR)) if properties else None
    return E("w:tr", trpr, *(_cell(slot, widths, ctx) for slot in slots))


def _cell(slot: Slot, widths: list[int], ctx: BodyContext) -> Element:
    cell: Cell = slot.cell
    width = sum(widths[slot.column : slot.column + cell.colspan])
    properties: dict[str, Element] = {"tcW": E("w:tcW", _twips(width))}
    if cell.colspan > 1:
        properties["gridSpan"] = E("w:gridSpan", {"w:val": str(cell.colspan)})
    if cell.rowspan > 1:
        properties["vMerge"] = (
            E("w:vMerge") if slot.continues else E("w:vMerge", {"w:val": "restart"})
        )
    if cell.shading:
        properties["shd"] = E(
            "w:shd", {"w:val": "clear", "w:color": "auto", "w:fill": cell.shading}
        )
    if cell.valign:
        properties["vAlign"] = E("w:vAlign", {"w:val": cell.valign})
    content = [E("w:p")] if slot.continues else _blocks(cell.content, ctx)
    return E("w:tc", E("w:tcPr", *ordered(properties, TC_PR)), *content)


# --- the document ----------------------------------------------------------------------------


def _section_properties(page: PageSetup) -> Element:
    size = {"w:w": str(page.width.in_twips), "w:h": str(page.height.in_twips)}
    if page.landscape:
        size["w:orient"] = "landscape"
    children = {
        "pgSz": E("w:pgSz", size),
        "pgMar": E(
            "w:pgMar",
            {
                "w:top": str(page.margin_top.in_twips),
                "w:right": str(page.margin_right.in_twips),
                "w:bottom": str(page.margin_bottom.in_twips),
                "w:left": str(page.margin_left.in_twips),
                "w:header": str(page.header_distance.in_twips),
                "w:footer": str(page.footer_distance.in_twips),
                "w:gutter": str(page.gutter.in_twips),
            },
        ),
        "cols": E("w:cols", {"w:space": "708"}),
        "docGrid": E("w:docGrid", {"w:linePitch": "360"}),
    }
    return E("w:sectPr", *ordered(children, SECT_PR))


def document_xml(document: Document, ctx: BodyContext) -> Element:
    body = _blocks(document.body, ctx) if document.body else []
    return E(
        "w:document",
        E("w:body", *body, _section_properties(document.page)),
        nsmap=nsmap("w", "r", "wp", "a", "pic"),
    )
