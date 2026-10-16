"""The main document part (word/document.xml), header/footer parts (word/headerN.xml) and
footnote/endnote parts."""

from __future__ import annotations

import re
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field

from wordgenx.ir import (
    LINE_NUMBER_RESTARTS,
    NOTE_KINDS,
    PAGE_NUMBER_FORMATS,
    SECTION_STARTS,
    VERTICAL_ALIGNMENTS,
    Block,
    BookmarkEnd,
    BookmarkStart,
    Break,
    Cell,
    Document,
    EmbeddedDocx,
    Field,
    FieldBlock,
    Image,
    Inline,
    Math,
    Note,
    NoteMark,
    Paragraph,
    RawBlock,
    RawXml,
    Row,
    Run,
    Section,
    SectionBreak,
    Settings,
    Slot,
    Table,
    table_layout,
)
from wordgenx.math.omml import omml
from wordgenx.ooxml.parts.properties import (
    cell_properties,
    paragraph_properties,
    run_properties,
    table_properties,
)
from wordgenx.ooxml.parts.settings import note_properties
from wordgenx.ooxml.schema_order import SECT_PR, TR_PR, ordered
from wordgenx.ooxml.style_names import style_id
from wordgenx.ooxml.xml import E, Element, nsmap, parse_fragment, qn
from wordgenx.props import EMPTY_RUN, EmitContext, PropertySet, Target
from wordgenx.values import Border

_SPLIT_RE = re.compile(r"(\t|\n)")
PICTURE_URI = "http://schemas.openxmlformats.org/drawingml/2006/picture"
SVG_BLIP_URI = "{96DAC541-7B7A-43D3-8B79-37D633B846F1}"
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
    image: Callable[[bytes, str], str] = lambda data, format: ""
    """Stores picture data of a format (a key of IMAGE_FORMATS, or "svg") as a media
    part; returns its relationship id."""
    drawing_id: Callable[[], int] = lambda: 1
    """A new document-wide unique id for wp:docPr."""
    header_footer: Callable[[str, tuple[Block, ...]], str] = lambda family, blocks: ""
    """Writes a header ("header") or footer ("footer") part; returns its relationship id."""
    bookmark_id: Callable[[str], int] = lambda name: 0
    """The document-wide w:id of a bookmark name (the same for its start and end)."""
    note: Callable[[Note], int] = lambda note: 0
    """Writes a footnote or endnote into its part; returns its w:id."""
    settings: Settings = field(default_factory=Settings)
    """Document settings; sections repeat the footnote and endnote numbering."""
    inherited: Callable[[Section], list[Element]] = lambda section: []
    """Header/footer references a section takes from the template (the first section, for
    the kinds it does not define)."""
    template_body: Callable[[], list[Element]] = lambda: []
    """The template's body content, rewritten for this package; it precedes the document's."""
    chunk: Callable[[EmbeddedDocx], str] = lambda chunk: ""
    """Stores an embedded .docx as an alternative format import part (of the main
    document); returns its relationship id."""


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
    if field.has_result:
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
    rel_id = ctx.image(image.data, image.format)
    svg = None
    if image.svg is not None:
        svg_blip = E("asvg:svgBlip", {"r:embed": ctx.image(image.svg, "svg")}, nsmap=nsmap("asvg"))
        svg = E("a:extLst", E("a:ext", {"uri": SVG_BLIP_URI}, svg_blip))
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
            E("a:blip", {"r:embed": rel_id}, svg),
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
    if isinstance(item, RawXml):
        return parse_fragment(item.xml)
    if isinstance(item, Math):
        return [
            omml(
                item.expr,
                display=item.display,
                number=item.number if item.numbered else None,
                bookmark=item.bookmark,
                bookmark_id=ctx.bookmark_id,
            )
        ]
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
            if _ends_with_table(previous):
                elements.append(E("w:p"))
            elements.append(_table(block, ctx))
            last_paragraph = None
        elif isinstance(block, RawBlock):
            raw = parse_fragment(block.xml)
            if raw and raw[0].tag == qn("w:tbl") and _ends_with_table(previous):
                elements.append(E("w:p"))
            elements.extend(raw)
            last_paragraph = raw[-1] if raw and raw[-1].tag == qn("w:p") else None
        elif isinstance(block, FieldBlock):
            paragraphs = _field_block(block, ctx)
            elements.extend(paragraphs)
            last_paragraph = paragraphs[-1]
        elif isinstance(block, EmbeddedDocx):
            elements.append(E("w:altChunk", {"r:id": ctx.chunk(block)}))
            last_paragraph = None
        else:
            last_paragraph = _paragraph(block, ctx)
            elements.append(last_paragraph)
        previous = block
    if _ends_with_table(previous) or (not elements and not final):
        elements.append(E("w:p"))
    return elements


