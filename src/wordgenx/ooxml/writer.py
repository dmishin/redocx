"""Assemble a .docx package from a document IR."""

from __future__ import annotations

import hashlib
import itertools
from collections.abc import Iterator
from dataclasses import replace
from os import PathLike

from wordgenx.ir import (
    IMAGE_FORMATS,
    Block,
    Document,
    Image,
    Numbering,
    NumberingRef,
    StyleKind,
)
from wordgenx.ooxml.constants import CT, RT
from wordgenx.ooxml.opc import Package, Part
from wordgenx.ooxml.parts.docprops import app_xml, core_xml
from wordgenx.ooxml.parts.document import BodyContext, document_xml, header_footer_xml
from wordgenx.ooxml.parts.fonts import font_table_xml
from wordgenx.ooxml.parts.numbering import default_level, list_num_ids, numbering_xml
from wordgenx.ooxml.parts.settings import settings_xml
from wordgenx.ooxml.parts.styles import StyleError, resolve_styles, styles_xml
from wordgenx.ooxml.parts.theme import Theme, theme_xml
from wordgenx.ooxml.style_names import style_id
from wordgenx.ooxml.xml import serialize
from wordgenx.props import PropertySet, Target


def _with_default_fonts(document: Document) -> Document:
    """Fill in run defaults Word would otherwise take from its own legacy defaults
    (Times New Roman 10pt): the theme body font, 11pt and the document language."""
    defaults: dict[str, object] = {"font": "theme.minor", "size": "11pt"}
    if document.meta.language:
        defaults["lang"] = document.meta.language
    base = PropertySet(Target.RUN, defaults)
    return replace(document, default_run=base.merged(document.default_run))


def _link_numbering(document: Document) -> Document:
    """Validate numbering references and complete the two-way style <-> level links.

    Word numbers a heading style through both a w:numPr in the style and a w:pStyle
    in the list level. Declaring either side is enough; this fills in the other.
    """
    definitions: dict[str, Numbering] = {}
    for numbering in document.numbering:
        if numbering.name in definitions:
            raise StyleError(f"numbering {numbering.name!r} is defined twice")
        definitions[numbering.name] = numbering
    styles = {style_id(s.name): s for s in document.styles}

    for style in styles.values():
        ref = style.numbering
        if ref is not None and ref.name not in definitions:
            raise StyleError(f"style {style.name!r}: numbering {ref.name!r} is not defined")
    for paragraph in document.all_paragraphs():
        item = paragraph.list_item
        if item is not None and item.numbering not in definitions:
            raise StyleError(f"list numbering {item.numbering!r} is not defined")

    # Level -> style links: give the style its w:numPr.
    for numbering in definitions.values():
        for level in numbering.levels:
            if level.style is None:
                continue
            sid = style_id(level.style)
            style = styles.get(sid)
            if style is None or style.kind is not StyleKind.PARAGRAPH:
                raise StyleError(
                    f"numbering {numbering.name!r} level {level.level}: "
                    f"paragraph style {level.style!r} is not defined"
                )
            ref = NumberingRef(numbering.name, level.level)
            if style.numbering is None:
                styles[sid] = replace(style, numbering=ref)
            elif style.numbering != ref:
                raise StyleError(
                    f"style {style.name!r} is linked to two list levels: "
                    f"{style.numbering.name} level {style.numbering.level} and "
                    f"{numbering.name} level {level.level}"
                )

    # Style -> level links: give the level its w:pStyle (first style wins).
    for style in styles.values():
        ref = style.numbering
        if ref is None:
            continue
        numbering = definitions[ref.name]
        level = numbering.level(ref.level) or default_level(numbering, ref.level)
        if level.style is None:
            level = replace(level, style=style.name)
            others = tuple(lvl for lvl in numbering.levels if lvl.level != ref.level)
            levels = tuple(sorted((*others, level), key=lambda lvl: lvl.level))
            definitions[ref.name] = replace(numbering, levels=levels)

    ordered_styles = tuple(styles[style_id(s.name)] for s in document.styles)
    return replace(document, styles=ordered_styles, numbering=tuple(definitions.values()))


IMAGE_CONTENT_TYPES = {"png": CT.PNG, "jpeg": CT.JPEG, "gif": CT.GIF, "bmp": CT.BMP}


def _check_style_references(document: Document) -> None:
    styles = resolve_styles(document)
    for table in document.all_tables():
        if table.style is not None:
            style = styles.get(style_id(table.style))
            if style is None or style.kind is not StyleKind.TABLE:
                raise StyleError(f"table style {table.style!r} is not defined")
    for paragraph in document.all_paragraphs():
        refs = [(paragraph.style, StyleKind.PARAGRAPH)]
        refs += [(run.style, StyleKind.CHARACTER) for run in paragraph.runs()]
        for name, kind in refs:
            if name is None:
                continue
            style = styles.get(style_id(name))
            if style is None:
                raise StyleError(f"{kind.value} style {name!r} is not defined")
            if style.kind is not kind:
                raise StyleError(f"style {name!r} is a {style.kind.value} style, not {kind.value}")


