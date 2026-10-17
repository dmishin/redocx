"""Read a .docx package back into the document IR: the seed of a decompiler.

The compiler turns IR into WordprocessingML; this module runs the front half of
that in reverse (design note ``docs/ideas/docx-decompiler.md``). It parses the
main part, styles, numbering, headers and footers into ``ir.Document`` for the
subset the writer emits, and reports what it could not understand.

The report is the interesting part today: pointing the reader at real documents
gives a frequency-ranked list of language features that are still missing (the
"feature-gap detector" from the same note).
"""

from __future__ import annotations

import copy
import posixpath
import re
import zipfile
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime
from decimal import Decimal
from io import BytesIO
from os import PathLike
from pathlib import Path

from lxml import etree

from wordgenx import ir
from wordgenx.ooxml.constants import NS, RT
from wordgenx.ooxml.xml import Element
from wordgenx.props import EMPTY_PARAGRAPH, EMPTY_RUN, PropertySet, Target
from wordgenx.values import (
    BORDER_STYLES,
    TAB_ALIGNMENTS,
    TAB_LEADERS,
    Border,
    Color,
    InvalidValueError,
    Length,
    LineSpacing,
    TabStop,
)

_A = "http://schemas.openxmlformats.org/drawingml/2006/main"
_WP = "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
_ASVG = "http://schemas.microsoft.com/office/drawing/2016/SVG/main"
_PIC = "http://schemas.openxmlformats.org/drawingml/2006/picture"

_PREFIX_BY_URI = {NS.W: "w", NS.R: "r", _A: "a", _WP: "wp", _PIC: "pic"}

_RUN_IMAGE_FORMATS = {
    "png": "png",
    "jpeg": "jpeg",
    "jpg": "jpeg",
    "gif": "gif",
    "bmp": "bmp",
    "emf": "emf",
    "wmf": "wmf",
}
_VML = "urn:schemas-microsoft-com:vml"
_VML_SIZE = re.compile(r"(width|height)\s*:\s*([0-9.]+)(pt|in|cm|mm|px)")

_BUILTIN_TABLE_STYLES = {"normal table"}
_LEADER_NAMES = {value: key for key, value in TAB_LEADERS.items()}
_PAGE_NUMBER_FORMATS = {value: key for key, value in ir.PAGE_NUMBER_FORMATS.items()}
_SECTION_STARTS = {value: key for key, value in ir.SECTION_STARTS.items()}
_ALIGNMENTS = {"both": "justify"}


class NotADocxError(ValueError):
    """The file is not a WordprocessingML package we can read."""


def _w(tag: str) -> str:
    return f"{{{NS.W}}}{tag}"


def _r(tag: str) -> str:
    return f"{{{NS.R}}}{tag}"


def _a(tag: str) -> str:
    return f"{{{_A}}}{tag}"


def _wp(tag: str) -> str:
    return f"{{{_WP}}}{tag}"


def _svg_blip(blip: Element, ctx: _Ctx) -> bytes | None:
    """The SVG Word 2016+ shows in place of a picture (a:blip/a:extLst asvg:svgBlip)."""
    found = blip.find(f".//{{{_ASVG}}}svgBlip")
    rel_id = found.get(_r("embed")) if found is not None else None
    if rel_id is None:
        return None
    part, external = ctx.opc.target(ctx.part, rel_id)
    return ctx.opc.read(part) if part is not None and not external else None


def _qname(tag: object) -> str:
    """A readable name for a Clark-notation tag: ``{…main}p`` -> ``w:p``."""
    text = str(tag)
    if not text.startswith("{"):
        return text
    uri, _, local = text[1:].partition("}")
    prefix = _PREFIX_BY_URI.get(uri)
    return f"{prefix}:{local}" if prefix else local


def _kebab(camel: str) -> str:
    return re.sub(r"(?<!^)([A-Z])", r"-\1", camel).lower()


def _int(value: str | None, default: int = 0) -> int:
    try:
        return int(value) if value is not None else default
    except ValueError:
        return default


def _toggle_state(element: Element) -> bool:
    value = element.get(_w("val"))
    return value not in ("false", "0", "off")


# --- the report -------------------------------------------------------------------------


@dataclass(frozen=True)
class ImportReport:
    """What the reader saw, and what it could not represent in the IR."""

    paragraphs: int = 0
    runs: int = 0
    tables: int = 0
    cells: int = 0
    images: int = 0
    hyperlinks: int = 0
    fields: int = 0
    bookmarks: int = 0
    sections: int = 0
    styles: int = 0
    unsupported: tuple[tuple[str, int], ...] = ()
    """(construct, count) pairs, most frequent first; the feature-gap list."""

    @property
    def unhandled(self) -> int:
        return sum(count for _, count in self.unsupported)

    def render(self) -> str:
        lines = [
            "structure:",
            f"  paragraphs  {self.paragraphs}",
            f"  runs        {self.runs}",
            f"  tables      {self.tables} ({self.cells} cells)",
            f"  images      {self.images}",
            f"  hyperlinks  {self.hyperlinks}",
            f"  fields      {self.fields}",
            f"  bookmarks   {self.bookmarks}",
            f"  sections    {self.sections}",
            f"  styles      {self.styles}",
        ]
        lines.append("coverage:")
        if not self.unsupported:
            lines.append("  everything the reader understood")
        else:
            lines.append(f"  {self.unhandled} construct(s) fall back to raw OOXML:")
            width = max(len(name) for name, _ in self.unsupported)
            lines.extend(f"    {name.ljust(width)}  x{count}" for name, count in self.unsupported)
        return "\n".join(lines)


@dataclass
class _Stats:
    counts: Counter[str] = field(default_factory=lambda: Counter[str]())
    unsupported: Counter[str] = field(default_factory=lambda: Counter[str]())

    def count(self, name: str) -> None:
        self.counts[name] += 1

    def note(self, name: str) -> None:
        self.unsupported[name] += 1

    def report(self) -> ImportReport:
        ranked = sorted(self.unsupported.items(), key=lambda item: (-item[1], item[0]))
        return ImportReport(
            paragraphs=self.counts["paragraph"],
            runs=self.counts["run"],
            tables=self.counts["table"],
            cells=self.counts["cell"],
            images=self.counts["image"],
            hyperlinks=self.counts["hyperlink"],
            fields=self.counts["field"],
            bookmarks=self.counts["bookmark"],
            sections=self.counts["section"],
            styles=self.counts["style"],
            unsupported=tuple(ranked),
        )


# --- the OPC package --------------------------------------------------------------------


class _Opc:
    """Read-only access to the parts and relationships of a package."""

    def __init__(self, data: bytes) -> None:
        try:
            self._zip = zipfile.ZipFile(BytesIO(data))
        except zipfile.BadZipFile as e:
            raise NotADocxError("not a ZIP package") from e
        self._names = {name.lstrip("/").lower(): name.lstrip("/") for name in self._zip.namelist()}
        self._rels: dict[str, dict[str, tuple[str, str, bool]]] = {}

    def names(self) -> list[str]:
        return list(self._names.values())

    def read(self, part: str) -> bytes | None:
        actual = self._names.get(part.lstrip("/").lower())
        return self._zip.read(actual) if actual is not None else None

    def parse(self, part: str) -> Element | None:
        data = self.read(part)
        if data is None:
            return None
        try:
            return etree.fromstring(data)
        except etree.XMLSyntaxError:
            return None

    def relationships(self, source: str | None) -> dict[str, tuple[str, str, bool]]:
        """``rId`` -> (type, target, external) for a part (None = the package)."""
        key = "" if source is None else source.lstrip("/")
        if key not in self._rels:
            self._rels[key] = self._read_rels(key)
        return self._rels[key]

    def _read_rels(self, source: str) -> dict[str, tuple[str, str, bool]]:
        name = (
            "_rels/.rels"
            if not source
            else f"{posixpath.dirname(source)}/_rels/{posixpath.basename(source)}.rels"
        )
        root = self.parse(name)
        if root is None:
            return {}
        found: dict[str, tuple[str, str, bool]] = {}
        for rel in root:
            rid = rel.get("Id")
            target = rel.get("Target")
            rtype = rel.get("Type")
            if rid and target and rtype:
                found[rid] = (rtype, target, rel.get("TargetMode") == "External")
        return found

    def target(self, source: str, rel_id: str) -> tuple[str | None, bool]:
        """Resolve a relationship of `source` to (part name or URL, external)."""
        entry = self.relationships(source).get(rel_id)
        if entry is None:
            return None, False
        _rtype, target, external = entry
        if external:
            return target, True
        base = "" if source in ("", "/") else posixpath.dirname(source.lstrip("/"))
        return posixpath.normpath(posixpath.join(base, target)), False


# --- reader context ---------------------------------------------------------------------


