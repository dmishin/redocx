"""The LaTeX math subset: parsing, OMML output, numbering and references (design §3.14)."""

from __future__ import annotations

import re

import pytest
from lxml import etree

from redocx import ir
from redocx.compiler import build, compile_source
from redocx.math.nodes import (
    BOX_SIDES,
    Accent,
    Align,
    Bar,
    BorderBox,
    Delim,
    EqArr,
    Frac,
    Func,
    GroupChr,
    Limit,
    Matrix,
    Nary,
    Phant,
    Rad,
    Raw,
    Run,
    Scripts,
    SPre,
    canonical,
    linear_text,
)
from redocx.math.omml import omml
from redocx.math.parser import MathError, parse_formula, parse_math
from redocx.math.read import MathReader
from tests.support.xsd import validate_package

M = "{http://schemas.openxmlformats.org/officeDocument/2006/math}"
W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


# --- parsing ------------------------------------------------------------------------------------------


def test_scripts_fractions_roots() -> None:
    assert parse_math(r"x^2") == (Scripts((Run("x"),), None, (Run("2"),)),)
    assert parse_math(r"a_{ij}^{k}") == (Scripts((Run("a"),), (Run("i"), Run("j")), (Run("k"),)),)
    assert parse_math(r"\frac{a}{b}") == (Frac((Run("a"),), (Run("b"),)),)
    assert parse_math(r"\sqrt[3]{x}") == (Rad((Run("x"),), (Run("3"),)),)
    assert parse_math(r"f''") == (Scripts((Run("f"),), None, (Run("′′"),)),)
    assert parse_math(r"x^23") == (Scripts((Run("x"),), None, (Run("2"),)), Run("3"))  # one token


def test_a_superscript_after_primes_joins_them() -> None:
    assert parse_math(r"f'^2") == (Scripts((Run("f"),), None, (Run("′"), Run("2"))),)
    assert parse_math(r"f''_1^3") == (Scripts((Run("f"),), (Run("1"),), (Run("′′"), Run("3"))),)
    for formula in (r"f'^2^3", r"f^2'^3"):
        with pytest.raises(MathError, match="double superscript"):
            parse_math(formula)


def test_more_symbols_spaces_and_operators() -> None:
    assert parse_math(r"\varGamma\Gamma\varkappa") == (
        Run("Γ"),
        Run("Γ", style="p"),
        Run("ϰ"),
    )
    assert parse_math(r"A \sqcup B \dashv \Updownarrow") == (
        Run("A"),
        Run("⊔"),
        Run("B"),
        Run("⊣"),
        Run("⇕"),
    )
    assert parse_math(r"a\enspace b\negthinspace c\mathstrut") == (
        Run("a"),
        Run("\N{EN SPACE}"),
        Run("b"),
        Run("c"),
    )
    assert parse_math(r"\mathbin{\star}\mathrel{=}") == (Run("⋆"), Run("="))
    (nary,) = parse_math(r"\oiint_S f")
    assert isinstance(nary, Nary) and (nary.chr, nary.lim_loc) == ("∯", "subSup")  # noqa: PT018
    assert parse_math(r"\left\backslash x \right\Uparrow") == (Delim("\\", "⇑", (Run("x"),)),)
    assert parse_math(r"\biggm|") == (Run("|"),)


def test_boxes_cancels_and_phantoms() -> None:
    x = (Run("x"),)
    assert parse_math(r"\boxed{x}") == (BorderBox(x),)
    assert parse_math(r"\fbox{if x}") == (BorderBox((Run("if x", normal=True),)),)
    assert parse_math(r"\cancel{x}") == (BorderBox(x, BOX_SIDES, ("bltr",)),)
    assert parse_math(r"\bcancel{x}") == (BorderBox(x, BOX_SIDES, ("tlbr",)),)
    assert parse_math(r"\xcancel{x}") == (BorderBox(x, BOX_SIDES, ("bltr", "tlbr")),)
    assert parse_math(r"\phantom{x}") == (Phant(x, show=False),)
    assert parse_math(r"\hphantom{x}") == (Phant(x, False, False, True, True),)
    assert parse_math(r"\vphantom{x}") == (Phant(x, False, True),)
    assert parse_math(r"\smash{x}") == (Phant(x, True, False, True, True),)
    assert parse_math(r"\smash[t]{x}") == (Phant(x, True, False, True, False),)
    with pytest.raises(MathError, match="takes"):
        parse_math(r"\smash[q]{x}")


