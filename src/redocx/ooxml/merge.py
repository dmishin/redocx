"""Merging the document's definitions into a template's XML, and copying template parts.

The template's XML is the base: its styles, numbering, settings and font table keep
everything redocx does not know about. The document's definitions are merged in:

- A style the template has: the document's properties override the template's, element by
  element (``w:spacing`` and ``w:ind`` attribute by attribute); the template's name, base
  style, links and gallery settings stay. Other styles are added.
- Numbering: the document's list definitions are appended with ids after the template's.
- Settings: only what the document sets (headers, field updates, notes, tab stops,
  language) replaces the template's.
"""

from __future__ import annotations

import copy
import posixpath
from collections.abc import Callable, Mapping, Sequence

from redocx.ooxml.constants import NS
from redocx.ooxml.opc import Package, Part
from redocx.ooxml.schema_order import NUMBERING, PPR, RPR, SETTINGS, STYLE, TBL_PR, TC_PR, TR_PR
from redocx.ooxml.template import Template
from redocx.ooxml.xml import E, Element, qn

W = f"{{{NS.W}}}"
R = f"{{{NS.R}}}"

_CONTAINERS: dict[str, Sequence[str]] = {
    "pPr": PPR,
    "rPr": RPR,
    "tblPr": TBL_PR,
    "trPr": TR_PR,
    "tcPr": TC_PR,
}
_ATTRIBUTE_MERGED = frozenset({"spacing", "ind"})
"""Property elements merged attribute by attribute (the rest are replaced whole)."""
_IND_RIVALS = {
    "firstLine": ("hanging", "hangingChars"),
    "firstLineChars": ("hanging", "hangingChars"),
    "hanging": ("firstLine", "firstLineChars"),
    "hangingChars": ("firstLine", "firstLineChars"),
}
STYLE_REFERENCES = frozenset(
    f"{W}{tag}" for tag in "pStyle rStyle tblStyle basedOn next link".split()
)


def _local(element: Element) -> str:
    tag = element.tag
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


_OTHER_NAMESPACES = frozenset({qn("m:mathPr")})
"""Schema-ordered elements outside w: (CT_Settings holds m:mathPr)."""


def _sort(parent: Element, order: Sequence[str]) -> None:
    """Put `parent`'s children in schema order; unknown elements stay after the element
    that preceded them."""
    index = {name: i for i, name in enumerate(order)}
    keyed: list[tuple[float, int, Element]] = []
    last = -1.0
    for position, child in enumerate(parent):
        name = _local(child)
        if name in index and (str(child.tag).startswith(W) or child.tag in _OTHER_NAMESPACES):
            last = float(index[name])
            keyed.append((last, position, child))
        else:
            keyed.append((last + 0.5, position, child))
    for _, _, child in sorted(keyed, key=lambda item: (item[0], item[1])):
        parent.append(child)  # appending an existing child moves it


def merge_properties(base: Element, ours: Element, order: Sequence[str]) -> None:
    """Merge a property container (w:pPr, w:rPr, ...) of ours into the template's."""
    for child in list(ours):
        name = _local(child)
        existing = base.find(child.tag)  # pyright: ignore[reportArgumentType]
        if existing is None:
            base.append(copy.deepcopy(child))
        elif name in _ATTRIBUTE_MERGED:
            for attr, value in child.attrib.items():
                local = attr.rsplit("}", 1)[-1]
                if name == "ind":
                    for rival in _IND_RIVALS.get(local, ()):
                        existing.attrib.pop(f"{W}{rival}", None)
                existing.set(attr, value)
        else:
            base.replace(existing, copy.deepcopy(child))
    _sort(base, order)


def merge_style(base: Element, ours: Element) -> None:
    """Our style's formatting on top of the template's definition of the same style."""
    for child in list(ours):
        name = _local(child)
        if name in _CONTAINERS:
            existing = base.find(child.tag)  # pyright: ignore[reportArgumentType]
            if existing is None:
                base.append(copy.deepcopy(child))
            else:
                merge_properties(existing, child, _CONTAINERS[name])
        elif name == "tblStylePr":
            kind = child.get(f"{W}type")
            match = next(
                (c for c in base.findall(f"{W}tblStylePr") if c.get(f"{W}type") == kind), None
            )
            if match is None:
                base.append(copy.deepcopy(child))
            else:
                merge_style(match, child)
    _sort(
        base,
        (*STYLE, "pPr", "rPr", "tblPr", "trPr", "tcPr") if _local(base) == "tblStylePr" else STYLE,
    )


def merge_styles(template: Element, ours: Element, fixed: frozenset[str]) -> Element:
    """The template's styles.xml with our styles merged in. `fixed` are style ids of ours
    that are only Word's fixed defaults (the template's own are kept)."""
    result = copy.deepcopy(template)
    ours_defaults = ours.find(f"{W}docDefaults")
    if ours_defaults is not None:
        defaults = result.find(f"{W}docDefaults")
        if defaults is None:
            defaults = copy.deepcopy(ours_defaults)
            result.insert(0, defaults)
        else:
            for which, inner in (("rPrDefault", "rPr"), ("pPrDefault", "pPr")):
                block = ours_defaults.find(f"{W}{which}/{W}{inner}")
                if block is None or not len(block):
                    continue
                target = defaults.find(f"{W}{which}")
                if target is None:
                    defaults.append(E(f"w:{which}", copy.deepcopy(block)))
                    continue
                existing = target.find(f"{W}{inner}")
                if existing is None:
                    target.append(copy.deepcopy(block))
                else:
                    merge_properties(existing, block, _CONTAINERS[inner])
    by_id = {s.get(f"{W}styleId"): s for s in result.findall(f"{W}style")}
    for style in ours.findall(f"{W}style"):
        sid = style.get(f"{W}styleId")
        if sid in fixed and sid in by_id:
            continue
        if sid in by_id:
            merge_style(by_id[sid], style)
        else:
            result.append(copy.deepcopy(style))
    return result


