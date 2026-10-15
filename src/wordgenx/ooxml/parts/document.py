"""The main document part (word/document.xml), header/footer parts (word/headerN.xml) and
footnote/endnote parts."""

from __future__ import annotations

import re
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field

from wordgenx.ir import (
    NOTE_KINDS,
    PAGE_NUMBER_FORMATS,
    SECTION_STARTS,
    Block,
    BookmarkEnd,
    BookmarkStart,
    Break,
    Cell,
    Document,
    Field,
    FieldBlock,
    Image,
    Inline,
    Note,
    NoteMark,
    Paragraph,
    Row,
    Run,
    Section,
    SectionBreak,
    Settings,
    Slot,
    Table,
    table_layout,
)
from wordgenx.ooxml.parts.properties import paragraph_properties, run_properties
from wordgenx.ooxml.parts.settings import note_properties
from wordgenx.ooxml.schema_order import SECT_PR, TBL_BORDERS, TBL_PR, TC_PR, TR_PR, ordered
from wordgenx.ooxml.style_names import style_id
from wordgenx.ooxml.xml import E, Element, nsmap, qn
from wordgenx.props import EMPTY_RUN, EmitContext

_SPLIT_RE = re.compile(r"(\t|\n)")
PICTURE_URI = "http://schemas.openxmlformats.org/drawingml/2006/picture"
PART_NAMESPACES = ("w", "r", "wp", "a", "pic")


@dataclass(frozen=True)
class BodyContext:
    """Everything a part's content needs from the package being built.

    Relationships (hyperlinks, images) belong to the part being written, so the main
    document and each header or footer get their own context.
    """

    emit: EmitContext
    hyperlink: Callable[[str], str]
    """Returns the relationship id for an external hyperlink target."""
    list_num_ids: Mapping[tuple[str, int], int]
    """(numbering name, list instance) -> w:numId."""
    image: Callable[[Image], str] = lambda image: ""
    """Stores the image as a media part; returns its relationship id."""
    drawing_id: Callable[[], int] = lambda: 1
    """A new document-wide unique id for wp:docPr."""
    header_footer: Callable[[str, tuple[Block, ...]], str] = lambda family, blocks: ""
    """Writes a header ("header") or footer ("footer") part; returns its relationship id."""
    bookmark_id: Callable[[str], int] = lambda name: 0
    """The document-wide w:id of a bookmark name (the same for its start and end)."""
    note: Callable[[Note], int] = lambda note: 0
    settings: Settings = field(default_factory=Settings)
    """Document settings; sections repeat the footnote and endnote numbering."""
    """Writes a footnote or endnote into its part; returns its w:id."""


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


def _field_runs(field: Field, ctx: BodyContext) -> tuple[list[Element], list[Element]]:
    """(begin + instruction + separate, end) runs of a complex field."""
    style = style_id(field.style) if field.style else None

    def run(*content: Element) -> Element:
        return E("w:r", run_properties(field.props, ctx.emit, style_id=style), *content)

    begin = {"w:fldCharType": "begin"}
    if field.dirty:
        begin["w:dirty"] = "true"
    opening = [run(E("w:fldChar", begin))]
    for part in field.instruction:
        if isinstance(part, str):
            opening.append(run(E("w:instrText", {"xml:space": "preserve"}, text=part)))
        else:
            opening.extend(_field(part, ctx))
    opening.append(run(E("w:fldChar", {"w:fldCharType": "separate"})))
    return opening, [run(E("w:fldChar", {"w:fldCharType": "end"}))]


def _field(field: Field, ctx: BodyContext) -> list[Element]:
    """A complex field: begin, instruction (with nested fields), separate, result, end."""
    opening, closing = _field_runs(field, ctx)
    result: list[Element] = []
    if field.result:
        style = style_id(field.style) if field.style else None
        props = run_properties(field.props, ctx.emit, style_id=style)
        result.append(E("w:r", props, *_run_content(field.result)))
    return opening + result + closing


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


