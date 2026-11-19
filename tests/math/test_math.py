"""The LaTeX math subset: parsing, OMML output, numbering and references (design §3.14)."""

from __future__ import annotations

import pytest
from lxml import etree

from redocx import ir
from redocx.compiler import build, compile_source
from redocx.math.nodes import (
    Accent,
    Align,
    Delim,
    EqArr,
    Frac,
    Func,
    Limit,
    Matrix,
    Nary,
    Rad,
    Raw,
    Run,
    Scripts,
    linear_text,
)
from redocx.math.omml import omml
from redocx.math.parser import MathError, parse_math
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
