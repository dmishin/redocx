"""A small element builder over lxml for writing OOXML parts.

    E("w:p", E("w:pPr", E("w:jc", {"w:val": "center"})), E("w:r", E("w:t", text="Hi")))

Tags and attribute names use the conventional prefixes (w:, r:, a:, ...). Only the
part root declares namespaces (``nsmap=``); when a subtree is attached, lxml maps its
elements onto the root's declarations, so output uses the conventional prefixes.
"""

from __future__ import annotations

from collections.abc import Mapping

from lxml import etree

from redocx.ooxml.constants import NS
from redocx.ooxml.opc import XML_DECLARATION

PREFIXES: dict[str, str] = {
    "w": NS.W,
    "r": NS.R,
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "wp": "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing",
    "pic": "http://schemas.openxmlformats.org/drawingml/2006/picture",
    "m": "http://schemas.openxmlformats.org/officeDocument/2006/math",
    "asvg": "http://schemas.microsoft.com/office/drawing/2016/SVG/main",
    "mc": "http://schemas.openxmlformats.org/markup-compatibility/2006",
    "wps": "http://schemas.microsoft.com/office/word/2010/wordprocessingShape",
    "v": "urn:schemas-microsoft-com:vml",
    "o": "urn:schemas-microsoft-com:office:office",
    "w10": "urn:schemas-microsoft-com:office:word",
    "w14": "http://schemas.microsoft.com/office/word/2010/wordml",
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


_MATH = RAW_NAMESPACES["m"]
_EQUATIONS = frozenset({f"{{{_MATH}}}oMath", f"{{{_MATH}}}oMathPara"})


def parse_fragment(text: str, *, math: bool = False) -> list[Element]:
    """Parse raw WordprocessingML written by hand: elements with the usual prefixes (w:,
    r:, a:, m:, ...), which need not be declared. The top-level elements are w: elements or
    whole equations (m:oMath, m:oMathPara); with `math`, OMML elements (m:) for inside an
    equation. Raises ValueError with lxml's message."""
    declarations = " ".join(f'xmlns:{p}="{u}"' for p, u in RAW_NAMESPACES.items())
    try:
        root = etree.fromstring(f"<rdx-raw {declarations}>{text}</rdx-raw>".encode())
    except etree.XMLSyntaxError as e:
        raise ValueError(f"not well-formed XML: {e.msg}") from None
    inside = "m:t" if math else "w:t"
    if (root.text or "").strip() or any((child.tail or "").strip() for child in root):
        raise ValueError(f"raw XML must be elements only (text belongs inside {inside})")
    elements = [child for child in root if isinstance(child.tag, str)]
    for element in elements:
        tag = str(element.tag)
        if math and not tag.startswith(f"{{{_MATH}}}"):
            raise ValueError(f"OMML must be math (m:) elements, got {_prefixed(tag)}")
        if not math and not tag.startswith(f"{{{NS.W}}}") and tag not in _EQUATIONS:
            raise ValueError(
                "raw XML must be WordprocessingML (w:) elements or whole equations "
                f"(m:oMath, m:oMathPara), got {_prefixed(tag)}"
            )
    return elements


def _prefixed(tag: str) -> str:
    """`{uri}local` as `prefix:local` when the prefix is one raw XML may use."""
    uri, _, local = tag[1:].partition("}")
    prefix = next((p for p, u in RAW_NAMESPACES.items() if u == uri), None)
    return f"{prefix}:{local}" if prefix else tag


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
