from decimal import Decimal

import pytest

from wordgenx.values import Color, InvalidValueError, Length, LineSpacing


@pytest.mark.parametrize(
    ("text", "emu"),
    [
        ("12pt", 152_400),
        ("1in", 914_400),
        ("2.54cm", 914_400),
        ("25.4mm", 914_400),
        ("1pc", 152_400),
        ("1440twip", 914_400),
        ("-0.5cm", -180_000),
        (" 10.5 pt ", 133_350),
        (".5in", 457_200),
    ],
)
def test_length_parse(text: str, emu: int) -> None:
    assert Length.parse(text).emu == emu


@pytest.mark.parametrize("text", ["12", "pt", "12px", "1,5cm", "12 pt pt", ""])
def test_length_parse_errors(text: str) -> None:
    with pytest.raises(InvalidValueError):
        Length.parse(text)


def test_length_conversions() -> None:
    assert Length.inch(1).in_twips == 1440
    assert Length.cm("2.54").in_twips == 1440
    assert Length.pt("10.5").in_half_points == 21
    assert Length.cm(1).in_twips == 567  # 566.93 rounded
    assert str(Length.pt("10.5")) == "10.5pt"


def test_color_parse() -> None:
    assert Color.parse("#c00000") == Color(rgb="C00000")
    assert Color.parse("C00000") == Color(rgb="C00000")
    assert Color.parse("auto") == Color()
    assert Color.parse("theme.accent1") == Color(theme="accent1")
    with pytest.raises(InvalidValueError, match="unknown theme color"):
        Color.parse("theme.accent9")
    with pytest.raises(InvalidValueError):
        Color.parse("#12345")


def test_line_spacing() -> None:
    assert LineSpacing.lines("1.15").line_attr == 276
    assert LineSpacing.lines(2).multiple == Decimal(2)
    assert LineSpacing.exact(Length.pt(12)).line_attr == 240
    with pytest.raises(InvalidValueError):
        LineSpacing.lines(0)