def test_cancelto_warns_and_writes_a_superscript() -> None:
    row, (warning,) = parse_formula(r"\cancelto{0}{x}")
    assert row == (Scripts((BorderBox((Run("x"),), BOX_SIDES, ("bltr",)),), None, (Run("0"),)),)
    assert "no arrow in Word" in warning.message
    assert (warning.start, warning.end) == (0, 9)


def test_box_and_phantom_omml() -> None:
    element = omml(parse_math(r"\cancel{x}\phantom{y}"), display=False)
    tags = _tags(element)
    for tag in ("borderBox", "hideTop", "hideBot", "hideLeft", "hideRight", "strikeBLTR"):
        assert tag in tags, tag
    show = element.find(f"{M}phant/{M}phantPr/{M}show")
    assert show is not None
    assert show.get(f"{M}val") == "0"


def test_colours() -> None:
    red, blue = "FF0000", "0000FF"
    (scripts, plus) = parse_math(r"\textcolor{red}{x^2}+")
    assert scripts == Scripts((Run("x", color=red),), None, (Run("2", color=red),), color=red)
    assert plus == Run("+")
    row = parse_math(r"a{\color{blue} b \frac{1}{2}}c")
    assert row == (
        Run("a"),
        Run("b", color=blue),
        Frac((Run("1", color=blue),), (Run("2", color=blue),), color=blue),
        Run("c"),
    )
    # the innermost colour wins
    assert parse_math(r"\textcolor{red}{a\textcolor{blue}{b}}") == (
        Run("a", color=red),
        Run("b", color=blue),
    )
    assert parse_math(r"\colorbox{yellow}{if}") == (Run("if", normal=True, shade="FFFF00"),)
    assert parse_math(r"\fcolorbox{red}{yellow}{if}") == (
        BorderBox((Run("if", normal=True, shade="FFFF00"),), color=red),
    )


@pytest.mark.parametrize(
    ("spec", "rgb"),
    [
        ("{orange}", "FF8000"),
        ("{red!50}", "FF8080"),
        ("{red!50!blue}", "800080"),
        ("[HTML]{00a0ff}", "00A0FF"),
        ("[rgb]{1,0.5,0}", "FF8000"),
        ("[RGB]{0,128,255}", "0080FF"),
        ("[cmyk]{0,1,1,0}", "FF0000"),
        ("[gray]{0.5}", "808080"),
    ],
)
def test_colour_values(spec: str, rgb: str) -> None:
    assert parse_math(rf"\textcolor{spec}{{x}}") == (Run("x", color=rgb),)


@pytest.mark.parametrize(
    ("formula", "message"),
    [
        (r"\textcolor{rot}{x}", "unknown colour `rot`"),
        (r"\textcolor[hsb]{1,1,1}{x}", "unknown colour model `hsb`"),
        (r"\textcolor[HTML]{red}{x}", "`red` is not a `HTML` colour"),
        (r"\textcolor{red!150}{x}", "`150` is not a percentage"),
        (r"x^\color{red}y", "`\\color` colours the rest of a group"),
    ],
)
def test_colour_errors(formula: str, message: str) -> None:
    with pytest.raises(MathError, match=re.escape(message)):
        parse_math(formula)