def _inline(item: Inline, ctx: BodyContext) -> list[Element]:
    if isinstance(item, Run):
        return [_run(item, ctx)]
    if isinstance(item, Break):
        return [E("w:r", E("w:br", {"w:type": item.kind}))]
    if isinstance(item, Image):
        return [_drawing(item, ctx)]
    if isinstance(item, Field):
        return _field(item, ctx)
    if isinstance(item, BookmarkStart):
        return [
            E("w:bookmarkStart", {"w:id": str(ctx.bookmark_id(item.name)), "w:name": item.name})
        ]
    if isinstance(item, BookmarkEnd):
        return [E("w:bookmarkEnd", {"w:id": str(ctx.bookmark_id(item.name))})]
    if isinstance(item, Note):
        style = style_id(item.style) if item.style else None
        reference = E(f"w:{item.kind}Reference", {"w:id": str(ctx.note(item))})
        return [E("w:r", run_properties(item.props, ctx.emit, style_id=style), reference)]
    if isinstance(item, NoteMark):
        style = style_id(item.style) if item.style else None
        mark = E(f"w:{item.kind}Ref")
        return [E("w:r", run_properties(EMPTY_RUN, ctx.emit, style_id=style), mark)]
    target = (
        {"w:anchor": item.anchor} if item.anchor is not None else {"r:id": ctx.hyperlink(item.url)}
    )
    return [
        E(
            "w:hyperlink",
            {**target, "w:history": "1"},
            *(element for part in item.content for element in _inline(part, ctx)),
        )
    ]


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
        *(element for item in paragraph.content for element in _inline(item, ctx)),
    )


# --- blocks and sections -------------------------------------------------------------------------


def _blocks(blocks: tuple[Block, ...], ctx: BodyContext, *, final: bool = False) -> list[Element]:
    """Blocks of a body, cell, header or footer.

    A section break goes into the last paragraph of its section (an empty paragraph is
    added when the section ends with a table). Word also needs a paragraph between two
    tables (adjacent tables merge into one) and after a table that ends a part. `final`
    marks the body, whose own w:sectPr follows the blocks.
    """
    elements: list[Element] = []
    previous: Block | None = None
    last_paragraph: Element | None = None
    for block in blocks:
        if isinstance(block, SectionBreak):
            if last_paragraph is None:
                last_paragraph = _paragraph(Paragraph(), ctx)
                elements.append(last_paragraph)
            _attach_section(last_paragraph, _section_properties(block.section, ctx))
            last_paragraph = None
            previous = None
            continue
        if isinstance(block, Table):
            if isinstance(previous, Table):
                elements.append(E("w:p"))
            elements.append(_table(block, ctx))
            last_paragraph = None
        elif isinstance(block, FieldBlock):
            paragraphs = _field_block(block, ctx)
            elements.extend(paragraphs)
            last_paragraph = paragraphs[-1]
        else:
            last_paragraph = _paragraph(block, ctx)
            elements.append(last_paragraph)
        previous = block
    if isinstance(previous, Table) or (not elements and not final):
        elements.append(E("w:p"))
    return elements


def _field_block(block: FieldBlock, ctx: BodyContext) -> list[Element]:
    """Paragraphs of a field whose result spans them: the field opens in the first
    paragraph and closes in the last."""
    paragraphs = [_paragraph(p, ctx) for p in block.content or (Paragraph(),)]
    opening, closing = _field_runs(block.field, ctx)
    first = paragraphs[0]
    position = 1 if len(first) and first[0].tag == qn("w:pPr") else 0
    for offset, run in enumerate(opening):
        first.insert(position + offset, run)
    paragraphs[-1].extend(closing)
    return paragraphs


def _attach_section(paragraph: Element, section: Element) -> None:
    """Put w:sectPr at the end of the paragraph's w:pPr (after the paragraph properties proper)."""
    if len(paragraph) and paragraph[0].tag == qn("w:pPr"):
        paragraph[0].append(section)
    else:
        paragraph.insert(0, E("w:pPr", section))


