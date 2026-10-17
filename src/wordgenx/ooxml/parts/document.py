"""The main document part (word/document.xml), header/footer parts (word/headerN.xml) and
footnote/endnote parts."""

from __future__ import annotations

import re
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field, replace
from decimal import Decimal

from wordgenx.ir import (
    CONTROL_LOCKS,
    H_ANCHORS,
    LINE_NUMBER_RESTARTS,
    NOTE_KINDS,
    PAGE_BORDER_PAGES,
    PAGE_NUMBER_FORMATS,
    SECTION_STARTS,
    TABLE_H_ANCHORS,
    TABLE_V_ANCHORS,
    TEXT_BOX_ALIGNMENTS,
    V_ANCHORS,
    VERTICAL_ALIGNMENTS,
    WRAP_SIDES,
    Anchor,
    Block,
    BookmarkEnd,
    BookmarkStart,
    Break,
    Cell,
    Comment,
    CommentEnd,
    CommentStart,
    ContentControl,
    Document,
    EmbeddedDocx,
    Field,
    FieldBlock,
    Image,
    Inline,
    Math,
    Note,
    NoteMark,
    PageBorders,
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
    TextBox,
    Watermark,
    iter_paragraphs,
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
from wordgenx.props.registry import border_attrs, color_attrs
from wordgenx.values import Border, Color, Length, apply_tint_shade

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
    comment: Callable[[Comment], int] = lambda comment: 0
    """Writes a comment into comments.xml; returns its w:id."""
    comment_id: Callable[[str], int] = lambda name: 0
    """The w:id of a comment written before, by name."""
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
    crop = None
    if (c := image.crop) is not None:
        sides = {"l": c.left, "t": c.top, "r": c.right, "b": c.bottom}
        crop = E("a:srcRect", {k: str(v) for k, v in sides.items() if v})
    transform = {"rot": str(image.rotation * 60000)} if image.rotation else None
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
            crop,
            E("a:stretch", E("a:fillRect")),
        ),
        E(
            "pic:spPr",
            E("a:xfrm", transform, E("a:off", {"x": "0", "y": "0"}), E("a:ext", size)),
            E("a:prstGeom", {"prst": "rect"}, E("a:avLst")),
            _picture_line(image.border, ctx),
        ),
    )
    graphic = [
        E("wp:extent", size),
        E("wp:effectExtent", {"l": "0", "t": "0", "r": "0", "b": "0"}),
    ]
    frame = [
        E("wp:docPr", {"id": str(drawing_id), "name": name}, description),
        E("wp:cNvGraphicFramePr", E("a:graphicFrameLocks", {"noChangeAspect": "1"})),
        E("a:graphic", E("a:graphicData", {"uri": PICTURE_URI}, picture)),
    ]
    if image.anchor is None:
        distances = {"distT": "0", "distB": "0", "distL": "0", "distR": "0"}
        return E("w:r", E("w:drawing", E("wp:inline", distances, *graphic, *frame)))
    return E("w:r", E("w:drawing", _anchor(image.anchor, drawing_id, graphic, frame)))


_WRAP_POLYGON = ((0, 0), (0, 21600), (21600, 21600), (21600, 0), (0, 0))
"""A tight wrap around the picture's box, in Word's 21600ths of each side."""
_BASE_Z_ORDER = 251658240
"""Word numbers the z-order (wp:anchor/@relativeHeight) of shapes from about here."""