def test_colour_omml() -> None:
    element = omml(parse_math(r"\textcolor{red}{\frac{1}{2}} \colorbox{yellow}{if}"), display=False)
    frac = element.find(f"{M}f")
    assert frac is not None
    bar = frac.find(f"{M}fPr/{M}ctrlPr/{W}rPr/{W}color")
    assert bar is not None and bar.get(f"{W}val") == "FF0000"  # noqa: PT018
    digit = frac.find(f"{M}num/{M}r/{W}rPr/{W}color")
    assert digit is not None and digit.get(f"{W}val") == "FF0000"  # noqa: PT018
    shade = element.find(f"{M}r/{W}rPr/{W}shd")
    assert shade is not None and shade.get(f"{W}fill") == "FFFF00"  # noqa: PT018
    data, result = build(r"\m{\textcolor{red}{\sqrt{x}} + \fcolorbox{blue}{yellow}{a}}")
    assert data is not None
    assert result.diagnostics == ()
    assert validate_package(data) == {}


def test_modulo() -> None:
    mod = Run("mod", style="p")
    assert parse_math(r"a \bmod b") == (
        Run("a"),
        Run("\N{THREE-PER-EM SPACE}"),
        mod,
        Run("\N{THREE-PER-EM SPACE}"),
        Run("b"),
    )
    assert parse_math(r"a \pmod{n}") == (
        Run("a"),
        Run("\N{EM SPACE}"),
        Delim("(", ")", (mod, Run("\N{THREE-PER-EM SPACE}"), Run("n"))),
    )


def test_symbols_and_text() -> None:
    assert parse_math(r"\alpha \leq \Omega - 1") == (
        Run("α"),
        Run("≤"),
        Run("Ω", style="p"),
        Run("−"),
        Run("1"),
    )
    assert parse_math(r"\text{if } x") == (Run("if ", normal=True), Run("x"))
    assert parse_math(r"\mathbb{R}") == (Run("R", style="p", script="double-struck"),)
    assert parse_math(r"a\,b\quad c") == (Run("a"), Run(" "), Run("b"), Run(" "), Run("c"))


def test_big_operators_take_their_operand() -> None:
    (nary, eq, rest) = parse_math(r"\sum_{i=1}^n x_i = y")
    assert isinstance(nary, Nary)
    assert (nary.chr, nary.lim_loc, linear_text(nary.e)) == ("∑", "undOvr", "x_(i)")
    assert (eq, rest) == (Run("="), Run("y"))
    (integral,) = parse_math(r"\int\limits_0^1 f")
    assert isinstance(integral, Nary)
    assert integral.lim_loc == "undOvr"
    assert parse_math(r"\int_0^1 f")[0].lim_loc == "subSup"  # type: ignore[union-attr]


def test_functions_and_limits() -> None:
    (sin,) = parse_math(r"\sin x")
    assert sin == Func((Run("sin", style="p"),), (Run("x"),))
    (lim, *_) = parse_math(r"\lim_{x \to 0} f(x) = 1")
    assert isinstance(lim, Func)
    assert isinstance(lim.name[0], Limit)
    assert linear_text(lim.e) == "f(x)"
    (op,) = parse_math(r"\operatorname{argmax} y")
    assert isinstance(op, Func)
    assert op.name == (Run("argmax", style="p"),)


def test_delimiters_environments_accents() -> None:
    assert parse_math(r"\left( x \right]") == (Delim("(", "]", (Run("x"),)),)
    (cases,) = parse_math(r"\begin{cases} 1 & x > 0 \\ 0 & \text{else} \end{cases}")
    assert isinstance(cases, Delim) and cases.beg == "{" and cases.end == ""  # noqa: PT018
    assert isinstance(cases.e[0], Matrix) and cases.e[0].align == "left"  # noqa: PT018
    (matrix,) = parse_math(r"\begin{bmatrix} a & b \\ c & d \\ \end{bmatrix}")
    assert isinstance(matrix, Delim) and len(matrix.e[0].rows) == 2  # type: ignore[union-attr]  # noqa: PT018
    (aligned,) = parse_math(r"\begin{aligned} a &= b \\ &= c \end{aligned}")
    assert isinstance(aligned, EqArr)
    assert Align() in aligned.rows[0]
    assert parse_math(r"\hat{x}") == (Accent("̂", (Run("x"),)),)
    (rows,) = parse_math(r"a \\ b")
    assert isinstance(rows, EqArr) and len(rows.rows) == 2  # noqa: PT018


