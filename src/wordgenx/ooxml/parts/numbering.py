"""The numbering part (word/numbering.xml): multilevel list definitions.

Each IR `Numbering` becomes one w:abstractNum with all nine levels (Word expects
them; undefined levels get defaults that continue the pattern) and one w:num
instance that paragraphs and styles refer to.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace

from wordgenx.ir import Numbering, NumberingLevel
from wordgenx.ooxml.parts.properties import paragraph_properties, run_properties
from wordgenx.ooxml.schema_order import ABSTRACT_NUM, LVL, NUMBERING, ordered
from wordgenx.ooxml.style_names import style_id
from wordgenx.ooxml.xml import E, Element, nsmap
from wordgenx.props import EmitContext, para_props
from wordgenx.values import Length

_INDENT_STEP = Length.twips(360)  # Word's default: 0.25" per level, 0.25" hanging
_BULLETS = ("•", "◦", "▪")


def numbering_ids(definitions: Sequence[Numbering], offset: int = 0) -> dict[str, int]:
    """Numbering name -> w:numId (offset + 1, 2, ...; a template's own ids come first)."""
    return {numbering.name: offset + i + 1 for i, numbering in enumerate(definitions)}


def default_level(numbering: Numbering, level: int) -> NumberingLevel:
    """A level the definition leaves out, continuing the pattern of the levels above it."""
    first = numbering.level(1)
    if first is not None and first.format == "bullet":
        return NumberingLevel(level, "bullet", _BULLETS[(level - 1) % len(_BULLETS)])
    above = next(
        (lvl for k in range(level - 1, 0, -1) if (lvl := numbering.level(k)) is not None), None
    )
    if above is not None:
        chain = ".".join(f"%{i}" for i in range(1, above.level + 1))
        if above.text in (chain, chain + "."):  # outline style: 1, 1.1, 1.1.1 ...
            suffix = "." if above.text.endswith(".") else ""
            text = ".".join(f"%{i}" for i in range(1, level + 1)) + suffix
            return NumberingLevel(level, "decimal", text, legal=above.legal)
    return NumberingLevel(level, "decimal", f"%{level}.")


def complete_levels(numbering: Numbering) -> list[NumberingLevel]:
    """All nine levels, with defaults for undefined ones and default indents where unset."""
    levels: list[NumberingLevel] = []
    for number in range(1, 10):
        level = numbering.level(number) or default_level(numbering, number)
        if "indent-left" not in level.paragraph and "hanging-indent" not in level.paragraph:
            indent = para_props(
                indent_left=Length(_INDENT_STEP.emu * number), hanging_indent=_INDENT_STEP
            )
            level = replace(level, paragraph=indent.merged(level.paragraph))
        levels.append(level)
    return levels


def _level(level: NumberingLevel, ctx: EmitContext) -> Element:
    def val(tag: str, value: str) -> Element:
        return E(tag, {"w:val": value})

    children: dict[str, Element | None] = {
        "start": val("w:start", str(level.start)),
        "numFmt": val("w:numFmt", level.format),
        "lvlRestart": val("w:lvlRestart", str(level.restart_after))
        if level.restart_after is not None
        else None,
        "pStyle": val("w:pStyle", style_id(level.style)) if level.style else None,
        "isLgl": E("w:isLgl") if level.legal else None,
        "suff": val("w:suff", level.suffix) if level.suffix != "tab" else None,
        "lvlText": val("w:lvlText", level.text),
        "lvlJc": val("w:lvlJc", level.marker_align),
        "pPr": paragraph_properties(level.paragraph, ctx),
        "rPr": run_properties(level.run, ctx),
    }
    present = {key: value for key, value in children.items() if value is not None}
    return E("w:lvl", {"w:ilvl": str(level.level - 1)}, *ordered(present, LVL))


@dataclass(frozen=True)
class ListInstance:
    """A separately counted list: its own w:num restarting the definition's numbering."""

    num_id: int
    numbering: str
    instance: int
    start: int | None


def list_num_ids(
    definitions: Sequence[Numbering],
    used: Iterable[tuple[str, int, int | None]],
    offset: int = 0,
) -> list[ListInstance]:
    """Allocate w:numIds for list instances (numbering, instance, start) in first-use order.

    Ids 1..len(definitions) belong to the definitions themselves (used by styles).
    """
    instances: dict[tuple[str, int], ListInstance] = {}
    next_id = offset + len(definitions) + 1
    for name, instance, start in used:
        if (name, instance) not in instances:
            instances[(name, instance)] = ListInstance(next_id, name, instance, start)
            next_id += 1
    return list(instances.values())


def numbering_xml(
    definitions: Sequence[Numbering],
    ctx: EmitContext,
    instances: Sequence[ListInstance] = (),
    offsets: tuple[int, int] = (0, 0),
) -> Element:
    """`offsets` (numId, abstractNumId) shift the ids past a template's own."""
    num_offset, abstract_offset = offsets
    abstract: list[Element] = []
    nums: list[Element] = []
    for index, numbering in enumerate(definitions):
        num_id, abstract_id = num_offset + index + 1, abstract_offset + index
        abstract_children = {
            "multiLevelType": E("w:multiLevelType", {"w:val": "multilevel"}),
            "name": E("w:name", {"w:val": numbering.name}),
        }
        abstract.append(
            E(
                "w:abstractNum",
                {"w:abstractNumId": str(abstract_id)},
                *ordered(abstract_children, ABSTRACT_NUM),
                *(_level(level, ctx) for level in complete_levels(numbering)),
            )
        )
        nums.append(
            E(
                "w:num",
                {"w:numId": str(num_id)},
                E("w:abstractNumId", {"w:val": str(abstract_id)}),
            )
        )
    abstract_ids = {numbering.name: abstract_offset + i for i, numbering in enumerate(definitions)}
    for instance in instances:
        definition = definitions[abstract_ids[instance.numbering] - abstract_offset]
        first = definition.level(1)
        start = instance.start if instance.start is not None else (first.start if first else 1)
        nums.append(
            E(
                "w:num",
                {"w:numId": str(instance.num_id)},
                E("w:abstractNumId", {"w:val": str(abstract_ids[instance.numbering])}),
                E(
                    "w:lvlOverride",
                    {"w:ilvl": "0"},
                    E("w:startOverride", {"w:val": str(start)}),
                ),
            )
        )
    groups = {"abstractNum": abstract, "num": nums}
    return E(
        "w:numbering",
        *(child for name in NUMBERING if name in groups for child in groups[name]),
        nsmap=nsmap("w"),
    )
