"""The property registry: the single definition of every formatting property.

Each `PropSpec` says what a property is called in wordgenx, what values it takes,
and which WordprocessingML element(s) it becomes. Style sheets, paragraph and
run options, the OOXML emitter, documentation and (later) LSP completion are all
driven from this table, so adding a Word property means adding one entry here.
"""

from __future__ import annotations

import difflib
import re
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Protocol

from wordgenx.values import (
    TAB_LEADERS,
    Border,
    Color,
    InvalidValueError,
    Length,
    LineSpacing,
    TabStop,
    apply_tint_shade,
)


class Target(Enum):
    """Which property container a property belongs to."""

    RUN = "rPr"
    PARAGRAPH = "pPr"
    TABLE = "tblPr"
    CELL = "tcPr"

    @property
    def label(self) -> str:
        return {"rPr": "run", "pPr": "paragraph", "tblPr": "table", "tcPr": "cell"}[self.value]


class EmitContext(Protocol):
    """What emitters may ask about the document being written."""

    def theme_color_rgb(self, name: str) -> str: ...


Attrs = dict[str, str]
"""Attributes of one element, keyed by local name in the w: namespace."""

Emitted = Iterable[tuple[str, Attrs]]
"""(element local name, attributes) pairs; same-named elements are merged."""


@dataclass(frozen=True)
class PropSpec:
    name: str
    target: Target
    coerce: Callable[[object], object]
    emit: Callable[[object, EmitContext], Emitted]
    doc: str
    values: str
    """Human-readable description of accepted values (docs, error messages)."""
    children: Callable[[object, EmitContext], list[tuple[str, Attrs]]] | None = None
    """Child elements of the emitted element (w:tab inside w:tabs, w:top inside w:tblBorders).
    Properties emitting the same element contribute children in registry order."""


class UnknownPropertyError(KeyError):
    def __init__(self, name: str, target: Target, suggestion: str | None) -> None:
        hint = f"; did you mean `{suggestion}`?" if suggestion else ""
        super().__init__(f"unknown {target.label} property `{name}`{hint}")
        self.name = name
        self.suggestion = suggestion

    def __str__(self) -> str:
        return str(self.args[0])


# --- value coercers ------------------------------------------------------------


def _bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value in ("true", "on", "yes"):
        return True
    if isinstance(value, str) and value in ("false", "off", "no"):
        return False
    raise InvalidValueError(f"expected a boolean (true/false), got {value!r}")


def _length(value: object) -> Length:
    if isinstance(value, Length):
        return value
    if isinstance(value, str):
        return Length.parse(value)
    raise InvalidValueError(f"expected a length such as 12pt, got {value!r}")


def _nonnegative_length(value: object) -> Length:
    length = _length(value)
    if length.emu < 0:
        raise InvalidValueError(f"length must not be negative: {length}")
    return length


def _color(value: object) -> Color:
    if isinstance(value, Color):
        return value
    if isinstance(value, str):
        return Color.parse(value)
    raise InvalidValueError(f"expected a color (#RRGGBB, auto, theme.<name>), got {value!r}")


