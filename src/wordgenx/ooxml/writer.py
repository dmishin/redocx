"""Assemble a .docx (or .dotx) package from a document IR, optionally into a template."""

from __future__ import annotations

import hashlib
import itertools
from collections.abc import Iterator
from dataclasses import replace
from os import PathLike

from lxml import etree

from wordgenx.ir import (
    IMAGE_FORMATS,
    NOTE_KINDS,
    Block,
    Document,
    Image,
    Note,
    Numbering,
    NumberingRef,
    Section,
    SectionBreak,
    Style,
    StyleKind,
)
from wordgenx.ooxml.constants import CT, NS, RT
from wordgenx.ooxml.merge import (
    PartCopier,
    merge_fonts,
    merge_numbering,
    merge_settings,
    merge_styles,
    numbering_offsets,
    remap_style_ids,
    without_note_separators,
)
from wordgenx.ooxml.opc import Package, Part
from wordgenx.ooxml.parts.docprops import app_xml, core_xml
from wordgenx.ooxml.parts.document import (
    BodyContext,
    document_xml,
    header_footer_xml,
    note_xml,
    notes_xml,
)
from wordgenx.ooxml.parts.fonts import font_table_xml
from wordgenx.ooxml.parts.numbering import default_level, list_num_ids, numbering_xml
from wordgenx.ooxml.parts.settings import settings_xml
from wordgenx.ooxml.parts.styles import StyleError, resolve_styles, styles_xml
from wordgenx.ooxml.parts.theme import Theme, theme_xml
from wordgenx.ooxml.style_names import style_id
from wordgenx.ooxml.template import MANAGED_RELATIONSHIPS, Template
from wordgenx.ooxml.xml import E, Element, serialize
from wordgenx.props import PropertySet, Target


def _with_default_fonts(document: Document) -> Document:
    """Fill in run defaults Word would otherwise take from its own legacy defaults
    (Times New Roman 10pt): the theme body font, 11pt and the document language. A
    template has its own defaults; only the language is added."""
    defaults: dict[str, object] = (
        {} if document.template is not None else {"font": "theme.minor", "size": "11pt"}
    )
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
    template_lists = document.template.lists if document.template is not None else {}
    for paragraph in document.all_paragraphs():
        item = paragraph.list_item
        if (
            item is not None
            and item.numbering not in definitions
            and item.numbering not in template_lists
        ):
            raise StyleError(f"list numbering {item.numbering!r} is not defined")

    # Level -> style links: give the style its w:numPr.
    for numbering in definitions.values():
        for level in numbering.levels:
            if level.style is None:
                continue
            sid = style_id(level.style)
            style = styles.get(sid)
            template = document.template
            if style is None and template is not None and template.style(level.style):
                # a template style: our copy carries only the link and merges into it
                style = styles[sid] = Style(StyleKind.PARAGRAPH, level.style, quick_format=False)
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

    known = {style_id(s.name) for s in document.styles}
    ordered_styles = tuple(styles[style_id(s.name)] for s in document.styles)
    ordered_styles += tuple(s for sid, s in styles.items() if sid not in known)
    return replace(document, styles=ordered_styles, numbering=tuple(definitions.values()))


IMAGE_CONTENT_TYPES = {"png": CT.PNG, "jpeg": CT.JPEG, "gif": CT.GIF, "bmp": CT.BMP}


def _check_style_references(document: Document) -> None:
    styles = resolve_styles(document)
    template = document.template

    def template_kind(name: str) -> str | None:
        found = template.style(name) if template is not None else None
        return found.kind if found is not None else None

    for table in document.all_tables():
        if table.style is not None:
            style = styles.get(style_id(table.style))
            if style is None and template_kind(table.style) == "table":
                continue
            if style is None or style.kind is not StyleKind.TABLE:
                raise StyleError(f"table style {table.style!r} is not defined")
    for paragraph in document.all_paragraphs():
        refs = [(paragraph.style, StyleKind.PARAGRAPH)]
        refs += [(run.style, StyleKind.CHARACTER) for run in paragraph.runs()]
        for name, kind in refs:
            if name is None:
                continue
            style = styles.get(style_id(name))
            if style is None and (found := template_kind(name)) is not None:
                if found != kind.value:
                    raise StyleError(f"style {name!r} is a {found} style, not {kind.value}")
                continue
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


