"""Validate XML against the ECMA-376 Transitional schemas in redocx/data/schemas.

The test suite validates every part it writes. At build time the schemas check the
escape hatches (raw XML from \\raw and ooxml.* options), which nothing else checks, and
whole packages on request (redocx build --validate). The schemas load in about half a
second, so they are loaded only when needed.
"""

from __future__ import annotations

import copy
import re
import zipfile
from functools import cache
from io import BytesIO
from pathlib import Path
from typing import Literal

from lxml import etree

from redocx.ooxml.xml import PREFIXES, E, Element, nsmap, parse_fragment

SCHEMAS = Path(__file__).resolve().parents[1] / "data" / "schemas"
MC = "http://schemas.openxmlformats.org/markup-compatibility/2006"
XML = "http://www.w3.org/XML/1998/namespace"

Context = Literal["block", "inline", "math", "rPr", "pPr", "tblPr", "tcPr"]
"""Where raw XML goes: block-level or run-level content, or a property container."""


@cache
def schema() -> etree.XMLSchema:
    return etree.XMLSchema(etree.parse(SCHEMAS / "redocx.xsd"))


@cache
def understood() -> frozenset[str]:
    """The namespaces the schemas define: what an ECMA-376 reader understands."""
    found: set[str] = set()
    for path in SCHEMAS.rglob("*.xsd"):
        target = etree.parse(path).getroot().get("targetNamespace")
        if target:
            found.add(target)
    return frozenset(found)


def is_xml(name: str) -> bool:
    return name.endswith((".xml", ".rels"))


def process_compatibility(root: Element) -> None:
    """Markup Compatibility (ECMA-376 Part 3) as an ECMA-376 reader applies it: each
    mc:AlternateContent becomes its first Choice whose required namespaces are understood,
    else its Fallback, else nothing. Word 2010+ markup (wps shapes) is in a Choice."""
    known = understood()
    for alternate in list(root.iter(f"{{{MC}}}AlternateContent")):
        chosen: list[Element] = []
        for branch in alternate:
            if branch.tag == f"{{{MC}}}Choice":
                prefixes = (branch.get("Requires") or "").split()
                if all(branch.nsmap.get(prefix) in known for prefix in prefixes):
                    chosen = list(branch)
                    break
            elif branch.tag == f"{{{MC}}}Fallback":
                chosen = list(branch)
        parent = alternate.getparent()
        assert parent is not None
        index = parent.index(alternate)
        parent.remove(alternate)
        for offset, child in enumerate(chosen):
            parent.insert(index + offset, child)
    # mc:Ignorable: elements and attributes of ignorable namespaces the reader does not know
    # are left out (a w14:checkbox, w14:paraId, ...).
    prefixes = (root.get(f"{{{MC}}}Ignorable") or "").split()
    _drop_namespaces(root, {root.nsmap.get(p) for p in prefixes} - known - {None})
    root.attrib.pop(f"{{{MC}}}Ignorable", None)


def _drop_namespaces(root: Element, namespaces: set[str | None]) -> None:
    """Remove the elements and attributes of `namespaces` under `root`."""
    if not namespaces:
        return
    for element in list(root.iter()):
        if not isinstance(element.tag, str):
            continue
        if etree.QName(element).namespace in namespaces:
            parent = element.getparent()
            if parent is not None:
                parent.remove(element)
            continue
        for attr in list(element.attrib):
            if etree.QName(attr).namespace in namespaces:
                del element.attrib[attr]


def validate_part(data: bytes) -> list[str]:
    """Schema errors for one XML part (empty when valid)."""
    root = etree.fromstring(data)
    process_compatibility(root)
    return _errors(root)


def validate_package(docx: bytes) -> dict[str, list[str]]:
    """Part name -> schema errors, for every XML part that has errors."""
    errors: dict[str, list[str]] = {}
    with zipfile.ZipFile(BytesIO(docx)) as archive:
        for name in archive.namelist():
            if is_xml(name):
                part_errors = validate_part(archive.read(name))
                if part_errors:
                    errors[name] = part_errors
    return errors


def _errors(root: Element) -> list[str]:
    xsd = schema()
    if xsd.validate(root):
        return []
    return [f"{e.line}:{e.column}: {e.message}" for e in xsd.error_log]


# --- raw XML -------------------------------------------------------------------------------


def fragment_problems(text: str, context: Context) -> list[str]:
    """Why raw WordprocessingML (already well-formed: see `parse_fragment`) does not fit
    where it goes, in short messages; empty when it does. Elements and attributes of
    namespaces the schemas do not define (w14:, w15:, ...) are not checked."""
    problems: list[str] = []
    for element in parse_fragment(text, math=context == "math"):  # one at a time
        problems += _check(element, context)
    return list(dict.fromkeys(problems))


def _check(element: Element, context: Context) -> list[str]:
    content = copy.deepcopy(element)
    match context:
        case "block":
            body = E("w:body", content)
        case "inline":
            body = E("w:body", E("w:p", content))
        case "math":
            body = E("w:body", E("w:p", E("m:oMath", content)))
        case "rPr":
            body = E("w:body", E("w:p", E("w:r", E("w:rPr", content))))
        case "pPr":
            body = E("w:body", E("w:p", E("w:pPr", content)))
        case "tblPr":
            body = E("w:body", E("w:tbl", E("w:tblPr", content), E("w:tblGrid")))
        case "tcPr":
            cell = E("w:tc", E("w:tcPr", content), E("w:p"))
            body = E("w:body", E("w:tbl", E("w:tblPr"), E("w:tblGrid"), E("w:tr", cell)))
    root = E("w:document", body, nsmap=nsmap("w"))
    used = {etree.QName(e).namespace for e in root.iter() if isinstance(e.tag, str)}
    used |= {etree.QName(a).namespace for e in root.iter() for a in e.attrib}
    _drop_namespaces(root, used - understood() - {None, XML})
    process_compatibility(root)
    xsd = schema()
    if xsd.validate(root):
        return []
    return [_short(e.message) for e in xsd.error_log]


_URIS = {uri: prefix for prefix, uri in reversed(list(PREFIXES.items()))}
_CLARK = re.compile(r"\{([^}]*)\}([\w.-]+)")


def _short(message: str) -> str:
    """lxml's message, shorter: prefixed names, and at most a few expected elements."""
    message = _CLARK.sub(lambda m: f"{_URIS.get(m.group(1), m.group(1))}:{m.group(2)}", message)
    subject = re.match(r"Element '([^']+)'(?:, attribute '([^']+)')?: ", message)
    if subject is None:
        return message.rstrip(".")
    element, attribute = subject.groups()
    rest = re.sub(r"^\[facet '\w+'\] ", "", message[subject.end() :]).rstrip(".")
    where = f"`{attribute}` of `{element}`" if attribute else f"`{element}`"
    if rest.startswith("This element is not expected"):
        expected = re.search(r"Expected is (?:one of )?\( ?(.*?) ?\)", rest)
        names = [n.strip() for n in expected.group(1).split(",")] if expected else []
        shown = ", ".join(names[:6]) + (", ..." if len(names) > 6 else "")
        return f"{where} is not allowed here" + (f" (expected: {shown})" if names else "")
    if rest.startswith("The attribute") and rest.endswith("is not allowed"):
        return f"{where} is not allowed here"
    value = re.match(
        r"(?:The value )?'([^']*)' is not (?:an element of the set|a valid value)", rest
    )
    if value:
        return f"{where}: `{value.group(1)}` is not a valid value"
    return f"{where}: {rest}"