def _anchor(
    anchor: Anchor, drawing_id: int, graphic: list[Element], frame: list[Element]
) -> Element:
    """wp:anchor: the position, the wrapping and then the picture, in Word's order."""
    top, bottom, left, right = anchor.distance
    attrs = {
        "distT": str(top.emu),
        "distB": str(bottom.emu),
        "distL": str(left.emu),
        "distR": str(right.emu),
        "simplePos": "0",
        "relativeHeight": str(_BASE_Z_ORDER + drawing_id * 1024),
        "behindDoc": "1" if anchor.wrap == "behind" else "0",
        "locked": "0",
        "layoutInCell": "1",
        "allowOverlap": "1",
    }

    def position(tag: str, at: Length | str, relative: str) -> Element:
        place = (
            E("wp:align", text=at) if isinstance(at, str) else E("wp:posOffset", text=str(at.emu))
        )
        return E(tag, {"relativeFrom": relative}, place)

    side = {"wrapText": WRAP_SIDES[anchor.wrap_side]}
    wrap = {
        "square": lambda: E("wp:wrapSquare", side),
        "tight": lambda: E(
            "wp:wrapTight",
            side,
            E(
                "wp:wrapPolygon",
                {"edited": "0"},
                E("wp:start", {"x": "0", "y": "0"}),
                *(E("wp:lineTo", {"x": str(x), "y": str(y)}) for x, y in _WRAP_POLYGON[1:]),
            ),
        ),
        "top-bottom": lambda: E("wp:wrapTopAndBottom"),
    }.get(anchor.wrap, lambda: E("wp:wrapNone"))()
    return E(
        "wp:anchor",
        attrs,
        E("wp:simplePos", {"x": "0", "y": "0"}),
        position("wp:positionH", anchor.x, H_ANCHORS[anchor.x_from]),
        position("wp:positionV", anchor.y, V_ANCHORS[anchor.y_from]),
        *graphic,
        wrap,
        *frame,
    )


_LINE_DASHES = {
    "dotted": "sysDot",
    "dashed": "dash",
    "dotDash": "dashDot",
    "dotDotDash": "sysDashDotDot",
}


def _picture_line(border: Border | None, ctx: BodyContext) -> Element | None:
    """a:ln around a picture or shape, from a border (style, width, colour); None without one."""
    if border is None or border.style == "nil":
        return None
    attrs = {"w": str(border.width.emu)}
    if border.style == "double":
        attrs["cmpd"] = "dbl"
    dash = _LINE_DASHES.get(border.style)
    return E(
        "a:ln",
        attrs,
        E("a:solidFill", _drawing_color(border.color)),
        E("a:prstDash", {"val": dash}) if dash else None,
    )


_SCHEME_COLORS = {
    "text1": "tx1",
    "background1": "bg1",
    "text2": "tx2",
    "background2": "bg2",
    "dark1": "dk1",
    "light1": "lt1",
    "dark2": "dk2",
    "light2": "lt2",
    "hyperlink": "hlink",
    "followedHyperlink": "folHlink",
}
"""Theme colour names -> DrawingML scheme colours (accent1-6 keep their names)."""


def _drawing_color(color: Color) -> Element:
    """A DrawingML colour: a theme colour stays one (lighter/darker as Word's lumMod and
    lumOff), so shapes follow theme changes like text does; auto is black."""
    if color.theme is None:
        return E("a:srgbClr", {"val": color.rgb or "000000"})
    modifiers: list[Element] = []
    if color.tint is not None:  # keep tint/255 of the colour, the rest towards white
        keep = round(color.tint * 100000 / 255)
        modifiers = [E("a:lumMod", {"val": str(keep)}), E("a:lumOff", {"val": str(100000 - keep)})]
    elif color.shade is not None:
        modifiers = [E("a:lumMod", {"val": str(round(color.shade * 100000 / 255))})]
    scheme = _SCHEME_COLORS.get(color.theme, color.theme)
    return E("a:schemeClr", {"val": scheme}, *modifiers)


WPS_URI = "http://schemas.microsoft.com/office/word/2010/wordprocessingShape"

