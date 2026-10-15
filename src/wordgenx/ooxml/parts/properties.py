"""Emit w:rPr / w:pPr elements from property sets, in schema order."""

from __future__ import annotations

from wordgenx.ooxml.schema_order import PPR, RPR, ordered
from wordgenx.ooxml.xml import E, Element
from wordgenx.props import EmitContext, PropertySet, Target


def _children(props: PropertySet, ctx: EmitContext) -> dict[str, Element]:
    return {
        name: E(f"w:{name}", {f"w:{key}": value for key, value in attrs.items()})
        for name, attrs in props.emit(ctx).items()
    }


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
    props: PropertySet, ctx: EmitContext, *, style_id: str | None = None
) -> Element | None:
    """A w:pPr element, or None when there is nothing to say."""
    assert props.target is Target.PARAGRAPH
    children = _children(props, ctx)
    if style_id is not None:
        children["pStyle"] = E("w:pStyle", {"w:val": style_id})
    return E("w:pPr", *ordered(children, PPR)) if children else None
