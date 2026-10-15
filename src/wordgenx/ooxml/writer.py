"""Assemble a .docx package from a document IR."""

from __future__ import annotations

from collections.abc import Iterator
from os import PathLike

from wordgenx.ir import Document, StyleKind
from wordgenx.ooxml.constants import CT, RT
from wordgenx.ooxml.opc import Package
from wordgenx.ooxml.parts.docprops import app_xml, core_xml
from wordgenx.ooxml.parts.document import document_xml
from wordgenx.ooxml.parts.fonts import font_table_xml
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
    return Document(
        body=document.body,
        meta=document.meta,
        settings=document.settings,
        styles=document.styles,
        default_run=base.merged(document.default_run),
        default_paragraph=document.default_paragraph,
        page=document.page,
    )


def _check_style_references(document: Document) -> None:
    styles = resolve_styles(document)
    for paragraph in document.body:
        refs = [(paragraph.style, StyleKind.PARAGRAPH)]
        refs += [(run.style, StyleKind.CHARACTER) for run in paragraph.content]
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
    property_sets += [run.props for p in document.body for run in p.content]
    for props in property_sets:
        font = props.get("font")
        if isinstance(font, str) and not font.startswith("theme."):
            yield font


def build_package(document: Document, theme: Theme | None = None) -> Package:
    """Build the OPC package for `document` (not yet serialized)."""
    theme = theme or Theme()
    document = _with_default_fonts(document)
    _check_style_references(document)

    pkg = Package()
    main = pkg.add_part(
        "/word/document.xml", CT.WML_DOCUMENT_MAIN, serialize(document_xml(document, theme))
    )
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
    return pkg


def to_docx_bytes(document: Document, theme: Theme | None = None) -> bytes:
    return build_package(document, theme).to_bytes()


def write_docx(document: Document, path: str | PathLike[str], theme: Theme | None = None) -> None:
    """Write `document` as a .docx file."""
    build_package(document, theme).save(path)