# Word's VML shape types for a watermark: WordArt text on a path, and a picture frame.
_TEXT_PATH_FORMULAS = (
    "sum #0 0 10800", "prod #0 2 1", "sum 21600 0 @1", "sum 0 0 @2", "sum 21600 0 @3",
    "if @0 @3 0", "if @0 21600 @1", "if @0 0 @2", "if @0 @4 21600", "mid @5 @6", "mid @8 @5",
    "mid @7 @8", "mid @6 @7", "sum @6 0 @5",
)  # fmt: skip
_PICTURE_FORMULAS = (
    "if lineDrawn pixelLineWidth 0", "sum @0 1 0", "sum 0 0 @1", "prod @2 1 2",
    "prod @3 21600 pixelWidth", "prod @3 21600 pixelHeight", "sum @0 0 1", "prod @6 1 2",
    "prod @7 21600 pixelWidth", "sum @8 21600 0", "prod @7 21600 pixelHeight",
    "sum @10 21600 0",
)  # fmt: skip
_WATERMARK_POSITION = (
    "position:absolute;margin-left:0;margin-top:0;width:{width}pt;height:{height}pt;{rotation}"
    "z-index:-251657216;mso-position-horizontal:center;mso-position-horizontal-relative:margin;"
    "mso-position-vertical:center;mso-position-vertical-relative:margin"
)


def _points(length: Length) -> str:
    return f"{Decimal(length.emu) / 12700:.2f}".rstrip("0").rstrip(".")


def _watermark(mark: Watermark, ctx: BodyContext) -> Element:
    """A VML watermark shape as Word's Design > Watermark writes it (behind the text,
    centred on the margins); Word recognises its id and can remove it."""
    number = ctx.drawing_id()
    style = _WATERMARK_POSITION.format(
        width=_points(mark.width),
        height=_points(mark.height),
        rotation="rotation:315;" if mark.text is not None and mark.diagonal else "",
    )
    if mark.text is not None:
        shape_type = E(
            "v:shapetype",
            {
                "id": "_x0000_t136",
                "coordsize": "21600,21600",
                "o:spt": "136",
                "adj": "10800",
                "path": "m@7,l@8,m@5,21600l@6,21600e",
            },
            E("v:formulas", *(E("v:f", {"eqn": f}) for f in _TEXT_PATH_FORMULAS)),
            E(
                "v:path",
                {
                    "textpathok": "t",
                    "o:connecttype": "custom",
                    "o:connectlocs": "@9,0;@10,10800;@11,21600;@12,10800",
                    "o:connectangles": "270,180,90,0",
                },
            ),
            E("v:textpath", {"on": "t", "fitshape": "t"}),
            E("v:handles", E("v:h", {"position": "#0,bottomRight", "xrange": "6629,14971"})),
            E("o:lock", {"v:ext": "edit", "text": "t", "shapetype": "t"}),
        )
        color = mark.color
        if color.theme is not None:
            rgb = apply_tint_shade(ctx.emit.theme_color_rgb(color.theme), color.tint, color.shade)
        else:
            rgb = color.rgb or "C0C0C0"
        shape = E(
            "v:shape",
            {
                "id": f"PowerPlusWaterMarkObject{number}",
                "o:spid": f"_x0000_s{2048 + number}",
                "type": "#_x0000_t136",
                "style": style,
                "o:allowincell": "f",
                "fillcolor": f"#{rgb}",
                "stroked": "f",
            },
            E("v:fill", {"opacity": ".5"}) if mark.semitransparent else None,
            E(
                "v:textpath",
                {"style": f'font-family:"{mark.font}";font-size:1pt', "string": mark.text},
            ),
            E("w10:wrap", {"anchorx": "margin", "anchory": "margin"}),
        )
    else:
        assert mark.image is not None
        shape_type = E(
            "v:shapetype",
            {
                "id": "_x0000_t75",
                "coordsize": "21600,21600",
                "o:spt": "75",
                "o:preferrelative": "t",
                "path": "m@4@5l@4@11@9@11@9@5xe",
                "filled": "f",
                "stroked": "f",
            },
            E("v:stroke", {"joinstyle": "miter"}),
            E("v:formulas", *(E("v:f", {"eqn": f}) for f in _PICTURE_FORMULAS)),
            E("v:path", {"o:extrusionok": "f", "gradientshapeok": "t", "o:connecttype": "rect"}),
            E("o:lock", {"v:ext": "edit", "aspectratio": "t"}),
        )
        data = {"r:id": ctx.image(mark.image.data, mark.image.format), "o:title": ""}
        if mark.washout:  # Word's washout: brighter and lower contrast
            data |= {"gain": "19661f", "blacklevel": "22938f"}
        shape = E(
            "v:shape",
            {
                "id": f"WordPictureWatermark{number}",
                "o:spid": f"_x0000_s{2048 + number}",
                "type": "#_x0000_t75",
                "style": style,
                "o:allowincell": "f",
            },
            E("v:imagedata", data),
            E("w10:wrap", {"anchorx": "margin", "anchory": "margin"}),
        )
    pict = E("w:pict", shape_type, shape, nsmap=nsmap("v", "o", "w10"))
    return E("w:r", E("w:rPr", E("w:noProof")), pict)


