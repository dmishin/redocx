r"""Printing the math tree as LaTeX (`redocx.math.latex`), for import: what is printed parses
back to the same equation, and what the subset cannot say comes out as `\ooxml{...}`."""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from redocx.math import symbols as S  # noqa: N812
from redocx.math.latex import to_latex
from redocx.math.nodes import (
    Delim,
    EqArr,
    Frac,
    Func,
    GroupChr,
    Raw,
    Row,
    Run,
    Scripts,
    canonical,
    walk,
)
from redocx.math.omml import omml
from redocx.math.parser import parse_math
from redocx.math.read import MathReader
from tests.math.test_read import nodes


def same_equation(a: Row, b: Row) -> bool:
    """Whether two formulas write the same OMML, compared by reading it back (raw OMML in one
    reads as the objects it holds)."""
    first = MathReader().equation(omml(a, display=False))
    second = MathReader().equation(omml(b, display=False))
    return first is not None and second is not None and canonical(first) == canonical(second)


def printed(formula: str) -> str:
    """A formula parsed and printed again; it must print without raw OMML."""
    text = to_latex(parse_math(formula))
    assert text is not None
    assert "\\ooxml" not in text, text
    assert canonical(parse_math(text)) == canonical(parse_math(formula))
    return text


@settings(max_examples=150, deadline=None)  # drawing the trees is the slow part
@given(st.lists(nodes, max_size=4).map(tuple))
def test_what_is_printed_parses_back_to_the_same_equation(row: Row) -> None:
    text = to_latex(row)
    if text is not None:
        assert same_equation(parse_math(text), canonical(row)), text


@pytest.mark.parametrize(
    "formula",
    [
        r"x = \frac{-b \pm \sqrt{b^2 - 4ac}}{2a}",
        r"\int_0^\infty e^{-x^2}\,dx = \frac{\sqrt{\pi}}{2}",
        r"\sum_{i=1}^{n} i^2 = \frac{n(n+1)(2n+1)}{6}",
        r"\sum\nolimits_i x_i + \int\limits_0^1 f",
        r"\lim_{x \to 0} \frac{\sin x}{x} = 1",
        r"\sin^2 x + \cos^2 x = 1",
        r"\log_2 n + \lim^2 x",
        r"\operatorname*{argmax}_{x} f(x) + \operatorname{erf} x + \operatorname{lim}_x y",
        r"\begin{pmatrix} a & b \\ c & d \end{pmatrix} \begin{bmatrix} 1 \end{bmatrix}",
        r"\begin{Bmatrix} 1 \end{Bmatrix} \begin{vmatrix} 1 \end{vmatrix}",
        r"\begin{Vmatrix} 1 \end{Vmatrix} \begin{matrix} 1 & 2 \end{matrix}",
        r"|x| = \begin{cases} x & x \ge 0 \\ -x & \text{otherwise} \end{cases}",
        r"\begin{aligned} a &= b + c \\ &= d \end{aligned}",
        r"a \\ b",
        r"\textcolor{red}{x^2} + \textcolor[HTML]{00A0FF}{\frac{a}{b}} + \colorbox{yellow}{if}",
        r"\fcolorbox{red}{yellow}{a} + \textcolor{blue}{\sum_i x_i} = 1",
        r"\boxed{E = mc^2} +\cancel{x} + \bcancel{y} + \xcancel{z} + \fbox{if}",
        r"a\phantom{x}b + \hphantom{y} + \vphantom{X} + \smash{q} + \smash[t]{p} + \smash[b]{g}",
        r"\hat{x} \vec{v} \overline{AB} \underline{y} \underbrace{a+b}_{n} \overbrace{c}^{m}",
        r"\binom{n}{k} \left\langle x \right\rangle \left( a \right. \left\{ b \right\}",
        r"\left\lfloor x \right\rfloor \left\| v \right\|",
        r"\mathbb{R} \mathbf{v} \mathrm{d}x \mathcal{L} \mathfrak{g} \mathsf{A} \mathtt{x}",
        r"\boldsymbol{\alpha} \mathit{x} \Gamma \Delta x \mathrm{\Omega y}",
        r"\text{if } x \textit{ and } \textbf{bold} \text{\{braces\}}",
        r"x' f'' {ab}^2 {}^{14}_{6}C 10^{10} x_1 2",
        r"\sum_i x_i y_i + \prod_j z = \sum_k",
        r"\alpha\beta \leq \infty \pm \cdot \to \in \forall \partial \nabla",
        r"\overset{!}{=} \underset{x}{y} \sqrt[3]{x} \sqrt[n+1]{y}",
        r"\{ \} \% \$ \# \& \_ \| \backslash a~b\,c\:d\;e\quad f",
        r"3.14 + .5 - 2 * 4 / 1",
        r"\sin \frac{\pi}{2} + \cos\left(x\right) \tan{(x+y)}",
        r"\overrightarrow{AB} \underleftarrow{x} \overbracket{a} \underparen{b}",
        r"A \xrightarrow{f} B \xleftarrow[g]{h} C \xmapsto{} D",
        r"\prescript{14}{6}{C} + \left\langle a \middle| b \middle| c \right\rangle",
        r"{a \atop b} + \mathclap{x} + \varlimsup_{n} a + \varinjlim_i A + \injlim B",
    ],
)
def test_parsed_formulas_print_back(formula: str) -> None:
    printed(formula)


