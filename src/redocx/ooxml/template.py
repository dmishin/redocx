"""Templates: compile into an existing .dotx or .docx (architecture §4.6).

The template's own XML is the base of the output. Its styles, numbering, settings, font
table and theme are kept and the document's definitions are merged in (`merge`). Headers
and footers carry over where the document defines none. Body content (boilerplate, a cover
page) comes before the document's. Parts redocx does not manage (glossary, customXml,
web settings, custom properties) are copied as they are.
"""

from __future__ import annotations

import posixpath
from dataclasses import dataclass, field
from os import PathLike
from pathlib import Path

from lxml import etree

from redocx import ir
from redocx.ooxml.constants import CT, NS, RT
from redocx.ooxml.parts.theme import Theme, theme_from_xml
from redocx.ooxml.reader import (
    NotADocxError,
    PackageDefinitions,
    PackageReader,
    open_package,
    read_definitions,
)
from redocx.ooxml.style_names import stored_name, style_id
from redocx.ooxml.xml import Element

W = f"{{{NS.W}}}"
_MS = "http://schemas.microsoft.com/office/2011/relationships"
_MS_2016 = "http://schemas.microsoft.com/office/2016/09/relationships"

MANAGED_RELATIONSHIPS = frozenset(
    {
        RT.STYLES,
        RT.NUMBERING,
        RT.SETTINGS,
        RT.FONT_TABLE,
        RT.THEME,
        RT.HEADER,
        RT.FOOTER,
        RT.FOOTNOTES,
        RT.ENDNOTES,
        RT.COMMENTS,
        RT.IMAGE,
        RT.HYPERLINK,
        f"{_MS}/commentsExtended",
        f"{_MS}/people",
        f"{_MS_2016}/commentsIds",
        "http://schemas.microsoft.com/office/2018/08/relationships/commentsExtensible",
        "http://schemas.microsoft.com/office/2007/relationships/stylesWithEffects",
    }
)
"""Relationships of the main part that redocx writes itself (or only copies when the
template's body refers to them)."""

UNSUPPORTED_IN_BODY = {
    "footnoteReference": "footnotes",
    "endnoteReference": "endnotes",
    "commentReference": "comments",
    "commentRangeStart": "comments",
    "ins": "tracked changes",
    "del": "tracked changes",
    "moveFrom": "tracked changes",
    "moveTo": "tracked changes",
}
"""Body content that refers to parts redocx replaces: such a body is not carried over."""

_CONTENT = frozenset(
    f"{W}{tag}"
    for tag in "t drawing pict object fldSimple instrText sdt tbl sectPr br sym tab".split()
)


class TemplateError(ValueError):
    """A template that cannot be used."""


@dataclass(frozen=True)
class TemplateStyle:
    id: str
    name: str
    kind: str
    """paragraph | character | table | numbering"""


@dataclass(eq=False)
class Template:
    name: str
    reader: PackageReader
    document_part: str
    content_types: dict[str, str]
    """Lower-cased part name (no leading /) or ``.ext`` -> content type."""
    styles: Element | None
    numbering: Element | None
    settings: Element | None
    font_table: Element | None
    theme_part: str | None
    theme: Theme | None
    definitions: PackageDefinitions
    references: tuple[tuple[str, str, str], ...]
    """Header and footer references of the final section: (family, kind, part name)."""
    body: tuple[Element, ...]
    body_issue: str | None
    """Why the template's body content is not carried over (None if it is, or is empty)."""
    is_template: bool
    """A .dotx (its main part has the template content type)."""
    document_root: Element
    """An empty copy of the template's w:document (its namespace declarations)."""
    by_id: dict[str, TemplateStyle] = field(default_factory=lambda: {})
    by_name: dict[str, TemplateStyle] = field(default_factory=lambda: {})
    lists: dict[str, tuple[int, ir.Numbering]] = field(default_factory=lambda: {})
    """List definitions by their w:abstractNum w:name: (w:abstractNumId, definition)."""

    @property
    def section(self) -> ir.Section:
        return self.definitions.section

    def style(self, name: str) -> TemplateStyle | None:
        """The template's style of this display name (localized templates have their own
        style IDs; built-ins are matched by their stored name)."""
        return self.by_name.get(stored_name(name).lower()) or self.by_id.get(style_id(name))

    def content_type(self, part: str) -> str:
        key = part.lstrip("/").lower()
        if key in self.content_types:
            return self.content_types[key]
        return self.content_types.get(posixpath.splitext(key)[1], CT.XML)

    def relationships(self, source: str | None) -> dict[str, tuple[str, str, bool]]:
        return self.reader.relationships(source)

    def target(self, source: str, rel_id: str) -> tuple[str | None, bool]:
        return self.reader.target(source, rel_id)

    def related(self, reltype: str) -> str | None:
        for rel_id, (rtype, _target, external) in self.relationships(self.document_part).items():
            if rtype == reltype and not external:
                return self.target(self.document_part, rel_id)[0]
        return None