def _text_box(box: TextBox, ctx: BodyContext) -> Element:
    """A wps text box, in mc:AlternateContent as Word writes it (Word 2010 and later; there
    is no VML fallback)."""
    drawing_id = ctx.drawing_id()
    top, bottom, left, right = box.padding
    height = box.height or _estimated_height(box)
    size = {"cx": str(box.width.emu), "cy": str(height.emu)}
    transform = {"rot": str(box.rotation * 60000)} if box.rotation else None
    fill = E("a:solidFill", _drawing_color(box.fill)) if box.fill is not None else E("a:noFill")
    line = _picture_line(box.border, ctx)
    if line is None:
        line = E("a:ln", E("a:noFill"))
    body = {
        "rot": "0",
        "vert": "horz",
        "wrap": "square",
        "lIns": str(left.emu),
        "tIns": str(top.emu),
        "rIns": str(right.emu),
        "bIns": str(bottom.emu),
        "anchor": TEXT_BOX_ALIGNMENTS[box.valign],
        "anchorCtr": "0",
    }
    shape = E(
        "wps:wsp",
        E("wps:cNvSpPr", {"txBox": "1"}),
        E(
            "wps:spPr",
            E("a:xfrm", transform, E("a:off", {"x": "0", "y": "0"}), E("a:ext", size)),
            E("a:prstGeom", {"prst": "rect"}, E("a:avLst")),
            fill,
            line,
        ),
        E("wps:txbx", E("w:txbxContent", *_blocks(box.content, ctx))),
        E("wps:bodyPr", body, E("a:noAutofit") if box.height else E("a:spAutoFit")),
    )
    graphic = [
        E("wp:extent", size),
        E("wp:effectExtent", {"l": "0", "t": "0", "r": "0", "b": "0"}),
    ]
    frame = [
        E("wp:docPr", {"id": str(drawing_id), "name": box.name or f"Text Box {drawing_id}"}),
        E("wp:cNvGraphicFramePr"),
        E("a:graphic", E("a:graphicData", {"uri": WPS_URI}, shape)),
    ]
    if box.anchor is None:
        distances = {"distT": "0", "distB": "0", "distL": "0", "distR": "0"}
        drawing = E("w:drawing", E("wp:inline", distances, *graphic, *frame))
    else:
        drawing = E("w:drawing", _anchor(box.anchor, drawing_id, graphic, frame))
    choice = E("mc:Choice", {"Requires": "wps"}, drawing)
    return E("w:r", E("mc:AlternateContent", choice, nsmap=nsmap("mc", "wps")))