@pytest.mark.parametrize(
    ("formula", "message", "start", "end"),
    [
        (r"\frac{a}{b", "`{` is not closed", 8, 9),
        (r"x + \foo", "unknown math command `\\foo`", 4, 8),
        (r"x^2^3", "double superscript", 3, 4),
        (r"\left( x", "`\\left` is not closed", 0, 5),
        (r"\begin{cases} a \end{matrix}", "is closed by", 0, 28),
        (r"a & b", "`&` belongs in an environment", 2, 3),
        (r"\begin{foo} x \end{foo}", "unknown environment `foo`", 0, 11),
    ],
)
def test_errors(formula: str, message: str, start: int, end: int) -> None:
    with pytest.raises(MathError) as caught:
        parse_math(formula)
    assert message in caught.value.message
    assert (caught.value.start, caught.value.end) == (start, end)


def test_unknown_commands_suggest_a_name() -> None:
    with pytest.raises(MathError) as caught:
        parse_math(r"\alpah")
    assert caught.value.hint == "did you mean `\\alpha`?"


# --- OMML ---------------------------------------------------------------------------------------------


def _tags(element: etree._Element) -> list[str]:  # pyright: ignore[reportPrivateUsage]
    return [etree.QName(e).localname for e in element.iter() if isinstance(e.tag, str)]


def test_omml_matches_words_structure() -> None:
    element = omml(parse_math(r"E = mc^2"), display=False)
    assert etree.QName(element).localname == "oMath"
    first = element[0]
    assert etree.QName(first).localname == "r"
    font = first.find(f"{W}rPr/{W}rFonts")
    assert font is not None
    assert font.get(f"{W}ascii") == "Cambria Math"
    assert first.findtext(f"{M}t") == "E=m"  # adjacent runs merged, as Word writes them
    sup = element.find(f"{M}sSup")
    assert sup is not None
    assert sup.find(f"{M}sSupPr/{M}ctrlPr") is not None


def test_omml_objects() -> None:
    element = omml(parse_math(r"\sum_{i}^{n} \sqrt{x} + \sin y"), display=True)
    assert etree.QName(element).localname == "oMathPara"
    tags = _tags(element)
    for tag in ("nary", "naryPr", "chr", "limLoc", "rad", "degHide", "func", "fName"):
        assert tag in tags, tag
    function = element.find(f".//{M}fName/{M}r/{M}rPr/{M}sty")
    assert function is not None
    assert function.get(f"{M}val") == "p"


def test_numbered_equation_uses_words_hash() -> None:
    element = omml(
        parse_math("x = 1"), display=True, number="2.1", bookmark="_Ref_eq", bookmark_id=lambda n: 7
    )
    array = element.find(f"{M}oMath/{M}eqArr")
    assert array is not None
    assert array.find(f"{M}eqArrPr/{M}maxDist").get(f"{M}val") == "1"  # pyright: ignore[reportOptionalMemberAccess]
    (row,) = array.findall(f"{M}e")
    texts = [t.text for t in row.iter(f"{M}t")]
    assert texts == ["x=1", "#", "2.1"]
    # The bookmark holds the number without its parentheses: a REF adds its own.
    inside = row.find(f"{M}d/{M}e")
    assert inside is not None
    assert [etree.QName(c).localname for c in inside] == ["bookmarkStart", "r", "bookmarkEnd"]
    start = inside[0]
    assert (start.get(f"{W}name"), start.get(f"{W}id")) == ("_Ref_eq", "7")
    assert inside.find(f"{M}r/{M}rPr/{M}nor") is None