def _font(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise InvalidValueError(f"expected a font name, got {value!r}")
    if value.startswith("theme.") and value not in ("theme.major", "theme.minor"):
        raise InvalidValueError(f"unknown theme font {value!r}; use theme.major or theme.minor")
    return value


def _font_size(value: object) -> Length:
    size = _length(value)
    if not 1 <= size.in_half_points <= 3276:
        raise InvalidValueError(f"font size out of range (0.5pt-1638pt): {size}")
    return size


_LANG_RE = re.compile(r"[a-zA-Z]{2,3}(-[a-zA-Z0-9]{2,8})*")


def _lang(value: object) -> str:
    if not isinstance(value, str) or not _LANG_RE.fullmatch(value):
        raise InvalidValueError(f"expected a language tag such as en-US, got {value!r}")
    return value


def _line_spacing(value: object) -> LineSpacing:
    if isinstance(value, LineSpacing):
        return value
    if isinstance(value, int | float) and not isinstance(value, bool):
        return LineSpacing.lines(value)
    if isinstance(value, str):
        try:
            return LineSpacing.lines(value)
        except ArithmeticError:
            pass
    raise InvalidValueError(
        f"expected a line multiple such as 1.15 or a LineSpacing, got {value!r}"
    )


def _percent(value: object) -> int:
    text = value.rstrip("%") if isinstance(value, str) else value
    if isinstance(text, str) and text.isdigit():
        text = int(text)
    if isinstance(text, int) and not isinstance(text, bool) and 1 <= text <= 600:
        return text
    raise InvalidValueError(f"expected a percentage 1%-600%, got {value!r}")


def _outline_level(value: object) -> int:
    if isinstance(value, str) and value.isdigit():
        value = int(value)
    if isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= 9:
        return value
    raise InvalidValueError(f"outline level must be 1-9, got {value!r}")


def _kebab(camel: str) -> str:
    return re.sub(r"(?<!^)([A-Z])", r"-\1", camel).lower()


def _choice(ooxml_values: Iterable[str]) -> Callable[[object], object]:
    """Accept kebab-case names; coerce to the OOXML value they stand for."""
    table = {_kebab(v): v for v in ooxml_values}

    def coerce(value: object) -> object:
        if isinstance(value, str) and value in table:
            return table[value]
        raise InvalidValueError(f"expected one of {', '.join(table)}, got {value!r}")

    coerce.__doc__ = " | ".join(table)
    return coerce


# --- emitters --------------------------------------------------------------------


def _toggle(element: str) -> Callable[[object, EmitContext], Emitted]:
    def emit(value: object, ctx: EmitContext) -> Emitted:
        return [(element, {} if value else {"val": "0"})]

    return emit


def _val(
    element: str, convert: Callable[[object], str] = str
) -> Callable[[object, EmitContext], Emitted]:
    def emit(value: object, ctx: EmitContext) -> Emitted:
        return [(element, {"val": convert(value)})]

    return emit


def _attr(
    element: str, attr: str, convert: Callable[[object], str]
) -> Callable[[object, EmitContext], Emitted]:
    def emit(value: object, ctx: EmitContext) -> Emitted:
        return [(element, {attr: convert(value)})]

    return emit


def _twips(value: object) -> str:
    assert isinstance(value, Length)
    return str(value.in_twips)


def _tab_stops(value: object) -> tuple[TabStop, ...]:
    if isinstance(value, TabStop):
        return (value,)
    if isinstance(value, tuple) and all(isinstance(v, TabStop) for v in value):  # pyright: ignore[reportUnknownVariableType]
        return value  # type: ignore[return-value]
    if isinstance(value, str):
        return TabStop.parse_list(value)
    raise InvalidValueError(
        f"expected tab stops such as `8cm center, 16cm right leader=dot`, got {value!r}"
    )


def _tab_elements(value: object, ctx: EmitContext) -> list[tuple[str, Attrs]]:
    stops = [stop for stop in _tab_stops(value)]
    elements: list[tuple[str, Attrs]] = []
    for stop in sorted(stops, key=lambda s: s.position.emu):
        attrs: Attrs = {"val": stop.align, "pos": str(stop.position.in_twips)}
        if stop.leader != "none":
            attrs["leader"] = TAB_LEADERS[stop.leader]
        elements.append(("tab", attrs))
    return elements


def _zero_based(value: object) -> str:
    assert isinstance(value, int)
    return str(value - 1)


def _emit_font(value: object, ctx: EmitContext) -> Emitted:
    assert isinstance(value, str)
    if value in ("theme.major", "theme.minor"):
        kind = value.removeprefix("theme.")
        return [
            (
                "rFonts",
                {
                    "asciiTheme": f"{kind}HAnsi",
                    "eastAsiaTheme": f"{kind}EastAsia",
                    "hAnsiTheme": f"{kind}HAnsi",
                    "cstheme": f"{kind}Bidi",
                },
            )
        ]
    return [("rFonts", {"ascii": value, "hAnsi": value})]


def _emit_size(value: object, ctx: EmitContext) -> Emitted:
    assert isinstance(value, Length)
    half_points = str(value.in_half_points)
    return [("sz", {"val": half_points}), ("szCs", {"val": half_points})]


def _emit_color(value: object, ctx: EmitContext) -> Emitted:
    assert isinstance(value, Color)
    if value.theme is not None:
        rgb = apply_tint_shade(ctx.theme_color_rgb(value.theme), value.tint, value.shade)
        attrs = {"val": rgb, "themeColor": value.theme}
        if value.tint is not None:
            attrs["themeTint"] = f"{value.tint:02X}"
        if value.shade is not None:
            attrs["themeShade"] = f"{value.shade:02X}"
        return [("color", attrs)]
    return [("color", {"val": value.rgb or "auto"})]


def color_attrs(color: Color, ctx: EmitContext, prefix: str = "") -> Attrs:
    """val/color, themeColor, themeTint, themeShade (or fill, themeFill, ... with a prefix)."""
    main = "fill" if prefix else "color"
    if color.theme is None:
        return {main: color.rgb or "auto"}
    attrs = {
        main: apply_tint_shade(ctx.theme_color_rgb(color.theme), color.tint, color.shade),
        f"theme{prefix or 'Color'}": color.theme,
    }
    if color.tint is not None:
        attrs[f"theme{prefix}Tint"] = f"{color.tint:02X}"
    if color.shade is not None:
        attrs[f"theme{prefix}Shade"] = f"{color.shade:02X}"
    return attrs


def _border(value: object) -> Border:
    if isinstance(value, Border):
        return value
    if isinstance(value, str):
        return Border.parse(value)
    raise InvalidValueError(f"expected a border such as (single, 0.5pt, #BFBFBF), got {value!r}")


def _border_side(
    container: str, side: str
) -> tuple[
    Callable[[object, EmitContext], Emitted],
    Callable[[object, EmitContext], list[tuple[str, Attrs]]],
]:
    def emit(value: object, ctx: EmitContext) -> Emitted:
        return [(container, {})]

    def children(value: object, ctx: EmitContext) -> list[tuple[str, Attrs]]:
        assert isinstance(value, Border)
        return [(side, border_attrs(value, ctx))]

    return emit, children


def border_attrs(border: Border, ctx: EmitContext) -> Attrs:
    """The attributes of a border element (w:top, w:bdr, ...), without the w: prefix."""
    if border.style == "nil":
        return {"val": "nil"}
    return {
        "val": border.style,
        "sz": str(border.eighths),
        "space": str(border.space_points),
        **color_attrs(border.color, ctx),
    }


def _margin_side(side: str) -> Callable[[object, EmitContext], list[tuple[str, Attrs]]]:
    def children(value: object, ctx: EmitContext) -> list[tuple[str, Attrs]]:
        assert isinstance(value, Length)
        return [(side, {"w": str(value.in_twips), "type": "dxa"})]

    return children


_TEXT_DIRECTIONS = {"horizontal": "lrTb", "up": "btLr", "down": "tbRl"}


def _text_direction(value: object) -> object:
    if isinstance(value, str) and value in _TEXT_DIRECTIONS:
        return _TEXT_DIRECTIONS[value]
    raise InvalidValueError(f"expected one of {', '.join(_TEXT_DIRECTIONS)}, got {value!r}")


def _emit_shading(value: object, ctx: EmitContext) -> Emitted:
    assert isinstance(value, Color)
    return [("shd", {"val": "clear", "color": "auto", **color_attrs(value, ctx, "Fill")})]


def _emit_line_spacing(value: object, ctx: EmitContext) -> Emitted:
    assert isinstance(value, LineSpacing)
    rule = {"auto": "auto", "exact": "exact", "at-least": "atLeast"}[value.rule]
    return [("spacing", {"line": str(value.line_attr), "lineRule": rule})]


# --- the registry ------------------------------------------------------------------

_UNDERLINES = (
    "single words double thick dotted dottedHeavy dash dashedHeavy dashLong dashLongHeavy "
    "dotDash dashDotHeavy dotDotDash dashDotDotHeavy wave wavyHeavy wavyDouble none"
).split()
_HIGHLIGHTS = (
    "black blue cyan green magenta red yellow white darkBlue darkCyan darkGreen darkMagenta "
    "darkRed darkYellow darkGray lightGray none"
).split()
_ALIGNMENTS = {
    "left": "left",
    "center": "center",
    "right": "right",
    "justify": "both",
    "distribute": "distribute",
}


def _alignment(value: object) -> object:
    if isinstance(value, str) and value in _ALIGNMENTS:
        return _ALIGNMENTS[value]
    raise InvalidValueError(f"expected one of {', '.join(_ALIGNMENTS)}, got {value!r}")


R, P, T, C = Target.RUN, Target.PARAGRAPH, Target.TABLE, Target.CELL
_BOOL = "true | false"
_LEN = "length (e.g. 12pt, 0.5cm)"

_SPECS: tuple[PropSpec, ...] = (
    # Run properties ---------------------------------------------------------------
    PropSpec(
        "font",
        R,
        _font,
        _emit_font,
        "Font family (Latin text).",
        "font name | theme.major | theme.minor",
    ),
    PropSpec("bold", R, _bool, _toggle("b"), "Bold.", _BOOL),
    PropSpec("italic", R, _bool, _toggle("i"), "Italic.", _BOOL),
    PropSpec("caps", R, _bool, _toggle("caps"), "Display all letters as capitals.", _BOOL),
    PropSpec(
        "small-caps", R, _bool, _toggle("smallCaps"), "Lower-case letters as small capitals.", _BOOL
    ),
    PropSpec("strike", R, _bool, _toggle("strike"), "Single strikethrough.", _BOOL),
    PropSpec("double-strike", R, _bool, _toggle("dstrike"), "Double strikethrough.", _BOOL),
    PropSpec("hidden", R, _bool, _toggle("vanish"), "Hidden text.", _BOOL),
    PropSpec("color", R, _color, _emit_color, "Text color.", "#RRGGBB | auto | theme.<name>"),
    PropSpec("emboss", R, _bool, _toggle("emboss"), "Embossed (raised) text.", _BOOL),
    PropSpec("imprint", R, _bool, _toggle("imprint"), "Engraved (imprinted) text.", _BOOL),
    PropSpec("outline", R, _bool, _toggle("outline"), "Outlined (hollow) letters.", _BOOL),
    PropSpec("shadow", R, _bool, _toggle("shadow"), "Shadowed text.", _BOOL),
    PropSpec(
        "scale",
        R,
        _percent,
        _val("w"),
        "Horizontal scale of the characters (100% is normal).",
        "1%-600%",
    ),
    PropSpec(
        "kerning",
        R,
        _font_size,
        lambda value, ctx: [("kern", {"val": str(value.in_half_points)})],  # type: ignore[attr-defined]
        "Kern pairs of letters from this font size up.",
        "length, e.g. 12pt",
    ),
    PropSpec(
        "character-spacing",
        R,
        _length,
        _val("spacing", _twips),
        "Expand (positive) or condense (negative) character spacing.",
        _LEN,
    ),
    PropSpec(
        "size", R, _font_size, _emit_size, "Font size (whole or half points).", "length, e.g. 11pt"
    ),
    PropSpec(
        "position",
        R,
        _length,
        lambda value, ctx: [("position", {"val": str(value.in_half_points)})],  # type: ignore[attr-defined]
        "Raise (positive) or lower (negative) the text from the baseline.",
        _LEN,
    ),
    PropSpec(
        "highlight",
        R,
        _choice(_HIGHLIGHTS),
        _val("highlight"),
        "Highlighter color.",
        " | ".join(_kebab(v) for v in _HIGHLIGHTS),
    ),
    PropSpec(
        "border",
        R,
        _border,
        lambda value, ctx: [("bdr", border_attrs(value, ctx))],  # type: ignore[arg-type]
        "A border around the text (Word's character border).",
        "(style, width, colour, space=…) | none",
    ),
    PropSpec(
        "shading",
        R,
        _color,
        _emit_shading,
        "Background colour behind the text (any colour; highlight has 16).",
        "#RRGGBB | theme.<name>",
    ),
    PropSpec(
        "underline",
        R,
        _choice(_UNDERLINES),
        _val("u"),
        "Underline style.",
        " | ".join(_kebab(v) for v in _UNDERLINES),
    ),
    PropSpec(
        "vertical-align",
        R,
        _choice(("baseline", "superscript", "subscript")),
        _val("vertAlign"),
        "Superscript or subscript.",
        "baseline | superscript | subscript",
    ),
    PropSpec(
        "lang",
        R,
        _lang,
        _val("lang"),
        "Language for proofing and hyphenation.",
        "language tag, e.g. en-US",
    ),
    # Paragraph properties ---------------------------------------------------------
    PropSpec(
        "keep-with-next",
        P,
        _bool,
        _toggle("keepNext"),
        "Keep on the same page as the next paragraph.",
        _BOOL,
    ),
    PropSpec(
        "keep-lines",
        P,
        _bool,
        _toggle("keepLines"),
        "Keep all lines of the paragraph on one page.",
        _BOOL,
    ),
    PropSpec(
        "page-break-before", P, _bool, _toggle("pageBreakBefore"), "Start on a new page.", _BOOL
    ),
    PropSpec(
        "widow-control", P, _bool, _toggle("widowControl"), "Prevent widows and orphans.", _BOOL
    ),
    PropSpec(
        "space-before",
        P,
        _nonnegative_length,
        _attr("spacing", "before", _twips),
        "Space above.",
        _LEN,
    ),
    PropSpec(
        "space-after",
        P,
        _nonnegative_length,
        _attr("spacing", "after", _twips),
        "Space below.",
        _LEN,
    ),
    PropSpec(
        "line-spacing",
        P,
        _line_spacing,
        _emit_line_spacing,
        "Line spacing.",
        "multiple (e.g. 1.15) or LineSpacing.exact/at_least",
    ),
    PropSpec("indent-left", P, _length, _attr("ind", "left", _twips), "Left indent.", _LEN),
    PropSpec("indent-right", P, _length, _attr("ind", "right", _twips), "Right indent.", _LEN),
    PropSpec(
        "first-line-indent",
        P,
        _nonnegative_length,
        _attr("ind", "firstLine", _twips),
        "Indent of the first line.",
        _LEN,
    ),
    PropSpec(
        "hanging-indent",
        P,
        _nonnegative_length,
        _attr("ind", "hanging", _twips),
        "Outdent of the first line.",
        _LEN,
    ),
    PropSpec(
        "suppress-line-numbers",
        P,
        _bool,
        _toggle("suppressLineNumbers"),
        "Leave the paragraph out of line numbering.",
        _BOOL,
    ),
    PropSpec(
        "suppress-hyphenation",
        P,
        _bool,
        _toggle("suppressAutoHyphens"),
        "Do not hyphenate the paragraph automatically.",
        _BOOL,
    ),
    PropSpec(
        "contextual-spacing",
        P,
        _bool,
        _toggle("contextualSpacing"),
        "Ignore spacing between paragraphs of the same style.",
        _BOOL,
    ),
    PropSpec(
        "tabs",
        P,
        _tab_stops,
        lambda value, ctx: [("tabs", {})],
        "Tab stops: position, alignment and leader.",
        "e.g. 8cm center, 16cm right leader=dot",
        children=_tab_elements,
    ),
    PropSpec("align", P, _alignment, _val("jc"), "Horizontal alignment.", " | ".join(_ALIGNMENTS)),
    PropSpec(
        "outline-level",
        P,
        _outline_level,
        _val("outlineLvl", _zero_based),
        "Outline level (1-9), as used by the navigation pane and TOC.",
        "1-9",
    ),
)

BORDER_SIDES = {
    "top": "top",
    "left": "left",
    "bottom": "bottom",
    "right": "right",
    "inside-h": "insideH",
    "inside-v": "insideV",
}
"""wordgenx side -> OOXML element, in schema order (tables and cells)."""
PARAGRAPH_BORDER_SIDES = {
    "top": "top",
    "left": "left",
    "bottom": "bottom",
    "right": "right",
    "between": "between",
    "bar": "bar",
}
"""Paragraph border sides (w:pBdr): `between` separates paragraphs that share the same
borders; `bar` is a vertical line beside the text."""
MARGIN_SIDES = ("top", "left", "bottom", "right")


def _border_specs(
    target: Target, container: str, what: str, sides: Mapping[str, str] = BORDER_SIDES
) -> tuple[PropSpec, ...]:
    specs: list[PropSpec] = []
    for side, element in sides.items():
        emit, children = _border_side(container, element)
        specs.append(
            PropSpec(
                f"border-{side}",
                target,
                _border,
                emit,
                f"{what} border: {side.replace('-', ' ')}.",
                "(style, width, colour, space=…) | none",
                children=children,
            )
        )
    return tuple(specs)


_TABLE_SPECS: tuple[PropSpec, ...] = (
    *_border_specs(T, "tblBorders", "Table"),
    *(
        PropSpec(
            f"cell-margin-{side}",
            T,
            _nonnegative_length,
            lambda value, ctx: [("tblCellMar", {})],
            f"Default {side} margin inside cells.",
            _LEN,
            children=_margin_side(side),
        )
        for side in MARGIN_SIDES
    ),
    PropSpec(
        "table-align",
        T,
        _choice(("left", "center", "right")),
        _val("jc"),
        "Position of the table between the margins.",
        "left | center | right",
    ),
    PropSpec(
        "table-indent",
        T,
        _length,
        lambda value, ctx: [("tblInd", {"w": _twips(value), "type": "dxa"})],
        "Indent of the table from the left margin.",
        _LEN,
    ),
)

_CELL_SPECS: tuple[PropSpec, ...] = (
    *_border_specs(C, "tcBorders", "Cell"),
    PropSpec(
        "shading",
        C,
        _color,
        _emit_shading,
        "Background colour of the cell.",
        "#RRGGBB | theme.<name>",
    ),
    *(
        PropSpec(
            f"cell-margin-{side}",
            C,
            _nonnegative_length,
            lambda value, ctx: [("tcMar", {})],
            f"The {side} margin inside this cell (overrides the table's).",
            _LEN,
            children=_margin_side(side),
        )
        for side in MARGIN_SIDES
    ),
    PropSpec(
        "text-direction",
        C,
        _text_direction,
        _val("textDirection"),
        "Direction of the text in the cell: rotated up (bottom-to-top) or down.",
        "horizontal | up | down",
    ),
    PropSpec(
        "valign",
        C,
        _choice(("top", "center", "bottom")),
        _val("vAlign"),
        "Vertical alignment of the cell content.",
        "top | center | bottom",
    ),
)

_PARAGRAPH_SPECS: tuple[PropSpec, ...] = (
    *_border_specs(P, "pBdr", "Paragraph", PARAGRAPH_BORDER_SIDES),
    PropSpec(
        "shading",
        P,
        _color,
        _emit_shading,
        "Background colour of the paragraph.",
        "#RRGGBB | theme.<name>",
    ),
)

_RAW_CONTAINERS = {R: "rPr", P: "pPr", T: "tblPr", C: "tcPr"}


def _raw(target: Target) -> Callable[[object], object]:
    """Validate raw property XML for a container: well-formed w: elements it may hold."""

    def coerce(value: object) -> object:
        from wordgenx.ooxml import schema_order
        from wordgenx.ooxml.xml import parse_fragment

        if not isinstance(value, str):
            raise InvalidValueError(f"expected raw XML text, got {value!r}")
        try:
            elements = parse_fragment(value)
        except ValueError as e:
            raise InvalidValueError(str(e)) from None
        order = {
            "rPr": schema_order.RPR,
            "pPr": schema_order.PPR,
            "tblPr": schema_order.TBL_PR,
            "tcPr": schema_order.TC_PR,
        }[_RAW_CONTAINERS[target]]
        for element in elements:
            local = str(element.tag).rsplit("}", 1)[-1]
            if local not in order:
                raise InvalidValueError(f"w:{local} is not a w:{_RAW_CONTAINERS[target]} property")
        return value.strip()

    return coerce


_RAW_SPECS: tuple[PropSpec, ...] = tuple(
    PropSpec(
        f"ooxml.{container}",
        target,
        _raw(target),
        lambda value, ctx: [],
        f"Raw w:{container} XML; its elements replace those wordgenx writes (escape hatch).",
        "WordprocessingML elements, e.g. \"<w:snapToGrid w:val='0'/>\"",
    )
    for target, container in _RAW_CONTAINERS.items()
)

_ALL_SPECS = (*_SPECS, *_PARAGRAPH_SPECS, *_TABLE_SPECS, *_CELL_SPECS, *_RAW_SPECS)

REGISTRY: dict[Target, dict[str, PropSpec]] = {
    target: {spec.name: spec for spec in _ALL_SPECS if spec.target is target} for target in Target
}


def lookup(target: Target, name: str) -> PropSpec:
    try:
        return REGISTRY[target][name]
    except KeyError:
        close = difflib.get_close_matches(name, REGISTRY[target], n=1)
        raise UnknownPropertyError(name, target, close[0] if close else None) from None


# --- property sets -------------------------------------------------------------------


class PropertySet(Mapping[str, object]):
    """An immutable, validated set of properties for one target."""

    __slots__ = ("_target", "_values")

    def __init__(self, target: Target, values: Mapping[str, object] | None = None) -> None:
        coerced: dict[str, object] = {}
        for name, value in _expand_shorthands(target, values or {}).items():
            spec = lookup(target, name)
            try:
                coerced[name] = spec.coerce(value)
            except InvalidValueError as e:
                raise InvalidValueError(f"{name}: {e}") from None
        if "first-line-indent" in coerced and "hanging-indent" in coerced:
            raise InvalidValueError("first-line-indent and hanging-indent are mutually exclusive")
        self._target = target
        self._values = self._ordered(target, coerced)

    @staticmethod
    def _ordered(target: Target, values: dict[str, object]) -> dict[str, object]:
        # Registry order, so iteration (and anything derived from it) is deterministic.
        order = list(REGISTRY[target])
        return dict(sorted(values.items(), key=lambda item: order.index(item[0])))

    @property
    def target(self) -> Target:
        return self._target

    def __getitem__(self, name: str) -> object:
        return self._values[name]

    def __iter__(self) -> Iterator[str]:
        return iter(self._values)

    def __len__(self) -> int:
        return len(self._values)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, PropertySet):
            return NotImplemented
        return self._target is other._target and self._values == other._values

    def __hash__(self) -> int:
        return hash((self._target, tuple(self._values.items())))

    def __repr__(self) -> str:
        items = ", ".join(f"{k}={v!s}" for k, v in self._values.items())
        return f"PropertySet({self._target.name}, {items})"

    def merged(self, other: PropertySet) -> PropertySet:
        """A new set with `other`'s values taking precedence.

        Both sets are already validated, so values are not coerced again. A first-line
        indent in `other` replaces a hanging indent in `self`, and vice versa.
        """
        if other._target is not self._target:
            raise ValueError("cannot merge property sets of different targets")
        values = dict(self._values)
        for exclusive, rival in (
            ("first-line-indent", "hanging-indent"),
            ("hanging-indent", "first-line-indent"),
        ):
            if exclusive in other._values:
                values.pop(rival, None)
        values.update(other._values)
        merged = PropertySet.__new__(PropertySet)
        merged._target = self._target
        merged._values = self._ordered(self._target, values)
        return merged

    def emit(self, ctx: EmitContext) -> dict[str, Attrs]:
        """Element local name -> merged attributes, for every property in the set."""
        elements: dict[str, Attrs] = {}
        for name, value in self._values.items():
            for element, attrs in REGISTRY[self._target][name].emit(value, ctx):
                elements.setdefault(element, {}).update(attrs)
        return elements

    def emit_children(self, ctx: EmitContext) -> dict[str, list[tuple[str, Attrs]]]:
        """Element local name -> child elements, for properties that have them (w:tabs,
        w:tblBorders, ...)."""
        children: dict[str, list[tuple[str, Attrs]]] = {}
        for name, value in self._values.items():
            spec = REGISTRY[self._target][name]
            if spec.children is not None:
                for element, _ in spec.emit(value, ctx):
                    children.setdefault(element, []).extend(spec.children(value, ctx))
        return children