def _style_id_map(template: Template | None) -> dict[str, str]:
    """Our style id -> the template's, where they differ (localized templates: Word's
    German "heading 1" has the id "berschrift1")."""
    if template is None:
        return {}
    return {
        style_id(entry.name): entry.id
        for entry in template.by_id.values()
        if style_id(entry.name) != entry.id and style_id(entry.name) not in template.by_id
    }


_FIXED_STYLES = frozenset({"DefaultParagraphFont", "TableNormal", "NoList"})


def _settings_taken(document: Document) -> frozenset[str]:
    """The settings.xml elements a document sets over a template's."""
    settings = document.settings
    taken = {"footnotePr", "endnotePr"}
    if settings.even_and_odd_headers:
        taken.add("evenAndOddHeaders")
    if settings.update_fields_on_open:
        taken.add("updateFields")
    if settings.default_tab_stop != type(settings)().default_tab_stop:
        taken.add("defaultTabStop")
    if document.meta.language:
        taken.add("themeFontLang")
    return frozenset(taken)


def build_package(
    document: Document, theme: Theme | None = None, *, as_template: bool = False
) -> Package:
    """Build the OPC package for `document` (not yet serialized). With `as_template`, the
    main part is a template's (.dotx)."""
    template = document.template
    if template is not None and template.theme is not None:
        theme = template.theme
    theme = theme or Theme()
    document = _link_numbering(_with_default_fonts(document))
    _check_style_references(document)
    ids = _style_id_map(template)
    offsets = numbering_offsets(template.numbering if template else None)
    has_notes = {kind: bool(document.notes(kind)) for kind in NOTE_KINDS}

    def finish(element: Element) -> bytes:
        remap_style_ids(element, ids)
        return serialize(element)

    pkg = Package()
    copier = PartCopier(pkg, template) if template is not None else None
    # Parts first, so the fixed relationships get stable ids (rId1 = styles, ...) and
    # hyperlinks found while writing the body come after them.
    main_type = CT.WML_TEMPLATE_MAIN if as_template else CT.WML_DOCUMENT_MAIN
    main = pkg.add_part("/word/document.xml", main_type)
    styles_root = styles_xml(document, theme, offsets[0])
    settings_root = settings_xml(document)
    fonts_root = font_table_xml(_font_names(document, theme))
    if template is not None:
        remap_style_ids(styles_root, ids)
        if template.styles is not None:
            styles_root = merge_styles(template.styles, styles_root, _FIXED_STYLES)
        if template.settings is not None:
            settings_root = merge_settings(
                template.settings, settings_root, _settings_taken(document)
            )
            without_note_separators(settings_root, [k for k, used in has_notes.items() if not used])
        if template.font_table is not None:
            fonts_root = merge_fonts(template.font_table, fonts_root)
    styles = pkg.add_part("/word/styles.xml", CT.WML_STYLES, finish(styles_root))
    settings = pkg.add_part("/word/settings.xml", CT.WML_SETTINGS, serialize(settings_root))
    fonts = pkg.add_part("/word/fontTable.xml", CT.WML_FONT_TABLE, serialize(fonts_root))
    if copier is not None and template is not None and template.theme_part is not None:
        theme_part = copier.copy(template.theme_part)
    else:
        theme_part = pkg.add_part("/word/theme/theme1.xml", CT.THEME, serialize(theme_xml(theme)))
    if copier is not None and template is not None:
        for reltype, part in ((RT.SETTINGS, settings), (RT.FONT_TABLE, fonts)):
            source = template.related(reltype)
            if source is not None:
                copier.relate_all(source, part)  # attached template, embedded fonts, ...
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
    instances = list_num_ids(document.numbering, used_lists, offsets[0])
    template_numbering = template.numbering if template is not None else None
    if document.numbering or template_numbering is not None:
        numbering_root = numbering_xml(
            document.numbering,
            theme,
            instances,
            offsets,
            template.lists if template is not None else {},
        )
        if template_numbering is not None:
            numbering_root = merge_numbering(template_numbering, numbering_root)
        numbering = pkg.add_part("/word/numbering.xml", CT.WML_NUMBERING, finish(numbering_root))
        main.relate(numbering, RT.NUMBERING)
        source = template.related(RT.NUMBERING) if template is not None else None
        if copier is not None and source is not None:
            copier.relate_all(source, numbering)  # picture bullets

    media: dict[bytes, Part] = {}  # sha256 -> media part (identical images are stored once)
    list_ids = {(i.numbering, i.instance): i.num_id for i in instances}
    drawing_ids = itertools.count(1)
    part_counts = {"header": 0, "footer": 0}
    bookmarks: dict[str, int] = {}

    def bookmark_id(name: str) -> int:
        return bookmarks.setdefault(name, len(bookmarks))

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
            theme,
            hyperlink,
            list_ids,
            image,
            lambda: next(drawing_ids),
            header_footer,
            bookmark_id,
            note,
            document.settings,
            inherited,
            template_body,
        )

    def header_footer(family: str, blocks: tuple[Block, ...]) -> str:
        part_counts[family] += 1
        content_type = CT.WML_HEADER if family == "header" else CT.WML_FOOTER
        part = pkg.add_part(f"/word/{family}{part_counts[family]}.xml", content_type)
        rel_id = main.relate(part, RT.HEADER if family == "header" else RT.FOOTER)
        part.data = finish(header_footer_xml(family, blocks, context(part)))
        return rel_id

    # The template: headers and footers the first section does not define, its body
    # content, and the parts wordgenx does not manage.
    first_section = next(
        (b.section for b in document.body if isinstance(b, SectionBreak)), document.section
    )
    inherited_ids: dict[tuple[str, str], str] = {}

    def inherited(section: Section) -> list[Element]:
        if template is None or copier is None or section is not first_section:
            return []
        out: list[Element] = []
        for family, kind, source in template.references:
            defined = section.headers if family == "header" else section.footers
            if any(k == kind for k, _ in defined):
                continue
            if (family, kind) not in inherited_ids:
                reltype = RT.HEADER if family == "header" else RT.FOOTER
                inherited_ids[(family, kind)] = main.relate(copier.copy(source), reltype)
            rel_id = inherited_ids[(family, kind)]
            out.append(E(f"w:{family}Reference", {"w:type": kind, "r:id": rel_id}))
        return out

    def template_body() -> list[Element]:
        if template is None or copier is None or not template.body:
            return []
        return copier.rewrite(
            template.body, template.document_part, main, lambda: next(drawing_ids), bookmark_id
        )

    notes: dict[str, tuple[Part, BodyContext, list[Element]]] = {}

    def note(item: Note) -> int:
        """Notes are numbered 1, 2, ... per kind in the order the body refers to them."""
        if item.kind not in notes:
            footnote = item.kind == "footnote"
            part = pkg.add_part(
                f"/word/{item.kind}s.xml", CT.WML_FOOTNOTES if footnote else CT.WML_ENDNOTES
            )
            main.relate(part, RT.FOOTNOTES if footnote else RT.ENDNOTES)
            notes[item.kind] = (part, context(part), [])
        _, note_context, written = notes[item.kind]
        note_id = len(written) + 1
        written.append(note_xml(item, note_id, note_context))
        return note_id

    body = context(main)
    root = None
    if template is not None and template.body:  # its namespaces (w14, mc:Ignorable, ...)
        source = template.document_root
        root = etree.Element(source.tag, attrib=dict(source.attrib), nsmap=source.nsmap)
    main.data = finish(document_xml(document, body, root))
    for kind, (part, _, written) in notes.items():
        part.data = finish(notes_xml(kind, written))
    if template is not None and copier is not None:
        _copy_unmanaged(template, copier, pkg, main)
    return pkg