def test_bold_italic_text_is_both() -> None:
    element = omml((Run("if", style="bi", normal=True),), display=False)
    assert _tags(element) == ["oMath", "r", "rPr", "nor", "rPr", "b", "i", "t"]


def test_empty_scripts_are_valid() -> None:
    for formula in (r"y^{}", r"z_{}", r"x_{}^{}"):
        data, result = build(f"\\m{{{formula}}}")
        assert data is not None
        assert result.diagnostics == (), formula
        assert validate_package(data) == {}, formula


def test_equation_number_with_a_dash_is_text() -> None:
    element = omml(parse_math("x = 1"), display=True, number="2–1")
    number = element.find(f".//{M}d/{M}e/{M}r")
    assert number is not None
    assert number.find(f"{M}rPr/{M}nor") is not None  # not spaced like a minus sign


# --- in documents -------------------------------------------------------------------------------------


def compiled(text: str) -> ir.Document:
    result = compile_source(text)
    assert result.document is not None, result.render_diagnostics()
    assert not result.diagnostics, result.render_diagnostics()
    return result.document


def test_inline_and_display_math() -> None:
    doc = compiled("Text \\m{x^2} more.\n\n$$ \\frac{a}{b} $$\n\n\\math{y}")
    inline, display, command = doc.all_paragraphs()
    assert [type(i).__name__ for i in inline.content] == ["Run", "Math", "Run"]
    (math,) = display.content
    assert isinstance(math, ir.Math)
    assert (math.display, math.numbered) == (True, False)
    assert isinstance(command.content[0], ir.Math)


def test_equation_numbers_and_references() -> None:
    doc = compiled(
        "$$ a $$ {#eq:a}\n\n$$ b $$\n\n\\math[numbered]{c}\n\n\\math[id=eq:d]{d}\n\n"
        "@eq:a and \\ref[form=number]{eq:d}"
    )
    paragraphs = list(doc.all_paragraphs())
    numbers = [p.content[0].number for p in paragraphs[:4]]  # type: ignore[union-attr]
    assert numbers == ["1", None, "2", "3"]
    refs = paragraphs[-1]
    assert refs.text() == "(1) and 3"
    field = refs.content[1]
    assert isinstance(field, ir.Field)
    assert field.code.strip() == "REF _Ref_eq_a \\h"


def test_math_errors_point_into_the_document() -> None:
    result = compile_source("Text \\m{x + \\foo} end.")
    (diagnostic,) = [d for _, d in result.diagnostics]
    assert diagnostic.code.value == "RDX0311"
    assert result.diagnostics[0][0].text[diagnostic.span.start : diagnostic.span.end] == "\\foo"


def test_labelled_equations_must_be_numbered() -> None:
    result = compile_source("\\math[id=eq:x, nonumber]{x}")
    assert [d.message for _, d in result.diagnostics] == ["a labelled equation must be numbered"]


# --- raw OMML: \ooxml{...} in a formula, whole equations in \raw -------------------------------------

SPRE = (
    "<m:sPre><m:sub><m:r><m:t>1</m:t></m:r></m:sub><m:sup><m:r><m:t>2</m:t></m:r></m:sup>"
    "<m:e><m:r><m:t>X</m:t></m:r></m:e></m:sPre>"
)


def test_ooxml_parses_to_a_raw_node() -> None:
    (a, plus, raw) = parse_math(f"a + \\ooxml{{ {SPRE} }}")
    assert (a, plus) == (Run("a"), Run("+"))
    assert raw == Raw(SPRE)
    assert isinstance(raw, Raw)
    assert (raw.start, raw.end) == (4, 4 + len("\\ooxml{ ") + len(SPRE) + len(" }"))
    assert linear_text((raw,)) == "12X"