def _ends_with_table(block: Block | None) -> bool:
    return isinstance(block, Table) or (isinstance(block, RawBlock) and block.ends_with_table)


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
    references += ctx.inherited(section)
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
    if section.vertical_align is not None:
        children["vAlign"] = E("w:vAlign", {"w:val": VERTICAL_ALIGNMENTS[section.vertical_align]})
    if (numbers := section.line_numbers) is not None:
        attrs = {
            "w:countBy": str(numbers.count_by),
            "w:start": str(numbers.start - 1),  # Word stores the start as an offset from 1
            "w:restart": LINE_NUMBER_RESTARTS[numbers.restart],
        }
        if numbers.distance is not None:
            attrs["w:distance"] = str(numbers.distance.in_twips)
        children["lnNumType"] = E("w:lnNumType", attrs)
    for kind in NOTE_KINDS:  # Word numbers notes by these, not by the copy in settings.xml
        if properties := note_properties(ctx.settings.notes(kind)):
            children[f"{kind}Pr"] = E(f"w:{kind}Pr", *properties)
    return E("w:sectPr", *references, *ordered(children, SECT_PR))


# --- tables -----------------------------------------------------------------------------------


def _twips(value: int) -> dict[str, str]:
    return {"w:w": str(value), "w:type": "dxa"}


def _table(table: Table, ctx: BodyContext) -> Element:
    widths = [w.in_twips for w in table.widths]
    structure: dict[str, Element] = {
        "tblW": E("w:tblW", _twips(sum(widths))),
        "tblLayout": E("w:tblLayout", {"w:type": "fixed"}),
        "tblLook": _table_look(table.look),
    }
    props = table.props
    if table.style:
        structure["tblStyle"] = E("w:tblStyle", {"w:val": style_id(table.style)})
    else:  # no style: no borders, except those the table sets itself
        props = _NO_BORDERS.merged(props)
    if table.align:
        structure["jc"] = E("w:jc", {"w:val": table.align})
    grid = E("w:tblGrid", *(E("w:gridCol", {"w:w": str(w)}) for w in widths))
    layout = table_layout(table)
    rows = [_row(row, slots, widths, ctx) for row, slots in zip(table.rows, layout, strict=True)]
    return E("w:tbl", table_properties(props, ctx.emit, structure), grid, *rows)


_NO_BORDERS = PropertySet(
    Target.TABLE,
    {
        f"border-{side}": Border("nil")
        for side in ("top", "left", "bottom", "right", "inside-h", "inside-v")
    },
)
_LOOK_BITS = {
    "header-row": ("firstRow", 0x0020),
    "last-row": ("lastRow", 0x0040),
    "first-column": ("firstColumn", 0x0080),
    "last-column": ("lastColumn", 0x0100),
}


def _table_look(look: frozenset[str]) -> Element:
    """w:tblLook: which table-style parts apply (with the legacy w:val bit mask)."""
    attrs: dict[str, str] = {}
    mask = 0
    for flag, (attr, bit) in _LOOK_BITS.items():
        attrs[f"w:{attr}"] = "1" if flag in look else "0"
        mask |= bit if flag in look else 0
    attrs["w:noHBand"] = "0" if "banded-rows" in look else "1"
    attrs["w:noVBand"] = "0" if "banded-columns" in look else "1"
    mask |= 0 if "banded-rows" in look else 0x0200
    mask |= 0 if "banded-columns" in look else 0x0400
    return E("w:tblLook", {"w:val": f"{mask:04X}", **attrs})


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
    structure: dict[str, Element] = {"tcW": E("w:tcW", _twips(width))}
    if cell.colspan > 1:
        structure["gridSpan"] = E("w:gridSpan", {"w:val": str(cell.colspan)})
    if cell.rowspan > 1:
        structure["vMerge"] = (
            E("w:vMerge") if slot.continues else E("w:vMerge", {"w:val": "restart"})
        )
    content = [E("w:p")] if slot.continues else _blocks(cell.content, ctx)
    return E("w:tc", cell_properties(cell.props, ctx.emit, structure), *content)


# --- parts -----------------------------------------------------------------------------------


def document_xml(document: Document, ctx: BodyContext, root: Element | None = None) -> Element:
    """The w:document; `root` is an empty w:document to fill (a template's, with its
    namespace declarations), else a new one."""
    body = [*ctx.template_body(), *_blocks(document.body, ctx, final=True)]
    final = _section_properties(document.section, ctx)
    if root is None:
        root = E("w:document", nsmap=nsmap(*PART_NAMESPACES))
    root.append(E("w:body", *body, final))
    return root


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