def _python_names(kwargs: Mapping[str, object]) -> dict[str, object]:
    return {name.replace("_", "-"): value for name, value in kwargs.items()}


def run_props(**kwargs: object) -> PropertySet:
    """Run properties from Python keyword names (underscores for hyphens)."""
    return PropertySet(Target.RUN, _python_names(kwargs))


def para_props(**kwargs: object) -> PropertySet:
    """Paragraph properties from Python keyword names (underscores for hyphens)."""
    return PropertySet(Target.PARAGRAPH, _python_names(kwargs))


def table_props(**kwargs: object) -> PropertySet:
    return PropertySet(Target.TABLE, _python_names(kwargs))


def cell_props(**kwargs: object) -> PropertySet:
    return PropertySet(Target.CELL, _python_names(kwargs))


# --- shorthands --------------------------------------------------------------------

_BORDER_GROUPS: dict[str, tuple[str, ...]] = {
    **{side: (side,) for side in BORDER_SIDES},
    "outer": ("top", "left", "bottom", "right"),
    "inner": ("inside-h", "inside-v"),
    "all": tuple(BORDER_SIDES),
}
_PARAGRAPH_BORDER_GROUPS: dict[str, tuple[str, ...]] = {
    **{side: (side,) for side in PARAGRAPH_BORDER_SIDES},
    "outer": ("top", "left", "bottom", "right"),
    "all": ("top", "left", "bottom", "right"),
}
"""For paragraphs, `all` is a box: the four sides (between and bar are set by name)."""
_MARGIN_GROUPS: dict[str, tuple[str, ...]] = {
    **{side: (side,) for side in MARGIN_SIDES},
    "horizontal": ("left", "right"),
    "vertical": ("top", "bottom"),
    "all": MARGIN_SIDES,
}
SHORTHANDS = ("borders", "cell-margins")
"""Keys that stand for several properties (expanded by `expand_shorthand`)."""