def test_ooxml_is_written_as_it_is() -> None:
    element = omml((Run("a"), Raw(SPRE)), display=False)
    assert [str(c.tag).removeprefix(M) for c in element] == ["r", "sPre"]
    data = build(f"Text \\m{{a + \\ooxml{{{SPRE}}}}} end.")[0]
    assert data is not None
    assert validate_package(data) == {}


def test_braces_and_backslashes_in_ooxml_are_entities() -> None:
    xml = "<m:r><m:t>&#x7B;&#x5C;&#x7D;</m:t></m:r>"
    assert linear_text(parse_math(f"\\ooxml{{{xml}}}")) == "{\\}"
    compiled(f"\\m{{\\ooxml{{{xml}}}}}")


@pytest.mark.parametrize(
    ("formula", "message"),
    [
        (
            "\\ooxml{<m:sPre><m:e/></m:sPre>}",
            "OMML that Word would reject: `m:e` is not allowed here",
        ),
        ("\\ooxml{<w:r/>}", "OMML must be math (m:) elements, got w:r"),
        ("\\ooxml{<m:r>}", "not well-formed XML"),
        ("\\ooxml{ }", "`\\ooxml` needs OMML elements"),
    ],
)
def test_ooxml_errors_point_at_the_command(formula: str, message: str) -> None:
    text = f"Text \\m{{x + {formula}}} end."
    result = compile_source(text)
    ((source, diagnostic),) = result.diagnostics
    assert diagnostic.code.value == "RDX0311"
    assert diagnostic.message.startswith(message), diagnostic.message
    assert source.text[diagnostic.span.start : diagnostic.span.end] == formula


def test_whole_equations_in_raw() -> None:
    doc = compiled(
        "\\raw[ooxml]{<m:oMathPara><m:oMath><m:r><m:t>E</m:t></m:r></m:oMath></m:oMathPara>}\n\n"
        "Inline \\raw[ooxml]{<m:oMath><m:r><m:t>x</m:t></m:r></m:oMath>} here."
    )
    assert len(list(doc.all_paragraphs())) == 2
    result = compile_source("\\raw[ooxml]{<m:f/>}")
    messages = [d.message for _, d in result.diagnostics]
    assert any("whole equations (m:oMath, m:oMathPara), got m:f" in m for m in messages)


def test_ir_json_leaves_out_source_offsets() -> None:
    from redocx.ir_json import encode

    assert encode(Raw(SPRE, 3, 9)) == {"type": "Raw", "xml": SPRE}


def test_a_shaded_first_run_is_guarded() -> None:
    # Word shades the whole equation when its first run is shaded.
    row = parse_math(r"\colorbox{yellow}{if} + b")
    element = omml(row, display=False)
    assert element[0].findtext(f"{M}t") == "\N{ZERO WIDTH SPACE}"
    assert element[0].find(f"{W}rPr/{W}shd") is None
    back = MathReader().equation(element)
    assert back is not None
    assert canonical(back) == canonical(row)


def test_arrows_and_brackets_over_and_under() -> None:
    assert parse_math(r"\overrightarrow{AB}") == (GroupChr("→", "top", (Run("A"), Run("B"))),)
    assert parse_math(r"\underbracket{x}") == (GroupChr("⎵", "bot", (Run("x"),)),)


def test_an_extensible_arrow_sits_under_its_label() -> None:
    arrow = GroupChr("→", "bot", (Run("f"),), "bot")
    assert parse_math(r"\xrightarrow{f}") == (arrow,)
    assert parse_math(r"\xrightarrow[g]{f}") == (Limit("low", (arrow,), (Run("g"),)),)
    xml = etree.tostring(omml((arrow,), display=False), encoding="unicode")
    assert ':vertJc ns0:val="bot"/>' in xml


def test_prescript() -> None:
    assert parse_math(r"\prescript{14}{6}{C}") == (SPre((Run("6"),), (Run("14"),), (Run("C"),)),)


