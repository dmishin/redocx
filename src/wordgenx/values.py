"""Typed values used by formatting properties: lengths, colors, line spacing.

Lengths are stored exactly as integer EMUs (English Metric Units) and converted
to whatever unit an OOXML attribute needs (twips, half-points, ...).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Literal

EMU_PER_UNIT: dict[str, int] = {
    "emu": 1,
    "twip": 635,
    "pt": 12_700,
    "pc": 152_400,
    "in": 914_400,
    "cm": 360_000,
    "mm": 36_000,
}

_LENGTH_RE = re.compile(r"\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+))\s*([a-z]+)\s*")


class InvalidValueError(ValueError):
    """A value that cannot be parsed or is out of range."""


def _round(value: Decimal) -> int:
    return int(value.quantize(Decimal(1), rounding=ROUND_HALF_UP))


@dataclass(frozen=True, order=True)
class Length:
    emu: int

    @classmethod
    def of(cls, amount: float | int | str | Decimal, unit: str) -> Length:
        if unit not in EMU_PER_UNIT:
            raise InvalidValueError(
                f"unknown unit {unit!r}; expected one of {', '.join(EMU_PER_UNIT)}"
            )
        try:
            number = Decimal(str(amount))
        except InvalidOperation:
            raise InvalidValueError(f"not a number: {amount!r}") from None
        return cls(_round(number * EMU_PER_UNIT[unit]))

    @classmethod
    def pt(cls, amount: float | int | str) -> Length:
        return cls.of(amount, "pt")

    @classmethod
    def cm(cls, amount: float | int | str) -> Length:
        return cls.of(amount, "cm")

    @classmethod
    def mm(cls, amount: float | int | str) -> Length:
        return cls.of(amount, "mm")

    @classmethod
    def inch(cls, amount: float | int | str) -> Length:
        return cls.of(amount, "in")

    @classmethod
    def twips(cls, amount: int) -> Length:
        return cls.of(amount, "twip")

    @classmethod
    def parse(cls, text: str) -> Length:
        """Parse a length such as ``12pt``, ``2.5cm`` or ``-0.5in``; a bare ``0`` is zero."""
        if text.strip() in ("0", "0.0"):
            return cls(0)
        match = _LENGTH_RE.fullmatch(text)
        if match is None:
            raise InvalidValueError(
                f"not a length: {text!r} (expected a number and a unit, e.g. 12pt)"
            )
        return cls.of(match.group(1), match.group(2))

    @property
    def in_twips(self) -> int:
        return _round(Decimal(self.emu) / 635)

    @property
    def in_half_points(self) -> int:
        return _round(Decimal(self.emu) / 6_350)

    def __str__(self) -> str:
        points = Decimal(self.emu) / 12_700
        return f"{points.normalize():f}pt"


THEME_COLOR_NAMES: tuple[str, ...] = (
    "dark1",
    "light1",
    "dark2",
    "light2",
    "accent1",
    "accent2",
    "accent3",
    "accent4",
    "accent5",
    "accent6",
    "hyperlink",
    "followedHyperlink",
    "background1",
    "text1",
    "background2",
    "text2",
)

_HEX_RE = re.compile(r"#?([0-9A-Fa-f]{6})")
_MODIFIER_RE = re.compile(r"([A-Za-z0-9]+)\.(lighter|darker)\((\d+)%\)")


def apply_tint_shade(rgb: str, tint: int | None, shade: int | None) -> str:
    """The RGB Word shows for a theme color with a tint (towards white) or shade (towards black)."""
    channels = [int(rgb[i : i + 2], 16) for i in (0, 2, 4)]
    if tint is not None:
        channels = [round(c * tint / 255 + 255 * (1 - tint / 255)) for c in channels]
    if shade is not None:
        channels = [round(c * shade / 255) for c in channels]
    return "".join(f"{c:02X}" for c in channels)


@dataclass(frozen=True)
class Color:
    """An RGB color, a theme color, or ``auto`` (both fields None)."""

    rgb: str | None = None
    """Upper-case hex without '#', e.g. ``1F3864``."""
    theme: str | None = None
    """An ST_ThemeColor name such as ``accent1`` or ``text1``."""
    tint: int | None = None
    """Theme colors only: ``lighter(p%)`` keeps (100-p)% of the color (w:themeTint, 0-255)."""
    shade: int | None = None
    """Theme colors only: ``darker(p%)`` keeps (100-p)% of the color (w:themeShade, 0-255)."""

    @classmethod
    def parse(cls, text: str) -> Color:
        """Parse ``#RRGGBB``, ``auto``, ``theme.<name>`` or ``theme.<name>.lighter|darker(N%)``."""
        text = text.strip()
        if text == "auto":
            return cls()
        if text.startswith("theme."):
            name, tint, shade = text.removeprefix("theme."), None, None
            if match := _MODIFIER_RE.fullmatch(name):
                name, direction, percent = match.group(1), match.group(2), int(match.group(3))
                if not 0 <= percent <= 100:
                    raise InvalidValueError(f"{direction} takes 0-100%, got {percent}%")
                kept = round(255 * (100 - percent) / 100)
                tint, shade = (kept, None) if direction == "lighter" else (None, kept)
            if name not in THEME_COLOR_NAMES:
                raise InvalidValueError(
                    f"unknown theme color {name!r}; expected one of {', '.join(THEME_COLOR_NAMES)}"
                )
            return cls(theme=name, tint=tint, shade=shade)
        match = _HEX_RE.fullmatch(text)
        if match is None:
            raise InvalidValueError(
                f"not a color: {text!r} (expected #RRGGBB, auto or theme.<name>)"
            )
        return cls(rgb=match.group(1).upper())

    def __str__(self) -> str:
        if self.theme is not None:
            if self.tint is not None:
                return f"theme.{self.theme}.lighter({100 - round(self.tint * 100 / 255)}%)"
            if self.shade is not None:
                return f"theme.{self.theme}.darker({100 - round(self.shade * 100 / 255)}%)"
            return f"theme.{self.theme}"
        return "auto" if self.rgb is None else f"#{self.rgb}"


LineRule = Literal["auto", "exact", "at-least"]


@dataclass(frozen=True)
class LineSpacing:
    """Line spacing: a multiple of single spacing, or an exact / minimum height."""

    rule: LineRule
    multiple: Decimal | None = None
    height: Length | None = None

    @classmethod
    def lines(cls, multiple: float | int | str | Decimal) -> LineSpacing:
        value = Decimal(str(multiple))
        if value <= 0:
            raise InvalidValueError(f"line spacing multiple must be positive: {multiple}")
        return cls("auto", multiple=value)

    @classmethod
    def exact(cls, height: Length) -> LineSpacing:
        return cls("exact", height=height)

    @classmethod
    def at_least(cls, height: Length) -> LineSpacing:
        return cls("at-least", height=height)

    @property
    def line_attr(self) -> int:
        """The w:line attribute: 240ths of a line for ``auto``, otherwise twips."""
        if self.multiple is not None:
            return _round(self.multiple * 240)
        assert self.height is not None
        return self.height.in_twips
