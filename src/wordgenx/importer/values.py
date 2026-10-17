"""Values as a person writes them in options and style sheets: lengths snapped to round
units, colours, borders, quoted strings, and formatting properties by registry name."""

from __future__ import annotations

import re
from decimal import Decimal

from wordgenx.props import REGISTRY, PropertySet, Target
from wordgenx.values import Border, Color, InvalidValueError, Length, LineSpacing, TabStop

_BARE_RE = re.compile(r"[A-Za-z0-9#.%+-]+(\([0-9]+%\))?")
_NEEDS_QUOTES = frozenset({"on", "off", "true", "false", "yes", "no", "none"})
_POINT = 12_700
_TWIP = 635


def quote(text: str) -> str:
    """`text` as an option or style-sheet value: a bare word when it can be one."""
    if _BARE_RE.fullmatch(text) and text not in _NEEDS_QUOTES:
        return text
    escaped = text.replace("\\", "\\\\").replace('"', '\\"')
    return '"' + re.sub(r"[\r\n]+", " ", escaped) + '"'


def number(value: Decimal) -> str:
    text = f"{value.normalize():f}"
    return "0" if text in ("-0", "0") else text


def length(value: Length, prefer: str = "pt") -> str:
    """A length in round units: points for font sizes and spacing (`prefer="pt"`),
    centimetres for page and indent measures (`prefer="cm"`, within a twip of the value),
    and centimetres to two places for the sizes of tables, pictures and boxes
    (`prefer="size"`, within 0.05mm)."""
    emu = value.emu
    if emu == 0:
        return "0"
    if prefer == "pt" and emu % (_POINT // 2) == 0:  # whole and half points
        return number(Decimal(emu) / _POINT) + "pt"
    if prefer == "size":
        return number(round(Decimal(emu) / 360_000, 2)) + "cm"
    for unit, size, places in (("cm", 360_000, 2), ("in", 914_400, 2)):
        rounded = round(Decimal(emu) / size, places)
        if abs(int(rounded * size) - emu) <= _TWIP:
            return number(rounded) + unit
    return number(round(Decimal(emu) / _POINT, 2)) + "pt"


def color(value: Color) -> str:
    return str(value)


def border(value: Border) -> str:
    if value.style == "nil":
        return "none"
    style = re.sub(r"(?<!^)([A-Z])", r"-\1", value.style).lower()
    parts = [style, length(value.width)]
    if value.color != Color():
        parts.append(color(value.color))
    if value.space.emu:
        parts.append(f"space={length(value.space)}")
    return "(" + ", ".join(parts) + ")"


def line_spacing(value: LineSpacing) -> str:
    if value.multiple is not None:  # as few decimals as keep Word's 240ths of a line
        for places in range(7):
            rounded = round(value.multiple, places)
            if rounded > 0 and LineSpacing.lines(rounded).line_attr == value.line_attr:
                return number(rounded)
        return number(value.multiple)
    assert value.height is not None
    return f"({length(value.height)}, {value.rule})"


def tab_stops(stops: tuple[TabStop, ...]) -> str:
    def stop(tab: TabStop) -> str:
        parts = [length(tab.position, "cm"), tab.align]
        if tab.leader != "none":
            parts.append(f"leader={tab.leader}")
        return "(" + ", ".join(parts) + ")"

    if len(stops) == 1:
        return stop(stops[0])
    return "(" + ", ".join(stop(s) for s in stops) + ")"


_CM_PROPERTIES = frozenset(
    {"indent-left", "indent-right", "first-line-indent", "hanging-indent", "table-indent"}
)


def prop_value(target: Target, name: str, value: object) -> str | None:
    """A property's value as option text; None for a flag that is on (`bold`)."""
    if isinstance(value, bool):
        return None if value else "off"
    if isinstance(value, Border):
        return border(value)
    if isinstance(value, Color):
        return color(value)
    if isinstance(value, Length):
        return length(value, "cm" if name in _CM_PROPERTIES else "pt")
    if isinstance(value, LineSpacing):
        return line_spacing(value)
    if isinstance(value, tuple):
        return tab_stops(value)  # type: ignore[arg-type]
    if isinstance(value, int):
        return f"{value}%" if name == "scale" else str(value)
    text = str(value)
    spec = REGISTRY[target][name]
    words = [w.strip() for w in spec.values.split("|")]
    if len(words) > 1:  # a choice: the word that stands for the OOXML value
        kebab = re.sub(r"(?<!^)([A-Z])", r"-\1", text).lower()
        for word in (kebab, *words):
            try:
                if PropertySet(target, {name: word})[name] == value:
                    return word
            except (InvalidValueError, KeyError):
                continue
    return quote(text)


_BOX_SIDES = {
    Target.TABLE: ("top", "left", "bottom", "right", "inside-h", "inside-v"),
    Target.CELL: ("top", "left", "bottom", "right", "inside-h", "inside-v"),
    Target.PARAGRAPH: ("top", "left", "bottom", "right"),
}
"""The sides `borders` sets for each target."""


def _pairs(
    target: Target, props: PropertySet, skip: frozenset[str]
) -> list[tuple[str, str | None]]:
    """(key, value text) pairs; the same border on every side is one `borders`."""
    names = [n for n in props if n not in skip]
    sides = [f"border-{side}" for side in _BOX_SIDES.get(target, ())]
    out: list[tuple[str, str | None]] = []
    if sides and all(s in names for s in sides) and len({props[s] for s in sides}) == 1:
        out.append(("borders", prop_value(target, sides[0], props[sides[0]])))
        names = [n for n in names if n not in sides]
    out += [(name, prop_value(target, name, props[name])) for name in names]
    return out


def options(target: Target, props: PropertySet) -> list[str]:
    """`key=value` (or `flag`) for every property of `props`."""
    return [
        name if text is None else f"{name}={text}"
        for name, text in _pairs(target, props, frozenset())
    ]


def declarations(
    target: Target, props: PropertySet, skip: frozenset[str] = frozenset()
) -> list[str]:
    """`key: value;` (or `flag;`) for a style-sheet rule."""
    return [
        f"{name};" if text is None else f"{name}: {text};"
        for name, text in _pairs(target, props, skip)
    ]