def _expand_shorthands(target: Target, values: Mapping[str, object]) -> dict[str, object]:
    """`values` with shorthand keys (given as text) replaced by the properties they set."""
    out: dict[str, object] = {}
    for name, value in values.items():
        if name in SHORTHANDS and name not in REGISTRY[target] and isinstance(value, str):
            try:
                expanded = expand_shorthand(name, value, target) or {}
            except InvalidValueError as e:
                raise InvalidValueError(f"{name}: {e}") from None
            if any(key not in REGISTRY[target] for key in expanded):
                raise UnknownPropertyError(name, target, None)
            out.update(expanded)
        else:
            out[name] = value
    return out


def split_parts(text: str) -> list[tuple[str | None, str]]:
    """``(a=1, b=(x, y), z)`` -> [("a", "1"), ("b", "(x, y)"), (None, "z")]."""
    text = text.strip()
    if text.startswith("(") and text.endswith(")"):
        text = text[1:-1]
    parts: list[str] = []
    depth, current = 0, ""
    for ch in text:
        if ch == "," and depth == 0:
            parts.append(current.strip())
            current = ""
            continue
        depth += {"(": 1, ")": -1}.get(ch, 0)
        current += ch
    if current.strip():
        parts.append(current.strip())
    out: list[tuple[str | None, str]] = []
    for part in parts:
        match = re.fullmatch(r"([a-z][a-z-]*)\s*=\s*(.+)", part, re.DOTALL)
        out.append((match.group(1), match.group(2).strip()) if match else (None, part))
    return out


