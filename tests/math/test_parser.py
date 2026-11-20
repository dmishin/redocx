"""The parser's machinery: it never crashes, and its errors say what is wrong (design §3.14)."""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from redocx.math.commands import COMMANDS, KNOWN_COMMANDS, SWITCHES
from redocx.math.nodes import Frac, Func, Nary, Run
from redocx.math.parser import MathError, parse_formula, parse_math

TOKENS = st.sampled_from(
    [
        *"ab{}[]^_&$\\ 12+-()|.'=~",
        "\\\\",
        "{red}",
        "[r]",
        "{1em}",
        "{(}",
        *(f"\\{name}" for name in KNOWN_COMMANDS),
        *(f"\\begin{{{e}}}" for e in ("cases", "pmatrix*", "aligned", "array", "subarray")),
        *(f"\\end{{{e}}}" for e in ("cases", "pmatrix*", "aligned", "array", "subarray")),
    ]
)


@settings(max_examples=3000, deadline=None)
@given(st.lists(TOKENS, max_size=14).map("".join))
def test_any_text_parses_or_is_an_error(text: str) -> None:
    error: MathError | None = None
    try:
        parse_formula(text)
    except MathError as e:
        error = e
    if error is not None:  # anything else than MathError fails the test
        assert 0 <= error.start < error.end <= len(text) + 1, error.message


def test_every_known_command_is_handled() -> None:
    closers = {"right", "middle", "end"}
    assert set(KNOWN_COMMANDS) == {*COMMANDS, *SWITCHES, *closers}
    assert not set(COMMANDS) & set(SWITCHES)


@pytest.mark.parametrize(
    ("formula", "message"),
    [
        (r"x^\right)", "`\\right` without a matching `\\left`"),
        (r"x^\end{cases}", "`\\end` without a matching `\\begin`"),
        (r"a \middle| b", "`\\middle` without a matching `\\left`"),
        (r"x^\over", "`\\over` applies to the rest of a group: put it inside `{...}`"),
        (r"x_\bf y", "`\\bf` applies to the rest of a group: put it inside `{...}`"),
    ],
)
def test_misplaced_partners_and_switches(formula: str, message: str) -> None:
    with pytest.raises(MathError) as info:
        parse_math(formula)
    assert info.value.message == message


def test_an_operand_ends_at_a_group_switch() -> None:
    (total,) = parse_math(r"{\sum x \over 2}")
    assert isinstance(total, Frac)
    assert isinstance(total.num[0], Nary)
    (sin, *rest) = parse_math(r"\sin x \color{red} y")
    assert isinstance(sin, Func)
    assert rest == [Run("y", color="FF0000")]


def test_an_optional_argument_holds_brackets_in_braces() -> None:
    _, (warning,) = parse_formula(r"\begin{matrix} a \hdotsfor[{]}]{2} \end{matrix}")
    assert warning.message == "`\\hdotsfor` is not supported in Word's math; it is ignored"
    with pytest.raises(MathError, match="unclosed `\\[`") as info:
        parse_math(r"\colorbox[rgb")
    assert (info.value.start, info.value.end) == (9, 10)