def test_every_symbol_prints() -> None:
    for table in (S.GREEK, S.UPPER_GREEK, S.SYMBOLS, S.NARY, S.ACCENTS, S.FONTS):
        for name in table:
            formula = {
                **dict.fromkeys(S.NARY, f"\\{name}_a^b x"),
                **dict.fromkeys(S.ACCENTS, f"\\{name}{{x}}"),
                **dict.fromkeys(S.FONTS, f"\\{name}{{x1}}"),
            }.get(name, f"\\{name}")
            printed(f"a {formula} b")
    for name in S.FUNCTIONS:
        printed(f"\\{name} x + \\{name}_a y + \\{name}^2 z")
    for key in S.DELIMITERS:
        printed(f"\\left{key} x \\right{key}")


def test_separators_print_with_middle() -> None:
    row = (Delim("(", ")", (Run("a"),), sep="|", rest=((Run("b"),),)),)
    assert to_latex(row) == r"\left(a\middle|b\right)"


def test_readable_forms() -> None:
    assert printed(r"\frac{a}{b}") == r"\frac{a}{b}"
    assert printed(r"\alpha x") == r"\alpha x"
    assert printed(r"x^{2}_{i}") == r"x_i^2"
    assert printed(r"\sin x = 0") == r"\sin x=0"
    assert printed(r"\sum_{i=1}^n x_i") == r"\sum_{i=1}^n{x_i}"
    assert printed(r"\sum_i i + 1") == r"\sum_ii+1"
    assert printed(r"\left\langle x \right\rangle") == r"\left\langle x\right\rangle"
    assert printed(r"x''") == "x''"


def test_operands_end_where_they_ended() -> None:
    # A function followed by something other than a relation is put in braces, so that
    # something does not become part of its operand.
    row = (Func((Run("sin", style="p"),), (Run("x"),)), Run("y"))
    assert to_latex(row) == r"{\sin x}y"
    assert to_latex((Func((Run("sin", style="p"),), ()),)) == r"{\sin}"


def test_a_group_with_scripts_is_the_base() -> None:
    assert parse_math("{ab}^2") == (Scripts((Run("a"), Run("b")), None, (Run("2"),)),)
    assert parse_math("{}^2") == (Scripts((), None, (Run("2"),)),)
    assert parse_math(r"{\overbrace{x}}^2") == (
        Scripts((parse_math(r"\overbrace{x}")[0],), None, (Run("2"),)),
    )


def test_what_the_subset_cannot_say_is_raw_omml() -> None:
    row = (
        Run("a b"),
        Frac((Run("1"),), (Run("2"),), "lin"),
        GroupChr("~", "top", (Run("x"),)),
        Run("x", script="fraktur"),
        Raw("<m:sPre><m:sub/><m:sup/><m:e/></m:sPre>"),
    )
    text = to_latex(row)
    assert text is not None
    assert text.startswith('a\\ooxml{<m:r><m:t xml:space="preserve"> </m:t></m:r>}b')
    assert '\\ooxml{<m:f><m:fPr><m:type m:val="lin"/></m:fPr>' in text
    assert '<m:scr m:val="fraktur"/>' in text
    assert text.endswith("\\ooxml{<m:sPre><m:sub/><m:sup/><m:e/></m:sPre>}")
    raws = [n for n in walk(parse_math(text)) if isinstance(n, Raw)]
    assert len(raws) == 5
    assert same_equation(parse_math(text), row)


def test_alignment_outside_an_array_is_not_printed() -> None:
    from redocx.math.nodes import Align

    assert to_latex((Run("a"), Align(), Run("b"))) is None
    assert (
        to_latex((EqArr(((Run("a"), Align(), Run("b")),)),)) == r"\begin{aligned}a&b\end{aligned}"
    )