def numbering_offsets(template: Element | None) -> tuple[int, int]:
    """(largest w:numId, largest w:abstractNumId + 1) of the template's numbering."""
    if template is None:
        return 0, 0
    nums = [int(n.get(f"{W}numId") or 0) for n in template.findall(f"{W}num")]
    abstract = [int(a.get(f"{W}abstractNumId") or 0) for a in template.findall(f"{W}abstractNum")]
    return max(nums, default=0), max(abstract, default=-1) + 1


def merge_numbering(template: Element, ours: Element) -> Element:
    """The template's numbering.xml with our (already renumbered) definitions added."""
    result = copy.deepcopy(template)
    for child in ours:
        result.append(copy.deepcopy(child))
    _sort(result, NUMBERING)
    return result


def merge_settings(template: Element, ours: Element, take: frozenset[str]) -> Element:
    """The template's settings.xml with the elements named in `take` from ours."""
    result = copy.deepcopy(template)
    for child in ours:
        name = _local(child)
        if name not in take:
            continue
        existing = result.find(child.tag)  # pyright: ignore[reportArgumentType]
        if existing is None:
            result.append(copy.deepcopy(child))
        else:
            result.replace(existing, copy.deepcopy(child))
    _sort(result, SETTINGS)
    return result


def without_note_separators(settings: Element, kinds: Sequence[str]) -> None:
    """Drop the template's references to separator notes of `kinds` (their part is not
    written), and empty w:footnotePr / w:endnotePr elements."""
    for kind in kinds:
        props = settings.find(f"{W}{kind}Pr")
        if props is None:
            continue
        for reference in props.findall(f"{W}{kind}"):
            props.remove(reference)
        if not len(props):
            settings.remove(props)


def merge_fonts(template: Element, ours: Element) -> Element:
    """The template's font table plus our fonts it does not list."""
    result = copy.deepcopy(template)
    names = {f.get(f"{W}name") for f in result.findall(f"{W}font")}
    for font in ours.findall(f"{W}font"):
        if font.get(f"{W}name") not in names:
            result.append(copy.deepcopy(font))
    return result


def remap_style_ids(root: Element, ids: Mapping[str, str]) -> None:
    """Point style references (and our style definitions) at the template's style ids."""
    if not ids:
        return
    for element in root.iter():
        if element.tag in STYLE_REFERENCES:
            value = element.get(f"{W}val")
            if value is not None and value in ids:
                element.set(f"{W}val", ids[value])
        elif element.tag == f"{W}style":
            value = element.get(f"{W}styleId")
            if value is not None and value in ids:
                element.set(f"{W}styleId", ids[value])


class PartCopier:
    """Copies template parts (with everything they relate to) into the package once each."""

    def __init__(self, package: Package, template: Template) -> None:
        self.package = package
        self.template = template
        self.copied: dict[str, Part] = {}

    def copy(self, source: str) -> Part:
        key = source.lstrip("/").lower()
        if key in self.copied:
            return self.copied[key]
        directory, filename = posixpath.split("/" + source.lstrip("/"))
        name = posixpath.join(directory, f"tpl-{filename}")
        counter = 1
        while self.package.has_part(name):
            counter += 1
            name = posixpath.join(directory, f"tpl{counter}-{filename}")
        data = self.template.reader.read(source) or b""
        part = self.package.add_part(name, self.template.content_type(source), data)
        self.copied[key] = part
        self.relate_all(source, part)
        return part

    def relate_all(self, source: str, part: Part) -> None:
        """Give `part` the relationships `source` has in the template, with the same ids."""
        for rel_id, (rtype, target, external) in self.template.relationships(source).items():
            if external:
                part.relate_external(target, rtype, rel_id=rel_id)
                continue
            resolved, _ = self.template.target(source, rel_id)
            if resolved is None or self.template.reader.read(resolved) is None:
                continue
            part.relate(self.copy(resolved), rtype, rel_id=rel_id)

    def rewrite(
        self,
        elements: Sequence[Element],
        source: str,
        owner: Part,
        drawing_id: Callable[[], int],
        bookmark_id: Callable[[str], int],
    ) -> list[Element]:
        """Copies of `elements` (from the template part `source`) for the part `owner`:
        relationship ids, drawing ids and bookmark ids renumbered for the new package."""
        ids: dict[str, str] = {}
        rels = self.template.relationships(source)
        out: list[Element] = []
        for element in elements:
            element = copy.deepcopy(element)
            for node in element.iter():
                for attr, value in list(node.attrib.items()):
                    if attr.startswith(R) and value in rels:
                        if value not in ids:
                            rtype, target, external = rels[value]
                            if external:
                                ids[value] = owner.relate_external(target, rtype)
                            else:
                                resolved, _ = self.template.target(source, value)
                                ids[value] = owner.relate(self.copy(resolved or target), rtype)
                        node.set(attr, ids[value])
                if (
                    node.tag
                    == "{http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing}docPr"
                ):
                    node.set("id", str(drawing_id()))
                elif node.tag in (f"{W}bookmarkStart", f"{W}bookmarkEnd"):
                    old = node.get(f"{W}id")
                    if old is not None:
                        node.set(f"{W}id", str(bookmark_id(f"\x00template:{old}")))
            out.append(element)
        return out


__all__ = [
    "PartCopier",
    "merge_fonts",
    "merge_numbering",
    "merge_settings",
    "merge_styles",
    "numbering_offsets",
    "remap_style_ids",
    "without_note_separators",
]