def load_template(path: str | PathLike[str]) -> Template:
    """Read a .dotx or .docx to compile into. Raises TemplateError."""
    path = Path(path)
    try:
        data = path.read_bytes()
    except OSError as e:
        raise TemplateError(f"cannot read template `{path.name}`: {e.strerror}") from None
    try:
        reader, document_part = open_package(data)
    except NotADocxError as e:
        raise TemplateError(f"`{path.name}` is not a Word document or template: {e}") from None
    content_types = _content_types(reader)
    root = reader.parse(document_part)
    body_element = root.find(f"{W}body") if root is not None else None
    if body_element is None:
        raise TemplateError(f"`{path.name}`: the document part has no body")

    def part(reltype: str) -> Element | None:
        for rel_id, (rtype, _t, external) in reader.relationships(document_part).items():
            if rtype == reltype and not external:
                name, _ = reader.target(document_part, rel_id)
                return reader.parse(name) if name else None
        return None

    theme_part = None
    for rel_id, (rtype, _t, external) in reader.relationships(document_part).items():
        if rtype == RT.THEME and not external:
            theme_part, _ = reader.target(document_part, rel_id)
    theme_root = reader.parse(theme_part) if theme_part else None

    final = body_element.find(f"{W}sectPr")
    references: list[tuple[str, str, str]] = []
    if final is not None:
        for tag, family in (("headerReference", "header"), ("footerReference", "footer")):
            for reference in final.findall(f"{W}{tag}"):
                rel_id = reference.get(f"{{{NS.R}}}id")
                target, external = reader.target(document_part, rel_id) if rel_id else (None, True)
                if target and not external:
                    references.append((family, reference.get(f"{W}type") or "default", target))

    body = tuple(child for child in body_element if child.tag != f"{W}sectPr")
    body_issue = None
    if not any(_has_content(child) for child in body):
        body = ()
    else:
        for element in (e for child in body for e in child.iter()):
            local = etree.QName(element).localname if isinstance(element.tag, str) else ""
            if element.tag == f"{W}{local}" and local in UNSUPPORTED_IN_BODY:
                body_issue = f"it contains {UNSUPPORTED_IN_BODY[local]}"
                body = ()
                break

    main_type = content_types.get(document_part.lower(), CT.WML_DOCUMENT_MAIN)
    template = Template(
        name=path.name,
        reader=reader,
        document_part=document_part,
        content_types=content_types,
        styles=part(RT.STYLES),
        numbering=part(RT.NUMBERING),
        settings=part(RT.SETTINGS),
        font_table=part(RT.FONT_TABLE),
        theme_part=theme_part,
        theme=theme_from_xml(theme_root) if theme_root is not None else None,
        definitions=read_definitions(reader, document_part),
        references=tuple(references),
        body=body,
        body_issue=body_issue,
        is_template=main_type == CT.WML_TEMPLATE_MAIN,
        document_root=etree.Element(root.tag, attrib=dict(root.attrib), nsmap=root.nsmap),  # pyright: ignore[reportOptionalMemberAccess]
    )
    if template.styles is not None:
        for element in template.styles.findall(f"{W}style"):
            sid = element.get(f"{W}styleId")
            name_element = element.find(f"{W}name")
            name = name_element.get(f"{W}val") if name_element is not None else None
            if not sid:
                continue
            entry = TemplateStyle(sid, name or sid, element.get(f"{W}type") or "paragraph")
            template.by_id[sid] = entry
            if name:
                template.by_name.setdefault(name.lower(), entry)
    if template.numbering is not None:
        definitions = {n.name: n for n in reversed(template.definitions.numbering)}
        for element in template.numbering.findall(f"{W}abstractNum"):
            name_element = element.find(f"{W}name")
            name = name_element.get(f"{W}val") if name_element is not None else None
            abstract_id = element.get(f"{W}abstractNumId")
            if (
                name
                and name in definitions
                and abstract_id
                and abstract_id.isdigit()
                and element.find(f"{W}lvl") is not None  # not a w:numStyleLink
            ):
                template.lists.setdefault(name, (int(abstract_id), definitions[name]))
        # Unnamed definitions, under the names the reader gives them ("List 3"), so that an
        # imported document can use the lists of the original it compiles into.
        for name, abstract_id in template.definitions.list_ids.items():
            if name in definitions:
                template.lists.setdefault(name, (abstract_id, definitions[name]))
    return template


def _has_content(element: Element) -> bool:
    """Whether a body element shows anything (an empty paragraph does not)."""
    if element.tag != f"{W}p":
        return True
    return any(
        e.tag in _CONTENT and (e.tag != f"{W}t" or (e.text or "").strip()) for e in element.iter()
    )


def _content_types(reader: PackageReader) -> dict[str, str]:
    root = reader.parse("[Content_Types].xml")
    types: dict[str, str] = {}
    if root is None:
        return types
    ns = f"{{{NS.CONTENT_TYPES}}}"
    for element in root:
        content_type = element.get("ContentType")
        if not content_type:
            continue
        if element.tag == f"{ns}Default" and (ext := element.get("Extension")):
            types["." + ext.lower()] = content_type
        elif element.tag == f"{ns}Override" and (name := element.get("PartName")):
            types[name.lstrip("/").lower()] = content_type
    return types


__all__ = ["Template", "TemplateError", "TemplateStyle", "load_template"]
