"""The main document part (word/document.xml)."""

from __future__ import annotations

import re
from collections.abc import Iterator

from wordgenx.ir import Document, PageSetup, Paragraph, Run
from wordgenx.ooxml.parts.properties import paragraph_properties, run_properties
from wordgenx.ooxml.schema_order import SECT_PR, ordered
from wordgenx.ooxml.style_names import style_id
from wordgenx.ooxml.xml import E, Element, nsmap
from wordgenx.props import EmitContext

_SPLIT_RE = re.compile(r"(\t|\n)")


def _run_content(text: str) -> Iterator[Element]:
    for piece in _SPLIT_RE.split(text):
        if piece == "\t":
            yield E("w:tab")
        elif piece == "\n":
            yield E("w:br")
        elif piece:
            preserve = piece != piece.strip() or "  " in piece
            yield E("w:t", {"xml:space": "preserve"} if preserve else None, text=piece)


def _run(run: Run, ctx: EmitContext) -> Element:
    style = style_id(run.style) if run.style else None
    return E("w:r", run_properties(run.props, ctx, style_id=style), *_run_content(run.text))


def _paragraph(paragraph: Paragraph, ctx: EmitContext) -> Element:
    style = style_id(paragraph.style) if paragraph.style else None
    return E(
        "w:p",
        paragraph_properties(paragraph.props, ctx, style_id=style),
        *(_run(run, ctx) for run in paragraph.content),
    )


def _section_properties(page: PageSetup) -> Element:
    size = {"w:w": str(page.width.in_twips), "w:h": str(page.height.in_twips)}
    if page.landscape:
        size["w:orient"] = "landscape"
    children = {
        "pgSz": E("w:pgSz", size),
        "pgMar": E(
            "w:pgMar",
            {
                "w:top": str(page.margin_top.in_twips),
                "w:right": str(page.margin_right.in_twips),
                "w:bottom": str(page.margin_bottom.in_twips),
                "w:left": str(page.margin_left.in_twips),
                "w:header": str(page.header_distance.in_twips),
                "w:footer": str(page.footer_distance.in_twips),
                "w:gutter": str(page.gutter.in_twips),
            },
        ),
        "cols": E("w:cols", {"w:space": "708"}),
        "docGrid": E("w:docGrid", {"w:linePitch": "360"}),
    }
    return E("w:sectPr", *ordered(children, SECT_PR))


def document_xml(document: Document, ctx: EmitContext) -> Element:
    return E(
        "w:document",
        E(
            "w:body",
            *(_paragraph(p, ctx) for p in document.body),
            _section_properties(document.page),
        ),
        nsmap=nsmap("w", "r"),
    )