def _section_properties(section: Section, ctx: BodyContext) -> Element:
    page = section.page
    references: list[Element] = []
    for family, parts in (("header", section.headers), ("footer", section.footers)):
        for kind, blocks in parts:
            rel_id = ctx.header_footer(family, blocks)
            references.append(E(f"w:{family}Reference", {"w:type": kind, "r:id": rel_id}))
    size = {"w:w": str(page.width.in_twips), "w:h": str(page.height.in_twips)}
    if page.landscape:
        size["w:orient"] = "landscape"
    children: dict[str, Element] = {
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
        "docGrid": E("w:docGrid", {"w:linePitch": "360"}),
    }
    if section.start != "next-page":
        children["type"] = E("w:type", {"w:val": SECTION_STARTS[section.start]})
    if section.page_number_format or section.page_number_start is not None:
        numbering: dict[str, str] = {}
        if section.page_number_format:
            numbering["w:fmt"] = PAGE_NUMBER_FORMATS[section.page_number_format]
        if section.page_number_start is not None:
            numbering["w:start"] = str(section.page_number_start)
        children["pgNumType"] = E("w:pgNumType", numbering)
    columns = {"w:space": str(section.column_gap.in_twips) if section.columns > 1 else "708"}
    if section.columns > 1:
        columns["w:num"] = str(section.columns)
        if section.column_separator:
            columns["w:sep"] = "1"
    children["cols"] = E("w:cols", columns)
    if section.title_page:
        children["titlePg"] = E("w:titlePg")
    for kind in NOTE_KINDS:  # Word numbers notes by these, not by the copy in settings.xml
        if properties := note_properties(ctx.settings.notes(kind)):
            children[f"{kind}Pr"] = E(f"w:{kind}Pr", *properties)
    return E("w:sectPr", *references, *ordered(children, SECT_PR))


# --- tables -----------------------------------------------------------------------------------


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
        sides = ("top", "left", "bottom", "right", "insideH", "insideV")
        properties["tblBorders"] = E(
            "w:tblBorders",
            *ordered({side: E(f"w:{side}", border) for side in sides}, TBL_BORDERS),
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


# --- parts -----------------------------------------------------------------------------------


def document_xml(document: Document, ctx: BodyContext) -> Element:
    body = _blocks(document.body, ctx, final=True)
    final = _section_properties(document.section, ctx)
    return E("w:document", E("w:body", *body, final), nsmap=nsmap(*PART_NAMESPACES))


def note_xml(note: Note, note_id: int, ctx: BodyContext) -> Element:
    """One w:footnote or w:endnote."""
    return E(f"w:{note.kind}", {"w:id": str(note_id)}, *_blocks(note.content, ctx))


SEPARATOR_IDS = {"separator": -1, "continuationSeparator": 0}
"""Word's ids for the separator notes; the document's own notes are numbered from 1."""


def notes_xml(kind: str, notes: list[Element]) -> Element:
    """The footnotes or endnotes part: Word's separator lines, then the notes."""
    separators = [
        E(
            f"w:{kind}",
            {"w:type": name, "w:id": str(note_id)},
            E(
                "w:p",
                E("w:pPr", E("w:spacing", {"w:after": "0", "w:line": "240", "w:lineRule": "auto"})),
                E("w:r", E(f"w:{name}")),
            ),
        )
        for name, note_id in SEPARATOR_IDS.items()
    ]
    return E(f"w:{kind}s", *separators, *notes, nsmap=nsmap(*PART_NAMESPACES))


def header_footer_xml(family: str, blocks: tuple[Block, ...], ctx: BodyContext) -> Element:
    """A w:hdr or w:ftr part. It must contain at least one paragraph."""
    tag = "w:hdr" if family == "header" else "w:ftr"
    return E(tag, *_blocks(blocks, ctx), nsmap=nsmap(*PART_NAMESPACES))
