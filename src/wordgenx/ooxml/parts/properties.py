"""Emit w:rPr / w:pPr / w:tblPr / w:tcPr elements from property sets, in schema order."""

from __future__ import annotations

from collections.abc import Mapping

from wordgenx.ooxml.schema_order import NUM_PR, PPR, RPR, TBL_PR, TC_PR, ordered
from wordgenx.ooxml.xml import E, Element, parse_fragment
from wordgenx.props import EmitContext, PropertySet, Target


def _children(props: PropertySet, ctx: EmitContext) -> dict[str, Element]:
    nested = props.emit_children(ctx)
    children = {
        name: E(
            f"w:{name}",
            {f"w:{key}": value for key, value in attrs.items()},
            *(
                E(f"w:{child}", {f"w:{k}": v for k, v in child_attrs.items()})
                for child, child_attrs in nested.get(name, [])
            ),
        )
        for name, attrs in props.emit(ctx).items()
    }
    raw = props.get(f"ooxml.{props.target.value}")
    if isinstance(raw, str):  # the escape hatch: replaces what wordgenx wrote
        for element in parse_fragment(raw):
            children[str(element.tag).rsplit("}", 1)[-1]] = element
    return children


def run_properties(
    props: PropertySet, ctx: EmitContext, *, style_id: str | None = None
) -> Element | None:
    """A w:rPr element, or None when there is nothing to say."""
    assert props.target is Target.RUN
    children = _children(props, ctx)
    if style_id is not None:
        children["rStyle"] = E("w:rStyle", {"w:val": style_id})
    return E("w:rPr", *ordered(children, RPR)) if children else None


def paragraph_properties(
    props: PropertySet,
    ctx: EmitContext,
    *,
    style_id: str | None = None,
    numbering: tuple[int, int] | None = None,
    extra: Mapping[str, Element] | None = None,
) -> Element | None:
    """A w:pPr element, or None when there is nothing to say.

    `numbering` is (numId, 0-based level) for numbered paragraphs and styles; `extra` adds
    elements by name (a drop cap's w:framePr).
    """
    assert props.target is Target.PARAGRAPH
    children = _children(props, ctx)
    children.update(extra or {})
    if style_id is not None:
        children["pStyle"] = E("w:pStyle", {"w:val": style_id})
    if numbering is not None:
        num_id, ilvl = numbering
        children["numPr"] = E(
            "w:numPr",
            *ordered(
                {
                    "ilvl": E("w:ilvl", {"w:val": str(ilvl)}),
                    "numId": E("w:numId", {"w:val": str(num_id)}),
                },
                NUM_PR,
            ),
        )
    return E("w:pPr", *ordered(children, PPR)) if children else None


def table_properties(
    props: PropertySet, ctx: EmitContext, structure: dict[str, Element] | None = None
) -> Element | None:
    """A w:tblPr element: `structure` (w:tblW, w:tblLook, ...) plus the properties."""
    assert props.target is Target.TABLE
    children = {**(structure or {}), **_children(props, ctx)}
    return E("w:tblPr", *ordered(children, TBL_PR)) if children else None


def cell_properties(
    props: PropertySet, ctx: EmitContext, structure: dict[str, Element] | None = None
) -> Element | None:
    """A w:tcPr element: `structure` (w:tcW, w:gridSpan, w:vMerge) plus the properties."""
    assert props.target is Target.CELL
    children = {**(structure or {}), **_children(props, ctx)}
    return E("w:tcPr", *ordered(children, TC_PR)) if children else None