@dataclass(frozen=True)
class _Ctx:
    opc: _Opc
    part: str
    """The part being read; relationships (hyperlinks, images) belong to it."""
    stats: _Stats
    style_names: dict[str, str] = field(default_factory=lambda: dict[str, str]())
    """w:styleId -> display name."""
    numbering_defs: dict[int, str] = field(default_factory=lambda: dict[int, str]())
    """w:numId of a definition -> numbering name."""
    numbering_instances: dict[int, tuple[str, int, int | None]] = field(
        default_factory=lambda: dict[int, tuple[str, int, int | None]]()
    )
    """w:numId of a list instance -> (numbering name, instance, start)."""
    bookmark_names: dict[str, str] = field(default_factory=lambda: dict[str, str]())
    """w:id -> bookmark name, so an end element can name its start."""
    notes: dict[str, tuple[str, dict[str, Element]]] = field(
        default_factory=lambda: dict[str, tuple[str, dict[str, Element]]]()
    )
    """footnote | endnote -> (its part, w:id -> w:footnote or w:endnote)."""
    comments: tuple[str, dict[str, Element]] = ("", {})
    """The comments part and w:id -> w:comment."""

    def for_part(self, part: str) -> _Ctx:
        return replace(self, part=part)

    def style_name(self, style_id: str | None) -> str | None:
        if style_id is None:
            return None
        return self.style_names.get(style_id, style_id)


# --- property decoding ------------------------------------------------------------------


def _decode_color(element: Element) -> Color:
    theme = element.get(_w("themeColor"))
    if theme:
        tint = element.get(_w("themeTint"))
        shade = element.get(_w("themeShade"))
        return Color(
            theme=theme,
            tint=int(tint, 16) if tint else None,
            shade=int(shade, 16) if shade else None,
        )
    value = element.get(_w("val"))
    if value is None or value == "auto":
        return Color()
    return Color(rgb=value.upper())


def _decode_font(element: Element) -> str | None:
    theme = element.get(_w("asciiTheme")) or element.get(_w("hAnsiTheme"))
    if theme in ("majorHAnsi", "majorEastAsia", "majorBidi"):
        return "theme.major"
    if theme in ("minorHAnsi", "minorEastAsia", "minorBidi"):
        return "theme.minor"
    return element.get(_w("ascii"))


def _decode_line_spacing(element: Element) -> LineSpacing | None:
    line = element.get(_w("line"))
    if line is None:
        return None
    try:
        amount = int(line)
    except ValueError:
        return None
    rule = element.get(_w("lineRule")) or "auto"
    if rule == "auto":
        return LineSpacing.lines(Decimal(amount) / 240)
    if rule == "exact":
        return LineSpacing.exact(Length.twips(amount))
    if rule == "atLeast":
        return LineSpacing.at_least(Length.twips(amount))
    return None


_BORDER_STYLES = {"nil": "nil", "none": "nil"}


