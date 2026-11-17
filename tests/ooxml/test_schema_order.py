"""The hand-copied schema order lists must match wml.xsd exactly."""

from collections.abc import Sequence
from functools import cache

import pytest
from lxml import etree

from redocx.ooxml import schema_order
from redocx.props import REGISTRY, Target
from tests.support.xsd import SCHEMAS

XS = "{http://www.w3.org/2001/XMLSchema}"


@cache
def _wml() -> etree._Element:  # pyright: ignore[reportPrivateUsage]
    return etree.parse(SCHEMAS / "ecma-376-transitional" / "wml.xsd").getroot()


def _children(kind: str, name: str) -> list[str]:
    node = _wml().find(f"{XS}{kind}[@name='{name}']")
    assert node is not None, name
    names: list[str] = []
    for element in node.iter(f"{XS}element"):
        local = element.get("name") or element.get("ref", "").split(":")[-1]
        names.append(local)
    return names


@pytest.mark.parametrize(
    ("ours", "kind", "name"),
    [
        (schema_order.RPR, "group", "EG_RPrBase"),
        (schema_order.PPR, "complexType", "CT_PPrBase"),
        (schema_order.STYLE, "complexType", "CT_Style"),
        (schema_order.SECT_PR, "group", "EG_SectPrContents"),
        (schema_order.SETTINGS, "complexType", "CT_Settings"),
        (schema_order.LVL, "complexType", "CT_Lvl"),
        (schema_order.TBL_PR, "complexType", "CT_TblPrBase"),
        (schema_order.TR_PR, "complexType", "CT_TrPrBase"),
        (schema_order.TC_PR, "complexType", "CT_TcPrBase"),
        (schema_order.TBL_BORDERS, "complexType", "CT_TblBorders"),
        (schema_order.ABSTRACT_NUM, "complexType", "CT_AbstractNum"),
        (schema_order.NUMBERING, "complexType", "CT_Numbering"),
        (schema_order.NUM_PR, "complexType", "CT_NumPr"),
    ],
)
def test_order_matches_schema(ours: Sequence[str], kind: str, name: str) -> None:
    assert list(ours) == _children(kind, name)


def test_registry_elements_are_known() -> None:
    """Every element the registry emits appears in its container's order list."""
    from redocx.ooxml.parts.theme import Theme
    from redocx.values import Color

    samples: dict[str, object] = {"color": Color(rgb="000000"), "shading": Color(rgb="F2F2F2")}
    containers = (
        (Target.RUN, schema_order.RPR),
        (Target.PARAGRAPH, schema_order.PPR),
        (Target.TABLE, schema_order.TBL_PR),
        (Target.CELL, schema_order.TC_PR),
    )
    for target, order in containers:
        for spec in REGISTRY[target].values():
            value = samples.get(spec.name)
            if value is None:
                value = _sample(spec.name, target)
            for element, _ in spec.emit(spec.coerce(value), Theme()):
                assert element in order, (spec.name, element)


def _sample(name: str, target: Target) -> object:
    from redocx.values import LineSpacing

    special: dict[str, object] = {
        "font": "Arial",
        "size": "11pt",
        "lang": "en-US",
        "highlight": "yellow",
        "border": "single 0.5pt",
        "font-cs": "Arial",
        "font-east-asian": "MS Mincho",
        "underline": "single",
        "vertical-align": "superscript",
        "align": "center",
        "outline-level": 1,
        "line-spacing": LineSpacing.lines(1),
        "tabs": "8cm center",
        "table-align": "center",
        "valign": "center",
        "scale": "90%",
        "kerning": "12pt",
        "text-direction": "up",
    }
    if name in special:
        return special[name]
    spec = REGISTRY[target][name]
    if name.startswith("ooxml."):
        return (
            "<w:snapToGrid w:val='0'/>"
            if target is Target.PARAGRAPH
            else {
                Target.RUN: "<w:noProof/>",
                Target.TABLE: "<w:tblCaption w:val='x'/>",
                Target.CELL: "<w:noWrap/>",
            }[target]
        )
    if name.startswith("border-"):
        return "(single, 0.5pt)"
    return "1pt" if "length" in spec.values else True
