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
from wordgenx.values import TAB_ALIGNMENTS, TAB_LEADERS, Color, Length, LineSpacing, TabStop

_A = "http://schemas.openxmlformats.org/drawingml/2006/main"
_WP = "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
_ASVG = "http://schemas.microsoft.com/office/drawing/2016/SVG/main"
_PIC = "http://schemas.openxmlformats.org/drawingml/2006/picture"

_PREFIX_BY_URI = {NS.W: "w", NS.R: "r", _A: "a", _WP: "wp", _PIC: "pic"}

_RUN_IMAGE_FORMATS = {"png": "png", "jpeg": "jpeg", "jpg": "jpeg", "gif": "gif", "bmp": "bmp"}

_BUILTIN_TABLE_STYLES = {"normal table", "table grid"}
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


def _decode_tabs(element: Element, ctx: _Ctx) -> tuple[TabStop, ...] | None:
    stops: list[TabStop] = []
    for tab in element.findall(_w("tab")):
        position = tab.get(_w("pos"))
        if position is None:
            ctx.stats.note("w:tab without a position")
            continue
        align = tab.get(_w("val")) or "left"
        align = {"start": "left", "end": "right"}.get(align, align)
        if align not in TAB_ALIGNMENTS:  # clear (removes an inherited stop), num (lists)
            ctx.stats.note(f"tab stop {align!r}")
            continue
        leader = _LEADER_NAMES.get(tab.get(_w("leader")) or "none", "none")
        stops.append(TabStop(Length.twips(_int(position)), align, leader))
    return tuple(stops) if stops else None


_RUN_TOGGLES = {
    "b": "bold",
    "i": "italic",
    "caps": "caps",
    "smallCaps": "small-caps",
    "strike": "strike",
    "dstrike": "double-strike",
    "vanish": "hidden",
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
            if value := child.get(_w("val")):
                values["lang"] = value
        elif _local(child.tag) in _RUN_TOGGLES:
            values[_RUN_TOGGLES[_local(child.tag)]] = _toggle_state(child)
        elif name in ("w:szCs", "w:rStyle"):
            pass
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
            values["outline-level"] = _int(child.get(_w("val"))) + 1
        elif child.tag == _w("numPr"):
            numbering = _decode_numbering(child)
        elif child.tag == _w("sectPr"):
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


def _read_drawing(drawing: Element, ctx: _Ctx) -> ir.Image | None:
    blip = drawing.find(f".//{_a('blip')}")
    inline = drawing.find(f".//{_wp('inline')}")
    if blip is None:
        return None
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
    if inline is None:
        return None
    extent = inline.find(_wp("extent"))
    if extent is None:
        return None
    width = Length(_int(extent.get("cx")))
    height = Length(_int(extent.get("cy")))
    if width.emu <= 0 or height.emu <= 0:
        return None
    doc_pr = inline.find(_wp("docPr"))
    description = name = ""
    if doc_pr is not None:
        description = doc_pr.get("descr") or ""
        name = doc_pr.get("name") or ""
    ctx.stats.count("image")
    svg = _svg_blip(blip, ctx)
    return ir.Image(data, image_format, width, height, description, name, svg=svg)


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
        elif tag == _w("drawing"):
            flush()
            if (image := _read_drawing(child, ctx)) is not None:
                items.append(image)
            else:
                ctx.stats.note("w:drawing")
        elif tag in (_w("rPr"), _w("fldChar"), _w("instrText")):
            pass
        else:
            ctx.stats.note(_qname(tag))
    flush()
    return items


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
    if tag == _w("proofErr"):
        return []
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

    ctx.stats.count("paragraph")
    return ir.Paragraph(content, style, props, list_item, unnumbered), section


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
            if style is not None and style.lower() != "table grid":
                ctx.stats.note(f"table style {style!r}")
                style = None
        if (jc := tbl_pr.find(_w("jc"))) is not None:
            align = jc.get(_w("val"))

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
        table = ir.Table(tuple(rows), tuple(widths), style, align)
    except ValueError as e:
        ctx.stats.note(f"unrepresentable table ({e})")
        return None
    ctx.stats.count("table")
    return table


def _cell_colspan(tc_pr: Element | None) -> int:
    if tc_pr is None or (span := tc_pr.find(_w("gridSpan"))) is None:
        return 1
    return max(1, _int(span.get(_w("val")), 1))


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


def _read_blocks(container: Element, ctx: _Ctx) -> tuple[ir.Block, ...]:
    blocks: list[ir.Block] = []
    for child in container:
        tag = child.tag
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
    )


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
        if kind is ir.StyleKind.TABLE:
            if name.lower() not in _BUILTIN_TABLE_STYLES:
                ctx.stats.note(f"table style {name!r}")
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
                based_on=ctx.style_name(_attr(element, "basedOn", "val")),
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
    ctx = replace(ctx, numbering_defs=defs, numbering_instances=instances)
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
