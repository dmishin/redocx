"""A small element builder over lxml for writing OOXML parts.

    E("w:p", E("w:pPr", E("w:jc", {"w:val": "center"})), E("w:r", E("w:t", text="Hi")))

Tags and attribute names use the conventional prefixes (w:, r:, a:, ...). Only the
part root declares namespaces (``nsmap=``); when a subtree is attached, lxml maps its
elements onto the root's declarations, so output uses the conventional prefixes.
"""

from __future__ import annotations

from collections.abc import Mapping

from lxml import etree

from wordgenx.ooxml.constants import NS
from wordgenx.ooxml.opc import XML_DECLARATION

PREFIXES: dict[str, str] = {
    "w": NS.W,
    "r": NS.R,
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "wp": "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing",
    "pic": "http://schemas.openxmlformats.org/drawingml/2006/picture",
    "cp": NS.CP,
    "dc": NS.DC,
    "dcterms": NS.DCTERMS,
    "xsi": NS.XSI,
    "ep": "http://schemas.openxmlformats.org/officeDocument/2006/extended-properties",
    "vt": "http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes",
    "xml": "http://www.w3.org/XML/1998/namespace",
}

Element = etree._Element  # pyright: ignore[reportPrivateUsage]


RAW_NAMESPACES = {
    **{prefix: uri for prefix, uri in PREFIXES.items() if prefix not in ("xml",)},
    "w14": "http://schemas.microsoft.com/office/word/2010/wordml",
    "mc": "http://schemas.openxmlformats.org/markup-compatibility/2006",
    "v": "urn:schemas-microsoft-com:vml",
    "o": "urn:schemas-microsoft-com:office:office",
    "m": "http://schemas.openxmlformats.org/officeDocument/2006/math",
}
"""Prefixes raw XML (\\raw, ooxml.* options) may use without declaring them."""


def parse_fragment(text: str) -> list[Element]:
    """Parse raw WordprocessingML written by hand: elements with the usual prefixes (w:,
    r:, a:, ...), which need not be declared. Raises ValueError with lxml's message."""
    declarations = " ".join(f'xmlns:{p}="{u}"' for p, u in RAW_NAMESPACES.items())
    try:
        root = etree.fromstring(f"<wgx-raw {declarations}>{text}</wgx-raw>".encode())
    except etree.XMLSyntaxError as e:
        raise ValueError(f"not well-formed XML: {e.msg}") from None
    if (root.text or "").strip() or any((child.tail or "").strip() for child in root):
        raise ValueError("raw XML must be elements only (text belongs inside w:t)")
    elements = [child for child in root if isinstance(child.tag, str)]
    for element in elements:
        if not str(element.tag).startswith(f"{{{NS.W}}}"):
            raise ValueError(f"raw XML must be WordprocessingML (w:) elements, got {element.tag}")
    return elements


def qn(name: str) -> str:
    """Clark notation for a prefixed name: ``w:p`` -> ``{http://...main}p``."""
    prefix, _, local = name.partition(":")
    if not local:
        raise ValueError(f"name must be prefixed: {name!r}")
    return f"{{{PREFIXES[prefix]}}}{local}"


def E(  # noqa: N802 - conventional name for an element factory
    tag: str,
    *children: Element | Mapping[str, str] | None,
    text: str | None = None,
    nsmap: Mapping[str | None, str] | None = None,
) -> Element:
    """Create an element. Positional mappings are attributes; None children are skipped."""
    element = etree.Element(qn(tag), nsmap=dict(nsmap) if nsmap else None)
    for child in children:
        if child is None:
            continue
        if isinstance(child, Mapping):
            for key, value in child.items():
                # Unprefixed attribute names are unqualified (as in DrawingML).
                element.set(qn(key) if ":" in key else key, value)
        else:
            element.append(child)
    if text is not None:
        element.text = text
    return element


def nsmap(*prefixes: str) -> dict[str | None, str]:
    """The namespace map for a part root declaring `prefixes`."""
    return {prefix: PREFIXES[prefix] for prefix in prefixes}


def serialize(root: Element) -> bytes:
    """Serialize a part root with the standard XML declaration."""
    return XML_DECLARATION.encode() + etree.tostring(root, encoding="UTF-8")