def test_middle_separates_parts() -> None:
    (delim,) = parse_math(r"\left( a \middle| b \middle| c \right)")
    assert delim == Delim("(", ")", (Run("a"),), sep="|", rest=((Run("b"),), (Run("c"),)))
    xml = etree.tostring(omml((delim,), display=False), encoding="unicode")
    assert ':sepChr ns0:val="|"/>' in xml
    assert xml.count(":e>") == 6


def test_middle_needs_one_separator() -> None:
    with pytest.raises(MathError, match="one separator character"):
        parse_math(r"\left( a \middle| b \middle/ c \right)")
    with pytest.raises(MathError, match=r"`\\middle` without a matching `\\left`"):
        parse_math(r"a \middle| b")


def test_infix_fractions_take_the_whole_group() -> None:
    frac = Frac((Run("a"), Run("+"), Run("b")), (Run("c"),))
    assert parse_math(r"{a+b \over c}") == (frac,)
    assert parse_math(r"x + {n \choose k}") == (
        Run("x"),
        Run("+"),
        Delim("(", ")", (Frac((Run("n"),), (Run("k"),), "noBar"),)),
    )
    assert parse_math(r"{a \atop b}") == (Frac((Run("a"),), (Run("b"),), "noBar"),)


def test_genfrac() -> None:
    assert parse_math(r"\genfrac{(}{)}{0pt}{}{n}{k}") == parse_math(r"\binom{n}{k}")
    assert parse_math(r"\genfrac{}{}{}{1}{a}{b}") == parse_math(r"\frac{a}{b}")
    with pytest.raises(MathError, match="is not a delimiter"):
        parse_math(r"\genfrac{x}{}{}{}{a}{b}")


def test_substack_and_subarray_are_rows() -> None:
    rows = EqArr(((Run("i"), Run("<"), Run("n")), (Run("j"),)))
    assert parse_math(r"\substack{i<n \\ j}") == (rows,)
    assert parse_math(r"\begin{subarray}{l} i<n \\ j \end{subarray}") == (rows,)


def test_root_offsets_are_dropped() -> None:
    assert parse_math(r"\sqrt[\leftroot{2}\uproot{3} n]{x}") == parse_math(r"\sqrt[n]{x}")


def test_operator_names_made_of_objects() -> None:
    (func,) = parse_math(r"\varlimsup_{n} a")
    assert isinstance(func, Func)
    assert func.name == (Limit("low", (Bar("top", (Run("lim", style="p"),)),), (Run("n"),)),)
    assert parse_math(r"\mathllap{x}") == (Phant((Run("x"),), True, True, False, False),)


def test_more_environments() -> None:
    cells = ((Run("a"), Run("b")),)
    assert parse_math(r"\begin{dcases} a & b \end{dcases}") == parse_math(
        r"\begin{cases} a & b \end{cases}"
    )
    (rcases,) = parse_math(r"\begin{rcases} a & b \end{rcases}")
    assert rcases == Delim("", "}", (Matrix(((cells[0][:1], cells[0][1:]),), "left"),))
    (pmatrix,) = parse_math(r"\begin{pmatrix*}[r] a \\ b \end{pmatrix*}")
    assert pmatrix == Delim("(", ")", (Matrix((((Run("a"),),), ((Run("b"),),)), "right"),))
    assert parse_math(r"\begin{alignedat}{2} a &= b \end{alignedat}") == parse_math(
        r"\begin{aligned} a &= b \end{aligned}"
    )
    assert parse_math(r"\begin{multlined}[t][5cm] a \\ b \end{multlined}") == (
        EqArr(((Run("a"),), (Run("b"),))),
    )
    with pytest.raises(MathError, match="is not a column alignment"):
        parse_math(r"\begin{pmatrix*}[x] a \end{pmatrix*}")