def expand_shorthand(key: str, raw: str, target: Target = Target.TABLE) -> dict[str, object] | None:
    """`borders` and `cell-margins` -> the per-side properties they stand for (None when
    `key` is not a shorthand). Raises InvalidValueError for a bad value.

    ``borders: (single, 0.5pt, #BFBFBF)`` sets all six sides; ``borders: none`` clears them;
    ``borders: (all=(single, 0.5pt), inside-v=none)`` sets groups (all, outer, inner) and
    sides (top, left, bottom, right, inside-h, inside-v) in order. For paragraphs
    (`target` PARAGRAPH) the sides are top, left, bottom, right, between and bar, and `all`
    is the four sides of a box.
    """
    if key == "borders":
        groups = _PARAGRAPH_BORDER_GROUPS if target is Target.PARAGRAPH else _BORDER_GROUPS
        parts = split_parts(raw)
        keys = [k for k, _ in parts if k]
        if not any(k in groups for k in keys):  # one border for all sides (space=... aside)
            parts = [("all", raw)]
        elif unknown := next((k for k in keys if k not in groups), None):
            raise InvalidValueError(
                f"unknown border side `{unknown}`; expected one of {', '.join(groups)}"
            )
        elif any(k is None for k, _ in parts):
            raise InvalidValueError("write borders as one border or as side=border pairs, not both")
        out: dict[str, object] = {}
        for group, value in parts:
            border = Border.parse(value)
            for side in groups[group or "all"]:
                out[f"border-{side}"] = border
        return out
    if key == "cell-margins":
        parts = split_parts(raw)
        if len(parts) == 1 and parts[0][0] is None:
            parts = [("all", parts[0][1])]
        out = {}
        for group, value in parts:
            if group not in _MARGIN_GROUPS:
                raise InvalidValueError(
                    f"cell-margins parts are {', '.join(_MARGIN_GROUPS)}; got {group or value!r}"
                )
            length = _nonnegative_length(value)
            for side in _MARGIN_GROUPS[group]:
                out[f"cell-margin-{side}"] = length
        return out
    return None


EMPTY_RUN = PropertySet(Target.RUN)
EMPTY_PARAGRAPH = PropertySet(Target.PARAGRAPH)
EMPTY_TABLE = PropertySet(Target.TABLE)
EMPTY_CELL = PropertySet(Target.CELL)