def _estimated_height(box: TextBox) -> Length:
    """A first height for a box that grows to fit its text (Word sets the real one when it
    lays the box out): a line per paragraph, plus the padding."""
    lines = max(1, sum(1 for _ in iter_paragraphs(box.content)))
    top, bottom, _, _ = box.padding
    return Length(lines * Length.pt(16).emu + top.emu + bottom.emu)


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
    if isinstance(item, TextBox):
        return [_text_box(item, ctx)]
    if isinstance(item, Watermark):
        return [_watermark(item, ctx)]
    if isinstance(item, ContentControl):
        return [_control(item, ctx)]
    if isinstance(item, CommentStart):
        return [E("w:commentRangeStart", {"w:id": str(ctx.comment(item.comment))})]
    if isinstance(item, CommentEnd):
        comment_id = str(ctx.comment_id(item.name))
        reference = E("w:rPr", E("w:rStyle", {"w:val": style_id("annotation reference")}))
        return [
            E("w:commentRangeEnd", {"w:id": comment_id}),
            E("w:r", reference, E("w:commentReference", {"w:id": comment_id})),
        ]
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
    extra: dict[str, Element] = {}
    if (drop := paragraph.drop_cap) is not None:  # as Word writes a drop cap
        extra["framePr"] = E(
            "w:framePr",
            {
                "w:dropCap": "margin" if drop.in_margin else "drop",
                "w:lines": str(drop.lines),
                "w:wrap": "around",
                "w:vAnchor": "text",
                "w:hAnchor": "page" if drop.in_margin else "text",
            },
        )
        extra["textAlignment"] = E("w:textAlignment", {"w:val": "baseline"})
    properties = paragraph_properties(
        paragraph.props, ctx.emit, style_id=style, numbering=numbering, extra=extra
    )
    return E(
        "w:p",
        properties,
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


def _page_borders(borders: PageBorders, ctx: BodyContext) -> Element:
    """w:pgBorders. A side without a space gets Word's: 24pt from the page edge, 4pt from
    the text."""
    attrs = {"w:offsetFrom": borders.offset_from}
    if borders.pages != "all":
        attrs["w:display"] = PAGE_BORDER_PAGES[borders.pages]
    default = Length.pt(24 if borders.offset_from == "page" else 4)
    sides: list[Element] = []
    for name, border in borders.sides():
        spaced = border if border.space.emu else replace(border, space=default)
        side_attrs = border_attrs(spaced, ctx.emit)
        sides.append(E(f"w:{name}", {f"w:{k}": v for k, v in side_attrs.items()}))
    return E("w:pgBorders", attrs, *sides)


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
    widths: list[Element] = []
    if section.columns > 1:
        columns["w:num"] = str(section.columns)
        if section.column_separator:
            columns["w:sep"] = "1"
    if section.column_widths:  # each column's width, and the gap after it (not the last)
        columns["w:equalWidth"] = "0"
        last = len(section.column_widths) - 1
        for i, width in enumerate(section.column_widths):
            column = {"w:w": str(width.in_twips)}
            if i < last:
                column["w:space"] = str(section.column_gap.in_twips)
            widths.append(E("w:col", column))
    children["cols"] = E("w:cols", columns, *widths)
    if section.title_page:
        children["titlePg"] = E("w:titlePg")
    if section.text_direction == "vertical":
        children["textDirection"] = E("w:textDirection", {"w:val": "tbRl"})
    if section.rtl:
        children["bidi"] = E("w:bidi")
        if section.page.gutter.emu:
            children["rtlGutter"] = E("w:rtlGutter")
    if section.vertical_align is not None:
        children["vAlign"] = E("w:vAlign", {"w:val": VERTICAL_ALIGNMENTS[section.vertical_align]})
    if (borders := section.borders) is not None:
        children["pgBorders"] = _page_borders(borders, ctx)
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
    if table.anchor is not None:
        structure["tblpPr"] = _table_position(table.anchor)
    grid = E("w:tblGrid", *(E("w:gridCol", {"w:w": str(w)}) for w in widths))
    layout = table_layout(table)
    rows = [_row(row, slots, widths, ctx) for row, slots in zip(table.rows, layout, strict=True)]
    return E("w:tbl", table_properties(props, ctx.emit, structure), grid, *rows)


def _table_position(anchor: Anchor) -> Element:
    """w:tblpPr: a floating table's distance from the text, anchors and position."""
    top, bottom, left, right = anchor.distance
    attrs = {
        "w:leftFromText": str(left.in_twips),
        "w:rightFromText": str(right.in_twips),
        "w:topFromText": str(top.in_twips),
        "w:bottomFromText": str(bottom.in_twips),
        "w:vertAnchor": TABLE_V_ANCHORS[anchor.y_from],
        "w:horzAnchor": TABLE_H_ANCHORS[anchor.x_from],
    }
    if isinstance(anchor.x, str):
        attrs["w:tblpXSpec"] = anchor.x
    else:
        attrs["w:tblpX"] = str(anchor.x.in_twips)
    if isinstance(anchor.y, str):
        attrs["w:tblpYSpec"] = anchor.y
    else:
        attrs["w:tblpY"] = str(anchor.y.in_twips)
    return E("w:tblpPr", attrs)


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
        uses_w14 = any(
            isinstance(item, ContentControl) and item.kind == "checkbox"
            for paragraph in document.all_paragraphs()
            for item in paragraph.content
        )
        if uses_w14:
            root = E("w:document", nsmap=nsmap(*PART_NAMESPACES, "mc", "w14"))
            root.set(qn("mc:Ignorable"), "w14")
        else:
            root = E("w:document", nsmap=nsmap(*PART_NAMESPACES))
    if (color := document.settings.page_color) is not None:
        attrs = {f"w:{k}": v for k, v in color_attrs(color, ctx.emit).items()}
        root.append(E("w:background", attrs))
    root.append(E("w:body", *body, final))
    return root


def _control(control: ContentControl, ctx: BodyContext) -> Element:
    """An inline w:sdt, as Word writes each kind of content control."""
    properties: list[Element] = []
    if control.title:
        properties.append(E("w:alias", {"w:val": control.title}))
    if control.tag:
        properties.append(E("w:tag", {"w:val": control.tag}))
    properties.append(E("w:id", {"w:val": str(ctx.drawing_id())}))
    if control.lock is not None:
        properties.append(E("w:lock", {"w:val": CONTROL_LOCKS[control.lock]}))
    if control.showing_placeholder:
        properties.append(E("w:showingPlcHdr"))
    choices = [E("w:listItem", {"w:displayText": i, "w:value": i}) for i in control.items]
    match control.kind:
        case "text":
            properties.append(E("w:text"))
        case "dropdown":
            properties.append(E("w:dropDownList", *choices))
        case "combo":
            properties.append(E("w:comboBox", *choices))
        case "date":
            date: dict[str, str] = {}
            if control.date is not None:
                date["w:fullDate"] = control.date.strftime("%Y-%m-%dT00:00:00Z")
            properties.append(
                E(
                    "w:date",
                    date,
                    E("w:dateFormat", {"w:val": control.date_format}),
                    E("w:lid", {"w:val": control.language}),
                    E("w:storeMappedDataAs", {"w:val": "dateTime"}),
                    E("w:calendar", {"w:val": "gregorian"}),
                )
            )
        case "picture":
            properties.append(E("w:picture"))
        case "checkbox":  # Word 2010's checkbox, ignorable by older readers
            properties.append(
                E(
                    "w14:checkbox",
                    E("w14:checked", {"w14:val": "1" if control.checked else "0"}),
                    E("w14:checkedState", {"w14:val": "2612", "w14:font": "MS Gothic"}),
                    E("w14:uncheckedState", {"w14:val": "2610", "w14:font": "MS Gothic"}),
                )
            )
        case _:  # rich text: no type element
            pass
    content = [element for item in control.content for element in _inline(item, ctx)]
    return E("w:sdt", E("w:sdtPr", *properties), E("w:sdtContent", *content))


def comment_xml(comment: Comment, comment_id: int, ctx: BodyContext) -> Element:
    """One w:comment: the text, its first paragraph opening with Word's annotation mark."""
    attrs = {"w:id": str(comment_id), "w:author": comment.author}
    if comment.date is not None:
        attrs["w:date"] = comment.date.replace(microsecond=0, tzinfo=None).isoformat() + "Z"
    if comment.initials:
        attrs["w:initials"] = comment.initials
    blocks = _blocks(comment.content, ctx)
    mark = E(
        "w:r",
        E("w:rPr", E("w:rStyle", {"w:val": style_id("annotation reference")})),
        E("w:annotationRef"),
    )
    first = next((b for b in blocks if b.tag == qn("w:p")), None)
    if first is not None:
        properties = first.find(qn("w:pPr"))
        first.insert(1 if properties is not None else 0, mark)
    return E("w:comment", attrs, *blocks)


def comments_xml(comments: list[Element]) -> Element:
    return E("w:comments", *comments, nsmap=nsmap(*PART_NAMESPACES))


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
