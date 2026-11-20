"""Consistency checks on a written package: what the schemas cannot see.

The schemas check each part on its own. Word also refuses, or silently repairs, a
document whose parts disagree: a relationship id that names nothing, a bookmark that is
opened twice or never closed, two drawings with one id, a note, comment, style or list
that is referred to but not there. The writer is meant to get all of this right; the
check catches what it misses, and what raw XML (\\raw, ooxml.*) or a template brings in.
It reads the finished parts, so it is independent of how they were written.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterator
from functools import cache
from pathlib import Path

from lxml import etree

from redocx.ooxml.constants import NS
from redocx.ooxml.opc import Package, Part
from redocx.ooxml.xml import PREFIXES, Element

W = f"{{{NS.W}}}"
R = f"{{{NS.R}}}"
WP = f"{{{PREFIXES['wp']}}}"
M = f"{{{PREFIXES['m']}}}"
XSD = "{http://www.w3.org/2001/XMLSchema}"
MATH_SCHEMA = (
    Path(__file__).resolve().parents[1]
    / "data"
    / "schemas"
    / "ecma-376-transitional"
    / "shared-math.xsd"
)

_STORIES = ("document", "header", "footer", "footnotes", "endnotes", "comments")
"""Parts with text: bookmarks, references and styles are used in these."""

_STYLE_REFERENCES = {"pStyle": "paragraph", "rStyle": "character", "tblStyle": "table"}


def package_problems(pkg: Package) -> list[str]:
    """What is inconsistent in `pkg`, one short message each (empty when nothing is)."""
    roots = {part.name: _parse(part) for part in pkg.parts if _is_xml(part)}
    stories = {
        name: root
        for name, root in roots.items()
        if root is not None and name.startswith("/word/") and _story(name)
    }
    problems: list[str] = []
    for part in pkg.parts:
        root = roots.get(part.name)
        if root is not None:
            problems += _relationships(part, root)
    for name, root in stories.items():
        problems += _bookmarks(name, root)
    problems += _bookmark_names(stories)
    problems += _link_anchors(stories)
    problems += _drawing_ids(stories)
    problems += _notes_and_comments(stories, roots)
    problems += _styles_and_lists(stories, roots)
    return problems


def math_problems(pkg: Package) -> list[str]:
    """What makes the math of `pkg` invalid, one short message each (empty when nothing
    does). Unlike `package_problems`, any of these means the document must not be written."""
    problems: list[str] = []
    for part in pkg.parts:
        if _is_xml(part) and part.name.startswith("/word/") and _story(part.name):
            root = _parse(part)
            if root is not None:
                problems += _math(part.name, root)
    return problems


def _is_xml(part: Part) -> bool:
    return part.content_type.endswith("xml") and bool(part.data)


def _parse(part: Part) -> Element | None:
    try:
        return etree.fromstring(part.data)
    except etree.XMLSyntaxError:
        return None  # the schemas report it; nothing here can be checked


def _story(name: str) -> bool:
    stem = name.removeprefix("/word/").removesuffix(".xml")
    return "/" not in stem and stem.rstrip("0123456789") in _STORIES


def _short(name: str) -> str:
    return name.lstrip("/")


def _relationships(part: Part, root: Element) -> Iterator[str]:
    """Every r: attribute names a relationship of its part."""
    ids = {rel.id for rel in part.rels}
    missing: set[str] = set()
    for element in root.iter():
        for attr, value in element.attrib.items():
            if str(attr).startswith(R) and value not in ids:
                missing.add(value)
    for rel_id in sorted(missing):
        yield f"{_short(part.name)} refers to relationship {rel_id}, which it does not have"


def _bookmarks(name: str, root: Element) -> Iterator[str]:
    """Each bookmark id is opened once and closed once, in the same part."""
    starts = Counter(e.get(f"{W}id") for e in root.iter(f"{W}bookmarkStart"))
    ends = Counter(e.get(f"{W}id") for e in root.iter(f"{W}bookmarkEnd"))
    part = _short(name)
    for bookmark, count in sorted(starts.items(), key=lambda item: str(item[0])):
        if count > 1:
            yield f"{part}: bookmark id {bookmark} is opened {count} times"
        if bookmark not in ends:
            yield f"{part}: bookmark id {bookmark} is never closed"
    for bookmark, count in sorted(ends.items(), key=lambda item: str(item[0])):
        if count > 1:
            yield f"{part}: bookmark id {bookmark} is closed {count} times"
        if bookmark not in starts:
            yield f"{part}: bookmark id {bookmark} is closed but never opened"


def _bookmark_names(stories: dict[str, Element]) -> Iterator[str]:
    """Bookmark names are unique in the document (Word keeps only one of a name)."""
    names = Counter(
        str(e.get(f"{W}name")).lower()
        for root in stories.values()
        for e in root.iter(f"{W}bookmarkStart")
    )
    for bookmark, count in sorted(names.items()):
        if count > 1:
            yield f"bookmark `{bookmark}` is defined {count} times"


def _link_anchors(stories: dict[str, Element]) -> Iterator[str]:
    """Links within the document go to a bookmark that exists (or `_top`, the start)."""
    names = {
        str(e.get(f"{W}name")).lower()
        for root in stories.values()
        for e in root.iter(f"{W}bookmarkStart")
    }
    missing = {
        str(anchor)
        for root in stories.values()
        for link in root.iter(f"{W}hyperlink")
        if (anchor := link.get(f"{W}anchor")) is not None
        and anchor.lower() not in names
        and anchor != "_top"
    }
    for anchor in sorted(missing):
        yield f"a link goes to bookmark `{anchor}`, which does not exist"


def _drawing_ids(stories: dict[str, Element]) -> Iterator[str]:
    """Drawing ids (wp:docPr) are unique in the document."""
    ids = Counter(e.get("id") for root in stories.values() for e in root.iter(f"{WP}docPr"))
    for drawing, count in sorted(ids.items(), key=lambda item: str(item[0])):
        if count > 1:
            yield f"drawing id {drawing} is used {count} times"


def _ids(root: Element | None, tag: str) -> set[str]:
    if root is None:
        return set()
    return {str(e.get(f"{W}id")) for e in root.iter(f"{W}{tag}")}


def _notes_and_comments(
    stories: dict[str, Element], roots: dict[str, Element | None]
) -> Iterator[str]:
    """Note and comment references name a note or comment that exists."""
    defined = {
        "footnoteReference": ("footnote", _ids(roots.get("/word/footnotes.xml"), "footnote")),
        "endnoteReference": ("endnote", _ids(roots.get("/word/endnotes.xml"), "endnote")),
        "commentReference": ("comment", _ids(roots.get("/word/comments.xml"), "comment")),
        "commentRangeStart": ("comment", _ids(roots.get("/word/comments.xml"), "comment")),
        "commentRangeEnd": ("comment", _ids(roots.get("/word/comments.xml"), "comment")),
    }
    seen: set[tuple[str, str]] = set()
    for name, root in stories.items():
        for tag, (kind, ids) in defined.items():
            for element in root.iter(f"{W}{tag}"):
                value = str(element.get(f"{W}id"))
                if value not in ids and (kind, value) not in seen:
                    seen.add((kind, value))
                    yield f"{_short(name)} refers to {kind} {value}, which does not exist"


def _styles_and_lists(
    stories: dict[str, Element], roots: dict[str, Element | None]
) -> Iterator[str]:
    """Style and list references name a style of the right type and a list that exist."""
    styles = roots.get("/word/styles.xml")
    by_type: dict[str, set[str]] = {}
    if styles is not None:
        for style in styles.iter(f"{W}style"):
            by_type.setdefault(str(style.get(f"{W}type")), set()).add(str(style.get(f"{W}styleId")))
    numbering = roots.get("/word/numbering.xml")
    lists: set[str] = set()
    if numbering is not None:
        lists = {str(e.get(f"{W}numId")) for e in numbering.iter(f"{W}num")}
    seen: set[str] = set()
    for root in [*stories.values(), *([styles] if styles is not None else [])]:
        for tag, kind in _STYLE_REFERENCES.items():
            for element in root.iter(f"{W}{tag}"):
                value = str(element.get(f"{W}val"))
                if value not in by_type.get(kind, set()) and f"{kind} {value}" not in seen:
                    seen.add(f"{kind} {value}")
                    yield f"{kind} style `{value}` is used but not defined"
        for element in root.iter(f"{W}numId"):
            value = str(element.get(f"{W}val"))
            if value != "0" and value not in lists and f"list {value}" not in seen:
                seen.add(f"list {value}")
                yield f"list (numbering) {value} is used but not defined"


# --- math ----------------------------------------------------------------------------------

Slot = tuple[str, int, int | None]
"""A child element of a math object: name, least and most times (None: any number)."""


@cache
def math_sequences() -> dict[str, tuple[Slot, ...]]:
    """Math element name -> its children in order, for the elements whose content is a plain
    sequence of elements in the schema (m:f is m:fPr?, m:num, m:den). Read from the schema,
    which is small enough to read at every build, so the check needs no table of its own."""
    root = etree.parse(MATH_SCHEMA).getroot()
    types: dict[str, tuple[Slot, ...]] = {}
    for complex_type in root.iter(f"{XSD}complexType"):
        children = [c for c in complex_type if isinstance(c.tag, str)]
        if len(children) != 1 or children[0].tag != f"{XSD}sequence":
            continue
        items = [c for c in children[0] if isinstance(c.tag, str)]
        if not items or any(c.tag != f"{XSD}element" or c.get("name") is None for c in items):
            continue
        types[str(complex_type.get("name"))] = tuple(
            (
                str(c.get("name")),
                int(c.get("minOccurs", "1")),
                None if c.get("maxOccurs") == "unbounded" else int(c.get("maxOccurs", "1")),
            )
            for c in items
        )
    declared: dict[str, set[str]] = {}
    for element in root.iter(f"{XSD}element"):
        name, kind = element.get("name"), element.get("type")
        if name is not None and kind is not None:
            declared.setdefault(name, set()).add(kind)
    return {
        name: types[kind]
        for name, kinds in declared.items()
        if len(kinds) == 1 and (kind := next(iter(kinds))) in types
    }


def _math(name: str, root: Element) -> Iterator[str]:
    """Each math object has the arguments the schema requires, in order: an m:sSup without
    m:sup is invalid, and Word may refuse the document. Cheaper than the schemas, so it
    runs at every build (design §3.21)."""
    if root.find(f".//{M}oMath") is None:
        return
    sequences = math_sequences()
    seen: set[str] = set()
    for element in root.iter(f"{M}*"):
        local = etree.QName(element).localname
        slots = sequences.get(local)
        if slots is None:
            continue
        for problem in _sequence_problems(element, local, slots):
            if problem not in seen:
                seen.add(problem)
                yield f"{_short(name)}: {problem}"


def _sequence_problems(element: Element, local: str, slots: tuple[Slot, ...]) -> Iterator[str]:
    position, count = 0, 0
    for child in element:
        if not isinstance(child.tag, str):
            continue
        child_name = etree.QName(child)
        tag = child_name.localname if child_name.namespace == M[1:-1] else None
        while position < len(slots):
            slot, least, most = slots[position]
            if slot == tag and (most is None or count < most):
                count += 1
                break
            if count < least:
                yield f"`m:{local}` has no `m:{slot}`"
            position, count = position + 1, 0
        else:
            yield f"`{_prefixed(child_name)}` is not allowed in `m:{local}` here"
            return
    for slot, least, _ in slots[position:]:
        if count < least:
            yield f"`m:{local}` has no `m:{slot}`"
        count = 0


def _prefixed(name: etree.QName) -> str:
    prefix = next((p for p, uri in PREFIXES.items() if uri == name.namespace), None)
    return f"{prefix}:{name.localname}" if prefix else name.localname