def _copy_unmanaged(template: Template, copier: PartCopier, pkg: Package, main: Part) -> None:
    """Template parts wordgenx does not write: glossary (building blocks), customXml, web
    settings, custom document properties, ..."""
    referenced = {
        value
        for element in template.body
        for node in element.iter()
        for attr, value in node.attrib.items()
        if attr.startswith(f"{{{NS.R}}}")
    }
    for rel_id, (reltype, _target, external) in template.relationships(
        template.document_part
    ).items():
        if external or reltype in MANAGED_RELATIONSHIPS or rel_id in referenced:
            continue
        source, _ = template.target(template.document_part, rel_id)
        if source is not None and template.reader.read(source) is not None:
            main.relate(copier.copy(source), reltype)
    for _rel_id, (reltype, target, external) in template.relationships(None).items():
        if reltype == RT.CUSTOM_PROPERTIES and not external:
            pkg.relate(copier.copy(target.lstrip("/")), reltype)


def to_docx_bytes(
    document: Document, theme: Theme | None = None, *, as_template: bool = False
) -> bytes:
    return build_package(document, theme, as_template=as_template).to_bytes()


def write_docx(document: Document, path: str | PathLike[str], theme: Theme | None = None) -> None:
    """Write `document` as a .docx file (a .dotx template when the name ends in .dotx)."""
    as_template = str(path).lower().endswith(".dotx")
    build_package(document, theme, as_template=as_template).save(path)
