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

from wordgenx.values import Color, InvalidValueError, Length, LineSpacing, apply_tint_shade


class Target(Enum):
    """Which property container a property belongs to."""

    RUN = "rPr"
    PARAGRAPH = "pPr"


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


class UnknownPropertyError(KeyError):
    def __init__(self, name: str, target: Target, suggestion: str | None) -> None:
        kind = "run" if target is Target.RUN else "paragraph"
        hint = f"; did you mean `{suggestion}`?" if suggestion else ""
        super().__init__(f"unknown {kind} property `{name}`{hint}")
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


R, P = Target.RUN, Target.PARAGRAPH
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
        "highlight",
        R,
        _choice(_HIGHLIGHTS),
        _val("highlight"),
        "Highlighter color.",
        " | ".join(_kebab(v) for v in _HIGHLIGHTS),
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
        "contextual-spacing",
        P,
        _bool,
        _toggle("contextualSpacing"),
        "Ignore spacing between paragraphs of the same style.",
        _BOOL,
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

REGISTRY: dict[Target, dict[str, PropSpec]] = {
    target: {spec.name: spec for spec in _SPECS if spec.target is target} for target in Target
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
        for name, value in (values or {}).items():
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


def _python_names(kwargs: Mapping[str, object]) -> dict[str, object]:
    return {name.replace("_", "-"): value for name, value in kwargs.items()}


def run_props(**kwargs: object) -> PropertySet:
    """Run properties from Python keyword names (underscores for hyphens)."""
    return PropertySet(Target.RUN, _python_names(kwargs))


def para_props(**kwargs: object) -> PropertySet:
    """Paragraph properties from Python keyword names (underscores for hyphens)."""
    return PropertySet(Target.PARAGRAPH, _python_names(kwargs))


EMPTY_RUN = PropertySet(Target.RUN)
EMPTY_PARAGRAPH = PropertySet(Target.PARAGRAPH)