def test_rules_in_arrays_are_ignored_with_a_warning() -> None:
    row, warnings = parse_formula(r"\begin{array}{c|c} a & b \\ \hline c & d \end{array}")
    assert row == parse_math(r"\begin{array}{cc} a & b \\ c & d \end{array}")
    assert [w.args[0] for w in warnings] == [
        "`\\hline` is not supported in Word's math; it is ignored"
    ]
    _, warnings = parse_formula(r"\begin{matrix} a \\ \cline{1-2} \hdotsfor[2]{1} \end{matrix}")
    assert len(warnings) == 2


def test_tag_is_ignored_with_a_warning() -> None:
    for formula in (r"E = mc^2 \tag{1}", r"E = mc^2 \tag*{(a)}"):
        row, (warning,) = parse_formula(formula)
        assert row == parse_math("E = mc^2")
        assert warning.args[0] == "`\\tag` is not supported; it is ignored"
        assert formula[warning.start : warning.end].startswith("\\tag")


def test_tag_warns_in_the_document() -> None:
    result = compile_source("$$ E = mc^2 \\tag{1} $$\n")
    assert [d.message for _, d in result.diagnostics] == ["`\\tag` is not supported; it is ignored"]


def test_old_font_switches_set_the_rest_of_the_group() -> None:
    assert parse_math(r"{\bf x y} z") == (Run("x", style="b"), Run("y", style="b"), Run("z"))
    assert parse_math(r"{\cal L}") == parse_math(r"\mathcal{L}")
    assert parse_math(r"a {\rm d}x") == parse_math(r"a \mathrm{d}x")


def test_more_text_commands() -> None:
    row, warnings = parse_formula(r"\emph{e} \textsf{s}")
    assert row == (Run("e", style="i", normal=True), Run("s", normal=True))
    assert [w.args[0] for w in warnings] == [
        "`\\textsf` is plain text in Word; the font is not kept"
    ]


def test_spaces_with_a_length() -> None:
    assert parse_math(r"a\hspace{1em}b") == (Run("a"), Run(" "), Run("b"))
    assert parse_math(r"a\mkern3mu b") == (Run("a"), Run(" "), Run("b"))
    assert parse_math(r"a\hspace*{1.5em}b") == (Run("a"), Run(" "), Run(" "), Run("b"))
    assert parse_math(r"a\kern-1em b") == (Run("a"), Run("b"))
    with pytest.raises(MathError, match="`1xx` is not a length"):
        parse_math(r"\hspace{1xx}")


def test_math_between_dollars_in_text() -> None:
    assert parse_math(r"\text{if $n$ is even}") == (
        Run("if ", normal=True),
        Run("n"),
        Run(" is even", normal=True),
    )
    assert parse_math(r"\text{a $\text{b $c$}$}") == (
        Run("a ", normal=True),
        Run("b ", normal=True),
        Run("c"),
    )
    assert parse_math(r"\fbox{$a$}") == (BorderBox((Run("a"),)),)
    (sum_,) = parse_math(r"\text{$\sum_i a$}")
    assert isinstance(sum_, Nary)


def test_escapes_in_text_are_their_characters() -> None:
    assert parse_math(r"\text{50\% of \$100 \& \#1 a\_b \{x\} {y}}") == (
        Run("50% of $100 & #1 a_b {x} y", normal=True),
    )


def test_colorbox_shades_its_math() -> None:
    (frac, text) = parse_math(r"\colorbox{yellow}{$\frac{1}{2}$ half}")
    assert frac == Frac((Run("1", shade="FFFF00"),), (Run("2", shade="FFFF00"),))
    assert text == Run(" half", normal=True, shade="FFFF00")
    (box,) = parse_math(r"\fcolorbox{red}{white}{$x$}")
    assert box == BorderBox((Run("x", shade="FFFFFF"),), color="FF0000")


def test_an_unclosed_dollar_in_text() -> None:
    for formula in (r"\text{a $x}", r"\text{a $x"):
        with pytest.raises(MathError, match=r"`\$` is not closed") as info:
            parse_math(formula)
        assert (info.value.start, info.value.end) == (8, 9)