def _font_names(document: Document, theme: Theme) -> Iterator[str]:
    yield theme.major_font
    yield theme.minor_font
    property_sets = [document.default_run, *(s.run for s in document.styles)]
    property_sets += [level.run for n in document.numbering for level in n.levels]
    property_sets += [run.props for p in document.all_paragraphs() for run in p.runs()]
    for props in property_sets:
        font = props.get("font")
        if isinstance(font, str) and not font.startswith("theme."):
            yield font


def build_package(document: Document, theme: Theme | None = None) -> Package:
    """Build the OPC package for `document` (not yet serialized)."""
    theme = theme or Theme()
    document = _link_numbering(_with_default_fonts(document))
    _check_style_references(document)

    pkg = Package()
    # Parts first, so the fixed relationships get stable ids (rId1 = styles, ...) and
    # hyperlinks found while writing the body come after them.
    main = pkg.add_part("/word/document.xml", CT.WML_DOCUMENT_MAIN)
    styles = pkg.add_part("/word/styles.xml", CT.WML_STYLES, serialize(styles_xml(document, theme)))
    settings = pkg.add_part(
        "/word/settings.xml", CT.WML_SETTINGS, serialize(settings_xml(document))
    )
    fonts = pkg.add_part(
        "/word/fontTable.xml",
        CT.WML_FONT_TABLE,
        serialize(font_table_xml(_font_names(document, theme))),
    )
    theme_part = pkg.add_part("/word/theme/theme1.xml", CT.THEME, serialize(theme_xml(theme)))
    core = pkg.add_part(
        "/docProps/core.xml", CT.CORE_PROPERTIES, serialize(core_xml(document.meta))
    )
    app = pkg.add_part("/docProps/app.xml", CT.EXTENDED_PROPERTIES, serialize(app_xml()))

    pkg.relate(main, RT.OFFICE_DOCUMENT)
    pkg.relate(core, RT.CORE_PROPERTIES)
    pkg.relate(app, RT.EXTENDED_PROPERTIES)
    main.relate(styles, RT.STYLES)
    main.relate(settings, RT.SETTINGS)
    main.relate(fonts, RT.FONT_TABLE)
    main.relate(theme_part, RT.THEME)

    used_lists = [
        (p.list_item.numbering, p.list_item.instance, p.list_item.start)
        for p in document.all_paragraphs()
        if p.list_item is not None
    ]
    instances = list_num_ids(document.numbering, used_lists)
    if document.numbering:
        numbering = pkg.add_part(
            "/word/numbering.xml",
            CT.WML_NUMBERING,
            serialize(numbering_xml(document.numbering, theme, instances)),
        )
        main.relate(numbering, RT.NUMBERING)

    media: dict[bytes, Part] = {}  # sha256 -> media part (identical images are stored once)
    list_ids = {(i.numbering, i.instance): i.num_id for i in instances}
    drawing_ids = itertools.count(1)
    part_counts = {"header": 0, "footer": 0}

    def context(part: Part) -> BodyContext:
        """Relationships for hyperlinks and images belong to the part that uses them."""
        links: dict[str, str] = {}
        images: dict[bytes, str] = {}

        def hyperlink(url: str) -> str:
            if url not in links:
                links[url] = part.relate_external(url, RT.HYPERLINK)
            return links[url]

        def image(picture: Image) -> str:
            digest = hashlib.sha256(picture.data).digest()
            if digest not in media:
                media[digest] = pkg.add_part(
                    f"/word/media/image{len(media) + 1}.{IMAGE_FORMATS[picture.format]}",
                    IMAGE_CONTENT_TYPES[picture.format],
                    picture.data,
                )
            if digest not in images:
                images[digest] = part.relate(media[digest], RT.IMAGE)
            return images[digest]

        return BodyContext(
            theme, hyperlink, list_ids, image, lambda: next(drawing_ids), header_footer
        )

    def header_footer(family: str, blocks: tuple[Block, ...]) -> str:
        part_counts[family] += 1
        content_type = CT.WML_HEADER if family == "header" else CT.WML_FOOTER
        part = pkg.add_part(f"/word/{family}{part_counts[family]}.xml", content_type)
        rel_id = main.relate(part, RT.HEADER if family == "header" else RT.FOOTER)
        part.data = serialize(header_footer_xml(family, blocks, context(part)))
        return rel_id

    body = context(main)
    main.data = serialize(document_xml(document, body))
    return pkg


def to_docx_bytes(document: Document, theme: Theme | None = None) -> bytes:
    return build_package(document, theme).to_bytes()


def write_docx(document: Document, path: str | PathLike[str], theme: Theme | None = None) -> None:
    """Write `document` as a .docx file."""
    build_package(document, theme).save(path)