def _decode_border(element: Element) -> Border:
    """w:top, w:bdr, ...: style, width (eighths of a point), space (points), colour."""
    style = element.get(_w("val")) or "single"
    if style in _BORDER_STYLES:
        return Border("nil")
    eighths = max(2, min(96, _int(element.get(_w("sz")), 4)))
    color = element.get(_w("color"))
    try:
        return Border(
            style if style in BORDER_STYLES else "single",
            Length(eighths * 12700 // 8),
            Color(rgb=color.upper()) if color and color != "auto" else Color(),
            Length.pt(_int(element.get(_w("space")))),
        )
    except InvalidValueError:
        return Border()


def _decode_tabs(element: Element, ctx: _Ctx) -> tuple[TabStop, ...] | None:
    stops: list[TabStop] = []
    for tab in element.findall(_w("tab")):
        position = tab.get(_w("pos"))
        if position is None:
            ctx.stats.note("w:tab without a position")
            continue
        align = tab.get(_w("val")) or "left"
        align = {"start": "left", "end": "right", "num": "left"}.get(align, align)
        if align == "clear":  # removes a stop the style sets: nothing to keep
            continue
        if align not in TAB_ALIGNMENTS:
            ctx.stats.note(f"tab stop {align!r}")
            continue
        leader = _LEADER_NAMES.get(tab.get(_w("leader")) or "none", "none")
        stops.append(TabStop(Length.twips(_int(position)), align, leader))
    return tuple(stops) if stops else None


_IGNORED_RUN = frozenset(
    "noProof webHidden specVanish snapToGrid szCs rStyle bCs iCs effect".split()
)
"""Run properties that are proofing or layout bookkeeping, or set together with others."""
_IGNORED_PARAGRAPH = frozenset(
    "rPr snapToGrid autoSpaceDE autoSpaceDN adjustRightInd kinsoku overflowPunct "
    "topLinePunct wordWrap textAlignment sectPr cnfStyle framePr".split()
)
"""Paragraph properties without a meaning wordgenx keeps (rPr: the paragraph mark's)."""
_W14 = "http://schemas.microsoft.com/office/word/2010/wordml"
_IGNORED_ELEMENTS = frozenset({"lastRenderedPageBreak", "proofErr", "permStart", "permEnd"})
"""Markers in the text that Word writes for its own bookkeeping."""


_RUN_TOGGLES = {
    "b": "bold",
    "i": "italic",
    "caps": "caps",
    "smallCaps": "small-caps",
    "strike": "strike",
    "dstrike": "double-strike",
    "vanish": "hidden",
    "rtl": "rtl",
    "cs": "complex-script",
}


def _run_props(rpr: Element | None, ctx: _Ctx) -> tuple[PropertySet, str | None]:
    if rpr is None:
        return EMPTY_RUN, None
    values: dict[str, object] = {}
    style: str | None = None
    for child in rpr:
        name = _qname(child.tag)
        if child.tag in (_w("rStyle"),):
            style = ctx.style_name(child.get(_w("val")))
        elif child.tag == _w("rFonts"):
            font = _decode_font(child)
            if font:
                values["font"] = font
        elif child.tag == _w("color"):
            values["color"] = _decode_color(child)
        elif child.tag == _w("spacing"):
            raw = child.get(_w("val"))
            if raw is not None:
                values["character-spacing"] = Length.twips(_int(raw))
        elif child.tag == _w("sz"):
            values["size"] = Length(_int(child.get(_w("val"))) * 6350)
        elif child.tag == _w("highlight"):
            if value := child.get(_w("val")):
                values["highlight"] = _kebab(value)
        elif child.tag == _w("u"):
            values["underline"] = _kebab(child.get(_w("val")) or "single")
        elif child.tag == _w("vertAlign"):
            if value := child.get(_w("val")):
                values["vertical-align"] = _kebab(value)
        elif child.tag == _w("lang"):
            # the Latin language, else the complex-script or East Asian one
            for attr in ("val", "bidi", "eastAsia"):
                if value := child.get(_w(attr)):
                    values["lang"] = value
                    break
        elif child.tag == _w("kern"):
            if (kern := _int(child.get(_w("val")))) > 0:  # 0: no kerning
                values["kerning"] = Length(kern * 6350)
        elif child.tag == _w("w"):
            if (scale := _int(child.get(_w("val")), 100)) != 100:
                values["scale"] = f"{scale}%"
        elif child.tag == _w("position"):
            values["position"] = Length(_int(child.get(_w("val"))) * 6350)
        elif child.tag == _w("shd"):
            if (fill := child.get(_w("fill"))) and fill != "auto":
                values["shading"] = Color(rgb=fill.upper())
        elif child.tag == _w("bdr"):
            values["border"] = _decode_border(child)
        elif _local(child.tag) in _RUN_TOGGLES:
            values[_RUN_TOGGLES[_local(child.tag)]] = _toggle_state(child)
        elif _local(child.tag) in _IGNORED_RUN and etree.QName(child).namespace == NS.W:
            pass
        elif etree.QName(child).namespace == _W14:
            ctx.stats.note("text effect (glow, shadow, 3-D, ligatures, ...)")
        else:
            ctx.stats.note(name)
    return _property_set(Target.RUN, values, ctx, "run"), style


_PARAGRAPH_TOGGLES = {
    "keepNext": "keep-with-next",
    "keepLines": "keep-lines",
    "pageBreakBefore": "page-break-before",
    "widowControl": "widow-control",
    "contextualSpacing": "contextual-spacing",
}


def _para_props(
    ppr: Element | None, ctx: _Ctx
) -> tuple[PropertySet, str | None, tuple[int, int] | None]:
    if ppr is None:
        return EMPTY_PARAGRAPH, None, None
    values: dict[str, object] = {}
    style: str | None = None
    numbering: tuple[int, int] | None = None
    for child in ppr:
        name = _qname(child.tag)
        if child.tag == _w("pStyle"):
            style = ctx.style_name(child.get(_w("val")))
        elif child.tag == _w("spacing"):
            before = child.get(_w("before"))
            after = child.get(_w("after"))
            if before is not None:
                values["space-before"] = Length.twips(_int(before))
            if after is not None:
                values["space-after"] = Length.twips(_int(after))
            if (spacing := _decode_line_spacing(child)) is not None:
                values["line-spacing"] = spacing
        elif child.tag == _w("ind"):
            _decode_indent(child, values)
        elif child.tag == _w("tabs"):
            if (stops := _decode_tabs(child, ctx)) is not None:
                values["tabs"] = stops
        elif child.tag == _w("jc"):
            values["align"] = _ALIGNMENTS.get(
                child.get(_w("val")) or "", child.get(_w("val")) or ""
            )
        elif child.tag == _w("outlineLvl"):
            level = _int(child.get(_w("val"))) + 1
            if level <= 9:  # Word writes 9 (level 10) for "body text"
                values["outline-level"] = level
        elif child.tag == _w("pBdr"):
            for side in child:
                local = _local(side.tag)
                if local in ("top", "left", "bottom", "right", "between", "bar"):
                    values[f"border-{local}"] = _decode_border(side)
        elif child.tag == _w("shd"):
            if (fill := child.get(_w("fill"))) and fill != "auto":
                values["shading"] = Color(rgb=fill.upper())
        elif child.tag == _w("numPr"):
            numbering = _decode_numbering(child)
        elif _local(child.tag) in _IGNORED_PARAGRAPH:
            pass
        elif _local(child.tag) in _PARAGRAPH_TOGGLES:
            values[_PARAGRAPH_TOGGLES[_local(child.tag)]] = _toggle_state(child)
        else:
            ctx.stats.note(name)
    return _property_set(Target.PARAGRAPH, values, ctx, "paragraph"), style, numbering


def _decode_indent(element: Element, values: dict[str, object]) -> None:
    for attr, prop in (
        ("left", "indent-left"),
        ("right", "indent-right"),
        ("firstLine", "first-line-indent"),
        ("hanging", "hanging-indent"),
    ):
        raw = element.get(_w(attr))
        if raw is not None:
            values[prop] = Length.twips(_int(raw))


def _decode_numbering(numpr: Element) -> tuple[int, int] | None:
    num_id = numpr.find(_w("numId"))
    ilvl = numpr.find(_w("ilvl"))
    if num_id is None or num_id.get(_w("val")) is None:
        return None
    return _int(num_id.get(_w("val"))), _int(ilvl.get(_w("val")) if ilvl is not None else None)


def _property_set(target: Target, values: dict[str, object], ctx: _Ctx, where: str) -> PropertySet:
    try:
        return PropertySet(target, values)
    except ValueError:
        pass
    # Keep every value that is valid on its own; report the rest.
    valid: dict[str, object] = {}
    for key, value in values.items():
        try:
            PropertySet(target, {key: value})
        except ValueError as e:
            ctx.stats.note(f"{where} property {e}")
        else:
            valid[key] = value
    try:
        return PropertySet(target, valid)
    except ValueError as e:
        ctx.stats.note(f"{where} property {e}")
        return PropertySet(target)


def _local(tag: object) -> str:
    text = str(tag)
    return text.rpartition("}")[2] if text.startswith("{") else text


# --- runs, fields and inline content -----------------------------------------------------


def _read_vml_picture(element: Element, ctx: _Ctx) -> ir.Image | None:
    """The picture of a VML shape (w:pict) or of an embedded object's preview (w:object)."""
    shape = element.find(f"{{{_VML}}}shape")
    data_element = shape.find(f"{{{_VML}}}imagedata") if shape is not None else None
    rel_id = data_element.get(_r("id")) if data_element is not None else None
    if shape is None or rel_id is None:
        return None
    part, external = ctx.opc.target(ctx.part, rel_id)
    data = ctx.opc.read(part) if part is not None and not external else None
    if part is None or data is None:
        return None
    image_format = _RUN_IMAGE_FORMATS.get(posixpath.splitext(part)[1].lstrip(".").lower())
    sizes = {
        m[1]: Length.of(m[2], m[3] if m[3] != "px" else "pt")
        for m in _VML_SIZE.finditer(shape.get("style") or "")
    }
    if image_format is None or "width" not in sizes or "height" not in sizes:
        return None
    ctx.stats.count("image")
    return ir.Image(
        data, image_format, sizes["width"], sizes["height"], "", posixpath.basename(part)
    )


_MC = "http://schemas.openxmlformats.org/markup-compatibility/2006"
_WPS = "http://schemas.microsoft.com/office/word/2010/wordprocessingShape"
_H_FROM = {value: key for key, value in ir.H_ANCHORS.items()}
_V_FROM = {value: key for key, value in ir.V_ANCHORS.items()}
_WRAP_SIDES = {value: key for key, value in ir.WRAP_SIDES.items()}
_PRESET_COLORS = {
    "black": "000000",
    "white": "FFFFFF",
    "red": "FF0000",
    "green": "008000",
    "blue": "0000FF",
    "yellow": "FFFF00",
    "gray": "808080",
    "darkGray": "A9A9A9",
    "lightGray": "D3D3D3",
}
"""DrawingML preset colours Word uses for lines (the others read as black)."""
_FIXED_STYLES = frozenset({"default paragraph font", "normal table", "no list"})
"""Styles every document has and the writer always writes itself."""
_SCHEME_COLORS = {
    "tx1": "text1",
    "bg1": "background1",
    "tx2": "text2",
    "bg2": "background2",
    "dk1": "dark1",
    "lt1": "light1",
    "dk2": "dark2",
    "lt2": "light2",
    "hlink": "hyperlink",
    "folHlink": "followedHyperlink",
}


def _read_anchor(anchor: Element) -> ir.Anchor | None:
    """Where a floating drawing sits and how text flows around it (wp:anchor)."""

    def position(tag: str, names: dict[str, str]) -> tuple[Length | str, str]:
        element = anchor.find(_wp(tag))
        if element is None:
            return Length(0), ""
        relative = names.get(element.get("relativeFrom") or "", "")
        align = element.findtext(_wp("align"))
        if align:
            return align, relative
        return Length(_int(element.findtext(_wp("posOffset")))), relative

    x, x_from = position("positionH", _H_FROM)
    y, y_from = position("positionV", _V_FROM)
    wrap, side = "front", "both"
    for element in anchor:
        local = _local(element.tag)
        if local in ("wrapSquare", "wrapTight", "wrapThrough"):
            wrap = "square" if local == "wrapSquare" else "tight"
            side = _WRAP_SIDES.get(element.get("wrapText") or "bothSides", "both")
        elif local == "wrapTopAndBottom":
            wrap = "top-bottom"
        elif local == "wrapNone":
            wrap = "behind" if anchor.get("behindDoc") == "1" else "front"
    distance = tuple(Length(_int(anchor.get(a))) for a in ("distT", "distB", "distL", "distR"))
    try:
        return ir.Anchor(
            wrap,
            side,
            x if not isinstance(x, str) or x in ir.H_ALIGNS else Length(0),
            x_from or "column",
            y if not isinstance(y, str) or y in ir.V_ALIGNS else Length(0),
            y_from or "paragraph",
            distance,  # type: ignore[arg-type]
        )
    except ValueError:
        return None


def _drawing_color(fill: Element | None) -> Color | None:
    """The colour of an a:solidFill (an RGB or a scheme colour, lighter or darker)."""
    if fill is None:
        return None
    if (rgb := fill.find(_a("srgbClr"))) is not None:
        return Color(rgb=(rgb.get("val") or "000000").upper())
    if (preset := fill.find(_a("prstClr"))) is not None:
        return Color(rgb=_PRESET_COLORS.get(preset.get("val") or "", "000000"))
    if (scheme := fill.find(_a("schemeClr"))) is not None:
        name = scheme.get("val") or ""
        name = _SCHEME_COLORS.get(name, name)
        mod = scheme.find(_a("lumMod"))
        off = scheme.find(_a("lumOff"))
        try:
            if mod is not None and off is not None:
                return Color(theme=name, tint=round(_int(mod.get("val")) * 255 / 100000))
            if mod is not None:
                return Color(theme=name, shade=round(_int(mod.get("val")) * 255 / 100000))
            return Color(theme=name)
        except (ValueError, InvalidValueError):
            return None
    return None


def _drawing_line(properties: Element | None) -> Border | None:
    """a:ln of a picture or shape as a border; None without a visible line."""
    line = properties.find(_a("ln")) if properties is not None else None
    if line is None or line.find(_a("noFill")) is not None:
        return None
    color = _drawing_color(line.find(_a("solidFill")))
    if color is None:
        return None
    dash = line.find(_a("prstDash"))
    dashes = {"sysDot": "dotted", "dot": "dotted", "dash": "dashed", "sysDash": "dashed"}
    plain = "double" if line.get("cmpd") == "dbl" else "single"
    style = dashes.get((dash.get("val") or "") if dash is not None else "", plain)
    width = Length(max(3175, min(152400, _int(line.get("w"), 12700))))
    try:
        return Border(style, width, color)
    except InvalidValueError:
        return None


def _rotation(properties: Element | None) -> int:
    transform = properties.find(_a("xfrm")) if properties is not None else None
    return round(_int(transform.get("rot")) / 60000) if transform is not None else 0


def _frame(drawing: Element) -> tuple[Element, ir.Anchor | None] | None:
    """The wp:inline or wp:anchor of a w:drawing, and the anchor's placement."""
    if (inline := drawing.find(_wp("inline"))) is not None:
        return inline, None
    if (anchor := drawing.find(_wp("anchor"))) is not None:
        return anchor, _read_anchor(anchor)
    return None


def _extent(frame: Element) -> tuple[Length, Length] | None:
    extent = frame.find(_wp("extent"))
    if extent is None:
        return None
    width, height = Length(_int(extent.get("cx"))), Length(_int(extent.get("cy")))
    return (width, height) if width.emu > 0 and height.emu > 0 else None


def _read_drawing(drawing: Element, ctx: _Ctx) -> ir.Image | None:
    """A picture, inline or floating, with its crop, rotation and border."""
    framed = _frame(drawing)
    blip = drawing.find(f".//{_a('blip')}")
    if blip is None or framed is None:
        return None
    frame, anchor = framed
    rel_id = blip.get(_r("embed"))
    if rel_id is None:
        return None
    part, external = ctx.opc.target(ctx.part, rel_id)
    if part is None or external:
        return None
    data = ctx.opc.read(part)
    if data is None:
        return None
    extension = posixpath.splitext(part)[1].lstrip(".").lower()
    image_format = _RUN_IMAGE_FORMATS.get(extension)
    if image_format is None:
        ctx.stats.note(f"image format .{extension}")
        return None
    size = _extent(frame)
    if size is None:
        return None
    doc_pr = frame.find(_wp("docPr"))
    description = name = ""
    if doc_pr is not None:
        description = doc_pr.get("descr") or ""
        name = doc_pr.get("name") or ""
    crop = None
    if (rect := drawing.find(f".//{_a('srcRect')}")) is not None:
        sides = {
            k: max(0, _int(rect.get(a)))
            for k, a in (("left", "l"), ("top", "t"), ("right", "r"), ("bottom", "b"))
        }
        if any(sides.values()):
            try:
                crop = ir.Crop(**sides)
            except ValueError:
                crop = None
    properties = drawing.find(".//{http://schemas.openxmlformats.org/drawingml/2006/picture}spPr")
    ctx.stats.count("image")
    try:
        return ir.Image(
            data,
            image_format,
            size[0],
            size[1],
            description,
            name,
            svg=_svg_blip(blip, ctx),
            anchor=anchor,
            crop=crop,
            rotation=_rotation(properties),
            border=_drawing_line(properties),
        )
    except ValueError as e:
        ctx.stats.note(f"picture ({e})")
        return None


def _read_text_box(alternate: Element, ctx: _Ctx) -> ir.TextBox | None:
    """A Word 2010 text box: the wps shape in mc:AlternateContent's choice."""
    choice = alternate.find(f"{{{_MC}}}Choice")
    drawing = choice.find(_w("drawing")) if choice is not None else None
    shape = drawing.find(f".//{{{_WPS}}}wsp") if drawing is not None else None
    if drawing is None or shape is None:
        return None
    content = shape.find(f"{{{_WPS}}}txbx/{_w('txbxContent')}")
    framed = _frame(drawing)
    if content is None or framed is None:
        ctx.stats.note("shape (no text)")
        return None
    frame, anchor = framed
    size = _extent(frame)
    if size is None:
        return None
    properties = shape.find(f"{{{_WPS}}}spPr")
    fill = properties.find(_a("solidFill")) if properties is not None else None
    body = shape.find(f"{{{_WPS}}}bodyPr")
    padding = ir.TextBox((), size[0]).padding
    valign = "top"
    autofit = True
    if body is not None:

        def inset(attr: str, default: Length) -> Length:
            raw = body.get(attr)
            return Length(_int(raw)) if raw is not None else default

        top, bottom, left, right = padding
        padding = (
            inset("tIns", top),
            inset("bIns", bottom),
            inset("lIns", left),
            inset("rIns", right),
        )
        valign = {"ctr": "center", "b": "bottom"}.get(body.get("anchor") or "t", "top")
        autofit = body.find(_a("spAutoFit")) is not None
    doc_pr = frame.find(_wp("docPr"))
    ctx.stats.count("text box")
    try:
        return ir.TextBox(
            _read_blocks(content, ctx),
            size[0],
            None if autofit else size[1],
            _drawing_color(fill),
            _drawing_line(properties),
            padding,
            valign,
            _rotation(properties),
            anchor,
            (doc_pr.get("name") or "") if doc_pr is not None else "",
        )
    except ValueError as e:
        ctx.stats.note(f"text box ({e})")
        return None


def _read_run(run: Element, ctx: _Ctx) -> list[ir.Inline]:
    rpr = run.find(_w("rPr"))
    props, style = _run_props(rpr, ctx)
    items: list[ir.Inline] = []
    buffer = ""

    def flush() -> None:
        nonlocal buffer
        if buffer:
            items.append(ir.Run(buffer, props, style))
            ctx.stats.count("run")
            buffer = ""

    for child in run:
        tag = child.tag
        if tag == _w("t"):
            buffer += child.text or ""
        elif tag == _w("tab"):
            buffer += "\t"
        elif tag == _w("br"):
            kind = child.get(_w("type"))
            if kind in ("page", "column"):
                flush()
                items.append(ir.Break(kind))
            else:
                buffer += "\n"
        elif tag == _w("cr"):
            buffer += "\n"
        elif tag == _w("noBreakHyphen"):
            buffer += "\u2011"
        elif tag == _w("softHyphen"):
            buffer += "\u00ad"
        elif tag == _w("sym"):
            char = child.get(_w("char"))
            try:
                buffer += chr(int(char or "", 16))
            except ValueError:
                ctx.stats.note("w:sym without a hex w:char")
        elif tag == f"{{{_MC}}}AlternateContent":
            flush()
            if (box := _read_text_box(child, ctx)) is not None:
                items.append(box)
        elif tag in (_w("object"), _w("pict")):
            flush()
            if (image := _read_vml_picture(child, ctx)) is not None:
                items.append(image)
                if tag == _w("object"):
                    ctx.stats.note("embedded object (kept as its picture)")
            else:
                ctx.stats.note(_qname(tag))
        elif tag == _w("drawing"):
            flush()
            if (image := _read_drawing(child, ctx)) is not None:
                items.append(image)
            else:
                ctx.stats.note("w:drawing")
        elif tag in (_w("footnoteReference"), _w("endnoteReference")):
            flush()
            kind = "footnote" if tag == _w("footnoteReference") else "endnote"
            if (note := _read_note(kind, child.get(_w("id")), props, style, ctx)) is not None:
                items.append(note)
        elif tag in (_w("footnoteRef"), _w("endnoteRef")):
            flush()
            items.append(ir.NoteMark("footnote" if tag == _w("footnoteRef") else "endnote", style))
        elif tag in (
            _w("rPr"),
            _w("fldChar"),
            _w("instrText"),
            _w("lastRenderedPageBreak"),
        ) or tag in (
            _w("separator"),
            _w("continuationSeparator"),
            _w("commentReference"),
            _w("annotationRef"),
        ):
            pass  # the writer puts a comment's reference mark after its range
        else:
            ctx.stats.note(_qname(tag))
    flush()
    return items


def _read_comment(ident: str, ctx: _Ctx) -> ir.Comment | None:
    """A comment: its author, initials, date and text, from the comments part."""
    part, comments = ctx.comments
    element = comments.get(ident)
    if element is None:
        ctx.stats.note("comment without its text")
        return None
    date = None
    if raw := element.get(_w("date")):
        try:
            date = datetime.fromisoformat(raw.removesuffix("Z"))
        except ValueError:
            date = None
    ctx.stats.count("comment")
    return ir.Comment(
        f"_Comment{ident}",
        _read_blocks(element, ctx.for_part(part)),
        element.get(_w("author")) or "",
        element.get(_w("initials")) or "",
        date,
    )


def _read_note(
    kind: str, ident: str | None, props: PropertySet, style: str | None, ctx: _Ctx
) -> ir.Note | None:
    """A footnote or endnote: its text, read from its own part."""
    part, notes = ctx.notes.get(kind, ("", {}))
    element = notes.get(ident or "")
    if element is None:
        ctx.stats.note(f"{kind} {ident} not found")
        return None
    ctx.stats.count(kind)
    return ir.Note(kind, _read_blocks(element, ctx.for_part(part)), props, style)


def _read_hyperlink(link: Element, ctx: _Ctx) -> ir.Hyperlink:
    rel_id = link.get(_r("id"))
    anchor = link.get(_w("anchor"))
    url = ""
    if rel_id is not None:
        target, external = ctx.opc.target(ctx.part, rel_id)
        if target is not None and external:
            url = target
    content: list[ir.Run | ir.Field] = []
    for item in _read_inline(link, ctx):
        if isinstance(item, ir.Run | ir.Field):
            content.append(item)
        else:
            ctx.stats.note(f"{type(item).__name__} inside w:hyperlink")
    ctx.stats.count("hyperlink")
    return ir.Hyperlink(url, tuple(content), anchor)


def _read_simple_field(field: Element, ctx: _Ctx) -> ir.Field:
    instruction = field.get(_w("instr")) or ""
    result = "".join(
        text
        for run in field.findall(_w("r"))
        for node in run.iter(_w("t"))
        for text in (node.text or "",)
    )
    rpr = field.find(_w("rPr"))
    props, style = _run_props(rpr, ctx)
    ctx.stats.count("field")
    return ir.Field((instruction.strip(),) if instruction.strip() else (), result, props, style)


def _read_field(children: Sequence[Element], start: int, ctx: _Ctx) -> tuple[ir.Field, int]:
    """A complex field between begin and end; returns it and the next index."""
    instruction: list[str] = []
    result: list[str] = []
    props, style, dirty = EMPTY_RUN, None, False
    state = "before"
    depth = 0
    index = start
    while index < len(children):
        element = children[index]
        index += 1
        if element.tag != _w("r"):
            continue
        run_props, run_style = _run_props(element.find(_w("rPr")), ctx)
        for child in element:
            if child.tag == _w("fldChar"):
                kind = child.get(_w("fldCharType"))
                if kind == "begin":
                    if depth == 0:
                        props, style = run_props, run_style
                        dirty = child.get(_w("dirty")) == "true"
                        state = "instruction"
                    depth += 1
                elif kind == "separate":
                    if depth == 1:
                        state = "result"
                elif kind == "end":
                    depth -= 1
                    if depth == 0:
                        ctx.stats.count("field")
                        return _make_field(instruction, result, props, style, dirty), index
            elif child.tag == _w("instrText") and state == "instruction":
                instruction.append(child.text or "")
            elif child.tag == _w("t") and state == "result":
                result.append(child.text or "")
            elif child.tag == _w("tab") and state == "result":
                result.append("\t")
            elif child.tag == _w("br") and state == "result":
                result.append("\n")
    ctx.stats.note("w:fldChar without an end")
    ctx.stats.count("field")
    return _make_field(instruction, result, props, style, dirty), index


def _make_field(
    instruction: list[str],
    result: list[str],
    props: PropertySet,
    style: str | None,
    dirty: bool,
) -> ir.Field:
    code = "".join(instruction).strip()
    return ir.Field((code,) if code else (), "".join(result), props, style, dirty)


def _read_inline(container: Element, ctx: _Ctx) -> tuple[ir.Inline, ...]:
    children = [child for child in container if child.tag != _w("pPr")]
    items: list[ir.Inline] = []
    index = 0
    while index < len(children):
        element = children[index]
        # A field that began in an earlier paragraph leaves a stray separate/end
        # here; its runs are read as plain text.
        begin = element.find(_w("fldChar")) if element.tag == _w("r") else None
        if begin is not None and begin.get(_w("fldCharType")) == "begin":
            field, index = _read_field(children, index, ctx)
            items.append(field)
            continue
        items.extend(_read_inline_element(element, ctx))
        index += 1
    return tuple(items)


def _read_inline_element(element: Element, ctx: _Ctx) -> list[ir.Inline]:
    tag = element.tag
    if tag == _w("r"):
        return list(_read_run(element, ctx))
    if tag == _w("hyperlink"):
        return [_read_hyperlink(element, ctx)]
    if tag == _w("fldSimple"):
        return [_read_simple_field(element, ctx)]
    if tag == _w("bookmarkStart"):
        ident = element.get(_w("id"))
        name = element.get(_w("name")) or ""
        if ident is not None:
            ctx.bookmark_names[ident] = name
        ctx.stats.count("bookmark")
        return [ir.BookmarkStart(name)]
    if tag == _w("bookmarkEnd"):
        ident = element.get(_w("id"))
        name = ctx.bookmark_names.get(ident) if ident is not None else None
        return [ir.BookmarkEnd(name)] if name is not None else []
    if _local(tag) in _IGNORED_ELEMENTS:
        return []
    if tag == _w("commentRangeStart"):
        comment = _read_comment(element.get(_w("id")) or "", ctx)
        return [ir.CommentStart(comment)] if comment is not None else []
    if tag == _w("commentRangeEnd"):
        ident = element.get(_w("id")) or ""
        return [ir.CommentEnd(f"_Comment{ident}")] if ident in ctx.comments[1] else []
    if tag in (_w("ins"), _w("smartTag"), _w("dir"), _w("bdo")):
        return list(_read_inline(element, ctx))
    if tag == _w("sdt"):
        content = element.find(_w("sdtContent"))
        return list(_read_inline(content, ctx)) if content is not None else []
    if tag == _w("del"):
        ctx.stats.note("w:del (tracked deletion)")
        return []
    ctx.stats.note(_qname(tag))
    return []


# --- paragraphs, tables and blocks --------------------------------------------------------


def _read_paragraph(element: Element, ctx: _Ctx) -> tuple[ir.Paragraph, ir.Section | None]:
    ppr = element.find(_w("pPr"))
    props, style, numbering = _para_props(ppr, ctx)
    section = None
    if ppr is not None and (sect_pr := ppr.find(_w("sectPr"))) is not None:
        section = _read_section(sect_pr, ctx)
    content = _read_inline(element, ctx)

    unnumbered = False
    list_item = None
    if numbering is not None:
        num_id, level = numbering
        if num_id == 0:
            unnumbered = True
        elif (info := ctx.numbering_instances.get(num_id)) is not None:
            name, instance, start = info
            list_item = ir.ListItemRef(name, instance, level + 1, start)
        elif (name := ctx.numbering_defs.get(num_id)) is not None:
            list_item = ir.ListItemRef(name, 1, level + 1)
        else:
            ctx.stats.note(f"list numId {num_id}")

    drop_cap = None
    frame = ppr.find(_w("framePr")) if ppr is not None else None
    if frame is not None:
        if (kind := frame.get(_w("dropCap"))) in ("drop", "margin"):
            lines = max(2, min(10, _int(frame.get(_w("lines")), 3)))
            drop_cap = ir.DropCap(lines, kind == "margin")
        else:
            ctx.stats.note("text frame")
    ctx.stats.count("paragraph")
    return ir.Paragraph(content, style, props, list_item, unnumbered, drop_cap), section


@dataclass
class _CellBuilder:
    content: tuple[ir.Block, ...]
    colspan: int
    rowspan: int
    shading: str | None
    valign: str | None
    row: int

    def build(self) -> ir.Cell:
        values: dict[str, object] = {}
        if self.shading:
            values["shading"] = "#" + self.shading
        if self.valign:
            values["valign"] = self.valign
        return ir.Cell(self.content, self.colspan, self.rowspan, PropertySet(Target.CELL, values))


def _read_table(element: Element, ctx: _Ctx) -> ir.Table | None:
    grid = element.find(_w("tblGrid"))
    widths = (
        [Length.twips(_int(col.get(_w("w")))) for col in grid.findall(_w("gridCol"))]
        if grid is not None
        else []
    )
    if not widths or any(w.emu <= 0 for w in widths):
        ctx.stats.note("table without a usable grid")
        return None

    tbl_pr = element.find(_w("tblPr"))
    style: str | None = None
    align: str | None = None
    if tbl_pr is not None:
        if (tbl_style := tbl_pr.find(_w("tblStyle"))) is not None:
            style = ctx.style_name(tbl_style.get(_w("val")))
        if (jc := tbl_pr.find(_w("jc"))) is not None:
            align = {"start": "left", "end": "right"}.get(
                jc.get(_w("val")) or "", jc.get(_w("val"))
            )
    values = _table_values(tbl_pr)
    if (
        style is None
        and all(isinstance(v, Border) and v.style == "nil" for v in values.values())
        and len(values) == len(_NO_TABLE_BORDERS)
    ):
        values = {}  # a table without a style and without borders: what "no style" means
    table_props = _property_set(Target.TABLE, values, ctx, "table")
    look = _table_look(tbl_pr)

    rows_elements = element.findall(_w("tr"))
    row_cells: list[list[_CellBuilder]] = [[] for _ in rows_elements]
    occupancy: dict[tuple[int, int], _CellBuilder] = {}
    for r, tr in enumerate(rows_elements):
        column = 0
        for tc in tr.findall(_w("tc")):
            tc_pr = tc.find(_w("tcPr"))
            colspan = _cell_colspan(tc_pr)
            vmerge = tc_pr.find(_w("vMerge")) if tc_pr is not None else None
            if vmerge is not None and vmerge.get(_w("val")) != "restart":
                origin = occupancy.get((r - 1, column))
                if origin is not None:
                    origin.rowspan = max(origin.rowspan, r - origin.row + 1)
                    for c in range(column, column + colspan):
                        occupancy[(r, c)] = origin
                    column += colspan
                    continue
            builder = _CellBuilder(
                content=_read_blocks(tc, ctx),
                colspan=colspan,
                rowspan=1,
                shading=_cell_shading(tc_pr),
                valign=_cell_valign(tc_pr),
                row=r,
            )
            for c in range(column, column + colspan):
                occupancy[(r, c)] = builder
            row_cells[r].append(builder)
            column += colspan
            ctx.stats.count("cell")

    rows = [_build_row(tr, row_cells[r]) for r, tr in enumerate(rows_elements)]
    try:
        table = ir.Table(
            tuple(rows),
            tuple(widths),
            style,
            align if align in ("left", "center", "right") else None,
            table_props,
            look if look is not None else ir.DEFAULT_TABLE_LOOK,
        )
    except ValueError as e:
        ctx.stats.note(f"unrepresentable table ({e})")
        return None
    ctx.stats.count("table")
    return table


def _cell_colspan(tc_pr: Element | None) -> int:
    if tc_pr is None or (span := tc_pr.find(_w("gridSpan"))) is None:
        return 1
    return max(1, _int(span.get(_w("val")), 1))


_TABLE_SIDES = {
    "top": "top",
    "left": "left",
    "start": "left",
    "bottom": "bottom",
    "right": "right",
    "end": "right",
    "insideH": "inside-h",
    "insideV": "inside-v",
}
_NO_TABLE_BORDERS = ("top", "left", "bottom", "right", "inside-h", "inside-v")
_LOOK_ATTRIBUTES = {
    "firstRow": "header-row",
    "lastRow": "last-row",
    "firstColumn": "first-column",
    "lastColumn": "last-column",
}


def _table_values(tbl_pr: Element | None) -> dict[str, object]:
    """Borders and cell margins of a table or table style (w:tblPr)."""
    values: dict[str, object] = {}
    if tbl_pr is None:
        return values
    if (borders := tbl_pr.find(_w("tblBorders"))) is not None:
        for side in borders:
            if (name := _TABLE_SIDES.get(_local(side.tag))) is not None:
                values[f"border-{name}"] = _decode_border(side)
    if (margins := tbl_pr.find(_w("tblCellMar"))) is not None:
        for side in margins:
            name = _TABLE_SIDES.get(_local(side.tag))
            if name in ("top", "left", "bottom", "right") and side.get(_w("type")) in (None, "dxa"):
                values[f"cell-margin-{name}"] = Length.twips(_int(side.get(_w("w"))))
    return values


def _cell_values(tc_pr: Element | None) -> dict[str, object]:
    """Shading, borders and vertical alignment of a cell or a table style's cells."""
    values: dict[str, object] = {}
    if tc_pr is None:
        return values
    if (fill := _cell_shading(tc_pr)) is not None:
        values["shading"] = Color(rgb=fill.upper())
    if (valign := _cell_valign(tc_pr)) is not None:
        values["valign"] = valign
    if (borders := tc_pr.find(_w("tcBorders"))) is not None:
        for side in borders:
            if (name := _TABLE_SIDES.get(_local(side.tag))) is not None:
                values[f"border-{name}"] = _decode_border(side)
    return values


def _table_look(tbl_pr: Element | None) -> frozenset[str] | None:
    """Which table-style parts apply (w:tblLook attributes, else its hex mask)."""
    look = tbl_pr.find(_w("tblLook")) if tbl_pr is not None else None
    if look is None:
        return None
    flags: set[str] = set()
    if any(look.get(_w(attr)) is not None for attr in _LOOK_ATTRIBUTES):
        flags = {flag for attr, flag in _LOOK_ATTRIBUTES.items() if look.get(_w(attr)) == "1"}
        if look.get(_w("noHBand")) != "1":
            flags.add("banded-rows")
        if look.get(_w("noVBand")) != "1":
            flags.add("banded-columns")
        return frozenset(flags)
    mask = int(look.get(_w("val")) or "0", 16)
    for flag, bit in (
        ("header-row", 0x20),
        ("last-row", 0x40),
        ("first-column", 0x80),
        ("last-column", 0x100),
    ):
        if mask & bit:
            flags.add(flag)
    if not mask & 0x200:
        flags.add("banded-rows")
    if not mask & 0x400:
        flags.add("banded-columns")
    return frozenset(flags)


def _base(element: Element, ctx: _Ctx) -> str | None:
    """A style's base; the fixed default styles are implicit bases, so they are left out."""
    base = ctx.style_name(_attr(element, "basedOn", "val"))
    return None if base is None or base.lower() in _FIXED_STYLES else base


def _read_table_style(element: Element, name: str, ctx: _Ctx) -> ir.Style:
    """A table style: the whole table, every cell, and its conditional parts."""
    paragraph, _style, _numbering = _para_props(element.find(_w("pPr")), ctx)
    run, _ = _run_props(element.find(_w("rPr")), ctx)
    table = _property_set(Target.TABLE, _table_values(element.find(_w("tblPr"))), ctx, "table")
    cell = _property_set(Target.CELL, _cell_values(element.find(_w("tcPr"))), ctx, "cell")
    conditions: list[ir.TableCondition] = []
    repeat = False
    for part in element.findall(_w("tblStylePr")):
        kind = part.get(_w("type"))
        if kind not in ir.TABLE_CONDITIONS or any(c.kind == kind for c in conditions):
            continue
        part_paragraph, _s, _n = _para_props(part.find(_w("pPr")), ctx)
        part_run, _ = _run_props(part.find(_w("rPr")), ctx)
        part_cell = _property_set(Target.CELL, _cell_values(part.find(_w("tcPr"))), ctx, "cell")
        conditions.append(ir.TableCondition(kind, part_paragraph, part_run, part_cell))
        if kind == "firstRow" and part.find(f"{_w('trPr')}/{_w('tblHeader')}") is not None:
            repeat = True
    order = {kind: i for i, kind in enumerate(ir.TABLE_CONDITIONS)}
    conditions.sort(key=lambda c: order[c.kind])
    return ir.Style(
        kind=ir.StyleKind.TABLE,
        name=name,
        based_on=_base(element, ctx),
        paragraph=paragraph,
        run=run,
        quick_format=element.find(_w("qFormat")) is not None,
        ui_priority=_optional_int(_attr(element, "uiPriority", "val")),
        table=table,
        cell=cell,
        conditions=tuple(conditions),
        repeat_header=repeat,
    )


def _cell_shading(tc_pr: Element | None) -> str | None:
    if tc_pr is None or (shd := tc_pr.find(_w("shd"))) is None:
        return None
    fill = shd.get(_w("fill"))
    return fill if fill and re.fullmatch(r"[0-9A-Fa-f]{6}", fill) else None


def _cell_valign(tc_pr: Element | None) -> str | None:
    if tc_pr is None or (valign := tc_pr.find(_w("vAlign"))) is None:
        return None
    value = valign.get(_w("val"))
    return value if value in ("top", "center", "bottom") else None


def _build_row(tr: Element, builders: list[_CellBuilder]) -> ir.Row:
    tr_pr = tr.find(_w("trPr"))
    header = cant_split = False
    height: Length | None = None
    exact = False
    if tr_pr is not None:
        header = tr_pr.find(_w("tblHeader")) is not None
        cant_split = tr_pr.find(_w("cantSplit")) is not None
        if (tr_height := tr_pr.find(_w("trHeight"))) is not None:
            height = Length.twips(_int(tr_height.get(_w("val"))))
            exact = tr_height.get(_w("hRule")) == "exact"
    return ir.Row(tuple(b.build() for b in builders), header, height, exact, cant_split)


def _field_depth(paragraph: Element) -> int:
    """How many fields a paragraph leaves open (begins minus ends)."""
    depth = 0
    for char in paragraph.iter(_w("fldChar")):
        kind = char.get(_w("fldCharType"))
        depth += 1 if kind == "begin" else -1 if kind == "end" else 0
    return depth


def _read_field_block(paragraphs: list[Element], ctx: _Ctx) -> ir.FieldBlock:
    """A field whose result is whole paragraphs (a table of contents, an index): its
    instruction, and its result read as paragraphs without the outer field's marks."""
    copies = [copy.deepcopy(p) for p in paragraphs]
    instruction: list[str] = []
    dirty = False
    depth = 0
    state = "result"
    for paragraph in copies:
        for run in list(paragraph.iter(_w("r"))):
            outer = False
            for child in run:
                if child.tag == _w("fldChar"):
                    kind = child.get(_w("fldCharType"))
                    if kind == "begin":
                        depth += 1
                        if depth == 1:
                            outer, state = True, "instruction"
                            dirty = child.get(_w("dirty")) == "true"
                    elif kind == "separate" and depth == 1:
                        outer, state = True, "result"
                    elif kind == "end":
                        if depth == 1:
                            outer = True
                        depth -= 1
                elif child.tag == _w("instrText") and depth == 1 and state == "instruction":
                    instruction.append(child.text or "")
                    outer = True
            if outer or (depth == 1 and state == "instruction"):
                parent = run.getparent()
                if parent is not None:
                    parent.remove(run)
    content = tuple(_read_paragraph(p, ctx)[0] for p in copies)
    code = "".join(instruction).strip()
    ctx.stats.count("field")
    return ir.FieldBlock(ir.Field((code,) if code else (), dirty=dirty), content)


def _read_blocks(container: Element, ctx: _Ctx) -> tuple[ir.Block, ...]:
    blocks: list[ir.Block] = []
    children = list(container)
    index = 0
    while index < len(children):
        child = children[index]
        index += 1
        tag = child.tag
        if tag == _w("p") and (depth := _field_depth(child)) > 0:
            # A field that ends in a later paragraph: gather the paragraphs it spans.
            span = [child]
            while depth > 0 and index < len(children) and children[index].tag == _w("p"):
                depth += _field_depth(children[index])
                span.append(children[index])
                index += 1
            if depth == 0:
                blocks.append(_read_field_block(span, ctx))
                for paragraph in span:
                    sect = paragraph.find(f"{_w('pPr')}/{_w('sectPr')}")
                    if sect is not None:
                        blocks.append(ir.SectionBreak(_read_section(sect, ctx)))
                continue
            index -= len(span) - 1  # never closed: read the paragraphs one by one
        if tag == _w("p"):
            paragraph, section = _read_paragraph(child, ctx)
            blocks.append(paragraph)
            if section is not None:
                blocks.append(ir.SectionBreak(section))
        elif tag == _w("tbl"):
            if (table := _read_table(child, ctx)) is not None:
                blocks.append(table)
        elif tag in (_w("sectPr"), _w("tblPr"), _w("trPr"), _w("tcPr"), _w("pPr")):
            continue
        elif tag == _w("sdt"):
            content = child.find(_w("sdtContent"))
            if content is not None:
                blocks.extend(_read_blocks(content, ctx))
        else:
            ctx.stats.note(_qname(tag))
    return tuple(blocks)


# --- sections, headers and footers --------------------------------------------------------


def _read_section(sect_pr: Element, ctx: _Ctx) -> ir.Section:
    page = _read_page_setup(sect_pr)
    start = "next-page"
    if (type_el := sect_pr.find(_w("type"))) is not None:
        start = _SECTION_STARTS.get(type_el.get(_w("val")) or "nextPage", "next-page")
    fmt: str | None = None
    start_number: int | None = None
    if (pg_num := sect_pr.find(_w("pgNumType"))) is not None:
        if (raw := pg_num.get(_w("fmt"))) is not None:
            fmt = _PAGE_NUMBER_FORMATS.get(raw)
        if (raw := pg_num.get(_w("start"))) is not None:
            start_number = _int(raw)
    columns, gap, separator = _read_columns(sect_pr)
    widths: tuple[Length, ...] = ()
    if (cols := sect_pr.find(_w("cols"))) is not None and cols.get(_w("equalWidth")) == "0":
        widths = tuple(Length.twips(_int(c.get(_w("w")))) for c in cols.findall(_w("col")))
        if len(widths) != columns or not all(w.emu > 0 for w in widths):
            widths = ()
    borders = None
    if (page_borders := sect_pr.find(_w("pgBorders"))) is not None:
        sides = {
            _local(side.tag): _decode_border(side)
            for side in page_borders
            if _local(side.tag) in ir.PAGE_BORDER_SIDES
        }
        sides = {
            k: b for k, b in sides.items() if b.style != "nil" and b.space.emu <= Length.pt(31).emu
        }
        if sides:
            pages = {"firstPage": "first", "notFirstPage": "not-first"}.get(
                page_borders.get(_w("display")) or "", "all"
            )
            offset = "text" if page_borders.get(_w("offsetFrom")) == "text" else "page"
            borders = ir.PageBorders(**sides, offset_from=offset, pages=pages)
    valign = None
    if (v_align := sect_pr.find(_w("vAlign"))) is not None:
        valign = {"both": "justify"}.get(v_align.get(_w("val")) or "", v_align.get(_w("val")))
        if valign not in ir.VERTICAL_ALIGNMENTS:
            valign = None
    direction = sect_pr.find(_w("textDirection"))
    vertical = direction is not None and direction.get(_w("val")) in ("tbRl", "tbRlV")
    for child in sect_pr:
        if _local(child.tag) not in _KNOWN_SECTION:
            ctx.stats.note(f"section: {_qname(child.tag)}")
    headers = _read_references(sect_pr, "headerReference", ctx)
    footers = _read_references(sect_pr, "footerReference", ctx)
    ctx.stats.count("section")
    return ir.Section(
        page=page,
        start=start,
        headers=headers,
        footers=footers,
        title_page=sect_pr.find(_w("titlePg")) is not None,
        page_number_format=fmt,
        page_number_start=start_number,
        columns=columns,
        column_gap=gap,
        column_separator=separator,
        column_widths=widths,
        borders=borders,
        vertical_align=valign,
        rtl=sect_pr.find(_w("bidi")) is not None,
        text_direction="vertical" if vertical else "horizontal",
    )


_KNOWN_SECTION = frozenset(
    "headerReference footerReference footnotePr endnotePr type pgSz pgMar paperSrc pgBorders "
    "lnNumType pgNumType cols formProt vAlign noEndnote titlePg textDirection bidi rtlGutter "
    "docGrid printerSettings".split()
)
"""What a section reads, or knowingly leaves out (paper trays, the document grid)."""


def _read_page_setup(sect_pr: Element) -> ir.PageSetup:
    defaults = ir.PageSetup()
    size = sect_pr.find(_w("pgSz"))
    width = length = None
    if size is not None:
        width = Length.twips(_int(size.get(_w("w")))) if size.get(_w("w")) else None
        length = Length.twips(_int(size.get(_w("h")))) if size.get(_w("h")) else None
    margins = sect_pr.find(_w("pgMar"))
    values: dict[str, Length] = {}
    if margins is not None:
        for attr, field_name in (
            ("top", "margin_top"),
            ("bottom", "margin_bottom"),
            ("left", "margin_left"),
            ("right", "margin_right"),
            ("header", "header_distance"),
            ("footer", "footer_distance"),
            ("gutter", "gutter"),
        ):
            if (raw := margins.get(_w(attr))) is not None:
                values[field_name] = Length.twips(_int(raw))
    return replace(
        defaults,
        width=width or defaults.width,
        height=length or defaults.height,
        **values,
    )


def _read_columns(sect_pr: Element) -> tuple[int, Length, bool]:
    default_gap = ir.Section().column_gap
    cols = sect_pr.find(_w("cols"))
    if cols is None:
        return 1, default_gap, False
    count = _int(cols.get(_w("num")), 1)
    gap = Length.twips(_int(cols.get(_w("space")), 708)) if count > 1 else default_gap
    return count, gap, cols.get(_w("sep")) == "1"


def _read_references(sect_pr: Element, tag: str, ctx: _Ctx) -> ir.HeaderFooterParts:
    parts: list[tuple[str, tuple[ir.Block, ...]]] = []
    for reference in sect_pr.findall(_w(tag)):
        kind = reference.get(_w("type")) or "default"
        rel_id = reference.get(_r("id"))
        if rel_id is None:
            continue
        part, external = ctx.opc.target(ctx.part, rel_id)
        if part is None or external:
            continue
        root = ctx.opc.parse(part)
        if root is None:
            continue
        parts.append((kind, _read_blocks(root, ctx.for_part(part))))
    return tuple(parts)


# --- styles, numbering, metadata ----------------------------------------------------------


def _style_kind(value: str | None) -> ir.StyleKind | None:
    return {
        "paragraph": ir.StyleKind.PARAGRAPH,
        "character": ir.StyleKind.CHARACTER,
        "table": ir.StyleKind.TABLE,
    }.get(value or "")


def _read_styles(root: Element, ctx: _Ctx) -> tuple[ir.Style, ...]:
    styles: list[ir.Style] = []
    for element in root.findall(_w("style")):
        kind = _style_kind(element.get(_w("type")))
        if kind is None:
            continue
        name_element = element.find(_w("name"))
        name = name_element.get(_w("val")) if name_element is not None else None
        if not name:
            continue
        if name.lower() in _FIXED_STYLES:
            continue
        if kind is ir.StyleKind.TABLE:
            if name.lower() not in _BUILTIN_TABLE_STYLES:  # wordgenx knows those
                styles.append(_read_table_style(element, name, ctx))
                ctx.stats.count("style")
            continue
        ppr = element.find(_w("pPr"))
        paragraph, _style, numbering = _para_props(ppr, ctx)
        if kind is ir.StyleKind.CHARACTER:
            paragraph = EMPTY_PARAGRAPH
        run, _ = _run_props(element.find(_w("rPr")), ctx)
        number_ref = None
        if numbering is not None and kind is ir.StyleKind.PARAGRAPH:
            num_id, level = numbering
            if (num_name := ctx.numbering_defs.get(num_id)) is not None:
                number_ref = ir.NumberingRef(num_name, level + 1)
        styles.append(
            ir.Style(
                kind=kind,
                name=name,
                based_on=_base(element, ctx),
                next=ctx.style_name(_attr(element, "next", "val")),
                paragraph=paragraph,
                run=run,
                quick_format=element.find(_w("qFormat")) is not None,
                ui_priority=_optional_int(_attr(element, "uiPriority", "val")),
                link=ctx.style_name(_attr(element, "link", "val")),
                numbering=number_ref,
            )
        )
        ctx.stats.count("style")
    return tuple(styles)


def _attr(element: Element, child: str, attr: str) -> str | None:
    node = element.find(_w(child))
    return node.get(_w(attr)) if node is not None else None


def _optional_int(value: str | None) -> int | None:
    return _int(value) if value is not None else None


def _read_numbering(
    root: Element, ctx: _Ctx
) -> tuple[tuple[ir.Numbering, ...], dict[int, str], dict[int, tuple[str, int, int | None]]]:
    abstract_names: dict[int, str] = {}
    definitions: list[ir.Numbering] = []
    for index, abstract in enumerate(root.findall(_w("abstractNum")), start=1):
        abstract_id = _int(abstract.get(_w("abstractNumId")), index - 1)
        name_element = abstract.find(_w("name"))
        name = (name_element.get(_w("val")) if name_element is not None else None) or (
            f"List {index}"
        )
        levels = tuple(
            level
            for child in abstract.findall(_w("lvl"))
            if (level := _read_numbering_level(child, ctx)) is not None
        )
        if not levels:
            continue
        abstract_names[abstract_id] = name
        definitions.append(ir.Numbering(name, levels))

    defs: dict[int, str] = {}
    instances: dict[int, tuple[str, int, int | None]] = {}
    counters: dict[str, int] = {}
    for element in root.findall(_w("num")):
        num_id = _int(element.get(_w("numId")))
        abstract_ref = element.find(_w("abstractNumId"))
        abstract_id = _int(abstract_ref.get(_w("val"))) if abstract_ref is not None else -1
        name = abstract_names.get(abstract_id)
        if name is None:
            continue
        override = element.find(_w("lvlOverride"))
        if override is None:
            defs[num_id] = name
        else:
            counters[name] = counters.get(name, 0) + 1
            start_override = override.find(_w("startOverride"))
            start = _int(start_override.get(_w("val"))) if start_override is not None else None
            instances[num_id] = (name, counters[name], start)
    return tuple(definitions), defs, instances


def _read_numbering_level(element: Element, ctx: _Ctx) -> ir.NumberingLevel | None:
    level = _int(element.get(_w("ilvl"))) + 1
    fmt = _attr(element, "numFmt", "val") or "decimal"
    if fmt not in ir.NUMBER_FORMATS:
        ctx.stats.note(f"number format {fmt!r}")
        fmt = "decimal"
    paragraph, _style, numbering = _para_props(element.find(_w("pPr")), ctx)
    if numbering is not None:
        ctx.stats.note("numbering level with w:numPr")
    del numbering
    run, _ = _run_props(element.find(_w("rPr")), ctx)
    restart = _attr(element, "lvlRestart", "val")
    return ir.NumberingLevel(
        level=level,
        format=fmt,
        text=_attr(element, "lvlText", "val") or "",
        start=_int(_attr(element, "start", "val"), 1),
        style=ctx.style_name(_attr(element, "pStyle", "val")),
        legal=element.find(_w("isLgl")) is not None,
        suffix=_attr(element, "suff", "val") or "tab",
        restart_after=_optional_int(restart),
        marker_align=_attr(element, "lvlJc", "val") or "left",
        paragraph=paragraph,
        run=run,
    )


def _style_name_map(root: Element | None) -> dict[str, str]:
    names: dict[str, str] = {}
    if root is None:
        return names
    for element in root.findall(_w("style")):
        style_id = element.get(_w("styleId"))
        name_element = element.find(_w("name"))
        if style_id and name_element is not None and (name := name_element.get(_w("val"))):
            names[style_id] = name
    return names


def _read_meta(root: Element | None) -> ir.Meta:
    if root is None:
        return ir.Meta()

    def text(tag: str) -> str | None:
        node = root.find(tag)
        return node.text if node is not None and node.text else None

    def moment(tag: str) -> datetime | None:
        raw = text(tag)
        if raw is None:
            return None
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return None

    keywords = text(f"{{{NS.CP}}}keywords")
    return ir.Meta(
        title=text(f"{{{NS.DC}}}title"),
        subject=text(f"{{{NS.DC}}}subject"),
        author=text(f"{{{NS.DC}}}creator"),
        keywords=tuple(k.strip() for k in keywords.split(",") if k.strip()) if keywords else (),
        description=text(f"{{{NS.DC}}}description"),
        last_modified_by=text(f"{{{NS.CP}}}lastModifiedBy"),
        created=moment(f"{{{NS.DCTERMS}}}created"),
        modified=moment(f"{{{NS.DCTERMS}}}modified"),
        language=text(f"{{{NS.DC}}}language"),
    )


def _read_settings(root: Element | None) -> ir.Settings:
    if root is None:
        return ir.Settings()
    settings = ir.Settings()
    if (tab := root.find(_w("defaultTabStop"))) is not None:
        settings = replace(settings, default_tab_stop=Length.twips(_int(tab.get(_w("val")))))
    if root.find(_w("updateFields")) is not None:
        settings = replace(settings, update_fields_on_open=True)
    if root.find(_w("evenAndOddHeaders")) is not None:
        settings = replace(settings, even_and_odd_headers=True)
    return settings


# --- entry points -------------------------------------------------------------------------


@dataclass(frozen=True)
class ImportResult:
    document: ir.Document
    report: ImportReport


def read_docx(source: bytes | str | PathLike[str]) -> ImportResult:
    """Read a .docx into the IR, with a report of what could not be represented."""
    data = source if isinstance(source, bytes) else Path(source).read_bytes()
    opc = _Opc(data)
    document_part = _document_part(opc)
    if document_part is None:
        raise NotADocxError("no word/document.xml part found; not a Word document")

    styles_root = _related(opc, document_part, RT.STYLES, "word/styles.xml")
    numbering_root = _related(opc, document_part, RT.NUMBERING, "word/numbering.xml")
    meta_root = _package_part(opc, RT.CORE_PROPERTIES, "docProps/core.xml")
    settings_root = _related(opc, document_part, RT.SETTINGS, "word/settings.xml")

    stats = _Stats()
    ctx = _Ctx(opc, document_part, stats, style_names=_style_name_map(styles_root))
    numbering, defs, instances = (
        _read_numbering(numbering_root, ctx) if numbering_root is not None else ((), {}, {})
    )
    notes: dict[str, tuple[str, dict[str, Element]]] = {}
    for kind, reltype in (("footnote", RT.FOOTNOTES), ("endnote", RT.ENDNOTES)):
        part = _related_part(opc, document_part, reltype)
        notes_root = opc.parse(part) if part is not None else None
        if part is not None and notes_root is not None:
            notes[kind] = (
                part,
                {
                    note.get(_w("id")) or "": note
                    for note in notes_root.findall(_w(kind))
                    if note.get(_w("type")) in (None, "normal")
                },
            )
    comments: tuple[str, dict[str, Element]] = ("", {})
    comments_part = _related_part(opc, document_part, RT.COMMENTS)
    comments_root = opc.parse(comments_part) if comments_part is not None else None
    if comments_part is not None and comments_root is not None:
        comments = (
            comments_part,
            {c.get(_w("id")) or "": c for c in comments_root.findall(_w("comment"))},
        )
    ctx = replace(
        ctx,
        numbering_defs=defs,
        numbering_instances=instances,
        notes=notes,
        comments=comments,
    )
    styles = _read_styles(styles_root, ctx) if styles_root is not None else ()

    root = opc.parse(document_part)
    if root is None:
        raise NotADocxError(f"cannot parse {document_part}")
    body = root.find(_w("body"))
    if body is None:
        raise NotADocxError("the document part has no w:body")

    blocks = _read_blocks(body, ctx)
    final = body.find(_w("sectPr"))
    section = _read_section(final, ctx) if final is not None else ir.Section()

    default_run, _ = _run_props(_defaults(styles_root, "rPrDefault"), ctx)
    default_paragraph, _, _ = _para_props(_defaults(styles_root, "pPrDefault"), ctx)

    document = ir.Document(
        body=blocks,
        meta=_read_meta(meta_root),
        settings=_read_settings(settings_root),
        styles=styles,
        numbering=numbering,
        default_run=default_run,
        default_paragraph=default_paragraph,
        section=section,
    )
    return ImportResult(document, stats.report())


def _defaults(styles_root: Element | None, which: str) -> Element | None:
    if styles_root is None:
        return None
    default = styles_root.find(_w("docDefaults"))
    if default is None:
        return None
    block = default.find(_w(which))
    return block.find(_w("rPr" if which == "rPrDefault" else "pPr")) if block is not None else None


def _document_part(opc: _Opc) -> str | None:
    for _rtype, target, external in opc.relationships(None).values():
        if not external and target.lower().endswith("document.xml"):
            return posixpath.normpath(target.lstrip("/"))
    if opc.read("word/document.xml") is not None:
        return "word/document.xml"
    return None


def _related_part(opc: _Opc, source: str, reltype: str) -> str | None:
    """The name of the part `source` relates to with `reltype`."""
    for rtype, target, external in opc.relationships(source).values():
        if rtype == reltype and not external:
            return posixpath.normpath(posixpath.join(posixpath.dirname(source), target))
    return None


def _related(opc: _Opc, source: str, reltype: str, fallback: str) -> Element | None:
    for rtype, target, external in opc.relationships(source).values():
        if rtype == reltype and not external:
            base = posixpath.dirname(source)
            return opc.parse(posixpath.normpath(posixpath.join(base, target)))
    return opc.parse(fallback)


def _package_part(opc: _Opc, reltype: str, fallback: str) -> Element | None:
    for rtype, target, external in opc.relationships(None).values():
        if rtype == reltype and not external:
            return opc.parse(posixpath.normpath(target.lstrip("/")))
    return opc.parse(fallback)


__all__ = [
    "ImportReport",
    "ImportResult",
    "NotADocxError",
    "PackageDefinitions",
    "PackageReader",
    "open_package",
    "read_definitions",
    "read_docx",
]


# --- reading definitions only (templates) ----------------------------------------------------

PackageReader = _Opc
"""Read-only access to a package's parts and relationships."""


@dataclass(frozen=True)
class PackageDefinitions:
    """What a template defines, in IR form (best effort: for lookups, not for writing)."""

    styles: tuple[ir.Style, ...]
    numbering: tuple[ir.Numbering, ...]
    section: ir.Section
    """The final section's page setup (headers and footers are not read)."""
    report: ImportReport


def open_package(data: bytes) -> tuple[PackageReader, str]:
    """The package and its main document part (raises NotADocxError)."""
    opc = _Opc(data)
    document_part = _document_part(opc)
    if document_part is None:
        raise NotADocxError("no word/document.xml part found; not a Word document or template")
    return opc, document_part


def read_definitions(opc: PackageReader, document_part: str) -> PackageDefinitions:
    styles_root = _related(opc, document_part, RT.STYLES, "word/styles.xml")
    numbering_root = _related(opc, document_part, RT.NUMBERING, "word/numbering.xml")
    stats = _Stats()
    ctx = _Ctx(opc, document_part, stats, style_names=_style_name_map(styles_root))
    numbering, defs, instances = (
        _read_numbering(numbering_root, ctx) if numbering_root is not None else ((), {}, {})
    )
    ctx = replace(ctx, numbering_defs=defs, numbering_instances=instances)
    styles = _read_styles(styles_root, ctx) if styles_root is not None else ()
    section = ir.Section()
    root = opc.parse(document_part)
    body = root.find(_w("body")) if root is not None else None
    final = body.find(_w("sectPr")) if body is not None else None
    if final is not None:
        page_only = copy.deepcopy(final)
        for reference in [
            *page_only.findall(_w("headerReference")),
            *page_only.findall(_w("footerReference")),
        ]:
            page_only.remove(reference)
        section = _read_section(page_only, ctx)
    return PackageDefinitions(styles, numbering, section, stats.report())
