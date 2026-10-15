"""The styles part (word/styles.xml)."""

from __future__ import annotations

from collections.abc import Mapping

from wordgenx.ir import Document, Style, StyleKind
from wordgenx.ooxml.parts.numbering import numbering_ids
from wordgenx.ooxml.parts.properties import paragraph_properties, run_properties
from wordgenx.ooxml.schema_order import STYLE, ordered
from wordgenx.ooxml.style_names import UI_PRIORITY, is_builtin, stored_name, style_id
from wordgenx.ooxml.xml import E, Element, nsmap
from wordgenx.props import EmitContext


class StyleError(ValueError):
    """An invalid style definition or reference."""


# The four defaults every Word document has: default paragraph, character, table
# and numbering styles. Users may redefine Normal; the others are fixed.
_NORMAL = Style(StyleKind.PARAGRAPH, "Normal", based_on=None)


def _base_styles() -> list[Element]:
    return [
        E(
            "w:style",
            {"w:type": "character", "w:default": "1", "w:styleId": "DefaultParagraphFont"},
            E("w:name", {"w:val": "Default Paragraph Font"}),
            E("w:uiPriority", {"w:val": "1"}),
            E("w:semiHidden"),
            E("w:unhideWhenUsed"),
        ),
        E(
            "w:style",
            {"w:type": "table", "w:default": "1", "w:styleId": "TableNormal"},
            E("w:name", {"w:val": "Normal Table"}),
            E("w:uiPriority", {"w:val": "99"}),
            E("w:semiHidden"),
            E("w:unhideWhenUsed"),
            E(
                "w:tblPr",
                E("w:tblInd", {"w:w": "0", "w:type": "dxa"}),
                E(
                    "w:tblCellMar",
                    E("w:top", {"w:w": "0", "w:type": "dxa"}),
                    E("w:left", {"w:w": "108", "w:type": "dxa"}),
                    E("w:bottom", {"w:w": "0", "w:type": "dxa"}),
                    E("w:right", {"w:w": "108", "w:type": "dxa"}),
                ),
            ),
        ),
        E(
            "w:style",
            {"w:type": "numbering", "w:default": "1", "w:styleId": "NoList"},
            E("w:name", {"w:val": "No List"}),
            E("w:uiPriority", {"w:val": "99"}),
            E("w:semiHidden"),
            E("w:unhideWhenUsed"),
        ),
    ]


def resolve_styles(document: Document) -> dict[str, Style]:
    """Style ID -> style for all paragraph/character styles the document defines (incl. Normal)."""
    styles: dict[str, Style] = {"Normal": _NORMAL}
    seen: set[str] = set()
    for style in document.styles:
        sid = style_id(style.name)
        if sid in seen:
            raise StyleError(f"style {style.name!r} is defined twice (style ID {sid!r})")
        seen.add(sid)
        if sid == "Normal" and style.kind is not StyleKind.PARAGRAPH:
            raise StyleError("Normal must be a paragraph style")
        if sid in ("DefaultParagraphFont", "TableNormal", "NoList"):
            raise StyleError(f"{style.name!r} is a fixed default style and cannot be redefined")
        styles[sid] = style

    for style in styles.values():
        for kind, ref in (("based-on", style.based_on), ("next", style.next)):
            if ref is None:
                continue
            target = styles.get(style_id(ref))
            if target is None:
                raise StyleError(f"style {style.name!r}: {kind} style {ref!r} is not defined")
            if target.kind is not style.kind:
                raise StyleError(f"style {style.name!r}: {kind} style {ref!r} is a different kind")
        if style.link is not None:
            linked = styles.get(style_id(style.link))
            if linked is None:
                raise StyleError(
                    f"style {style.name!r}: linked style {style.link!r} is not defined"
                )
            if linked.kind is style.kind:
                raise StyleError(f"style {style.name!r}: a linked style must be of the other kind")
    _check_cycles(styles)
    return styles


def _check_cycles(styles: dict[str, Style]) -> None:
    for start in styles:
        seen = {start}
        current = styles[start].based_on
        while current is not None:
            sid = style_id(current)
            if sid in seen:
                raise StyleError(f"style {styles[start].name!r} has a cyclic based-on chain")
            seen.add(sid)
            current = styles[sid].based_on


def _table_grid() -> Element:
    """Word's built-in "Table Grid": single thin borders around and between all cells."""
    border = {"w:val": "single", "w:sz": "4", "w:space": "0", "w:color": "auto"}
    return E(
        "w:style",
        {"w:type": "table", "w:styleId": "TableGrid"},
        E("w:name", {"w:val": "Table Grid"}),
        E("w:basedOn", {"w:val": "TableNormal"}),
        E("w:uiPriority", {"w:val": "39"}),
        E("w:pPr", E("w:spacing", {"w:after": "0", "w:line": "240", "w:lineRule": "auto"})),
        E(
            "w:tblPr",
            E(
                "w:tblBorders",
                *(
                    E(f"w:{side}", border)
                    for side in ("top", "left", "bottom", "right", "insideH", "insideV")
                ),
            ),
        ),
    )


def _style(style: Style, sid: str, ctx: EmitContext, num_ids: Mapping[str, int]) -> Element:
    if style.kind is StyleKind.TABLE:
        return _table_grid()
    name = stored_name(style.name)
    attrs = {"w:type": style.kind.value, "w:styleId": sid}
    if sid == "Normal":
        attrs["w:default"] = "1"
    if not is_builtin(name):
        attrs["w:customStyle"] = "1"
    priority = style.ui_priority if style.ui_priority is not None else UI_PRIORITY.get(name)
    numbering = None
    if style.numbering is not None:
        numbering = (num_ids[style.numbering.name], style.numbering.level - 1)

    children: dict[str, Element | None] = {
        "name": E("w:name", {"w:val": name}),
        "basedOn": E("w:basedOn", {"w:val": style_id(style.based_on)}) if style.based_on else None,
        "next": E("w:next", {"w:val": style_id(style.next)}) if style.next else None,
        "link": E("w:link", {"w:val": style_id(style.link)}) if style.link else None,
        "uiPriority": E("w:uiPriority", {"w:val": str(priority)}) if priority else None,
        "qFormat": E("w:qFormat") if style.quick_format else None,
        "pPr": paragraph_properties(style.paragraph, ctx, numbering=numbering),
        "rPr": run_properties(style.run, ctx),
    }
    present = {key: value for key, value in children.items() if value is not None}
    return E("w:style", attrs, *ordered(present, STYLE))


def styles_xml(document: Document, ctx: EmitContext) -> Element:
    styles = resolve_styles(document)
    rpr = run_properties(document.default_run, ctx)
    ppr = paragraph_properties(document.default_paragraph, ctx)
    doc_defaults = E(
        "w:docDefaults",
        E("w:rPrDefault", rpr) if rpr is not None else None,
        E("w:pPrDefault", ppr) if ppr is not None else None,
    )
    ids = numbering_ids(document.numbering)
    normal = _style(styles.pop("Normal"), "Normal", ctx, ids)
    return E(
        "w:styles",
        doc_defaults,
        normal,
        *_base_styles(),
        *(_style(style, sid, ctx, ids) for sid, style in styles.items()),
        nsmap=nsmap("w"),
    )
