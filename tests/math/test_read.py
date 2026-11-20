"""Reading OMML back into the math tree (`redocx.math.read`): what the writer writes reads
back the same, Word's own markup reads, and what the tree cannot hold is kept as XML."""

from __future__ import annotations

import zipfile
from pathlib import Path

from hypothesis import given, settings
from hypothesis import strategies as st
from lxml import etree

from redocx import ir
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
    Node,
    Phant,
    Rad,
    Raw,
    Row,
    Run,
    Scripts,
    canonical,
    linear_text,
)
from redocx.math.omml import omml
from redocx.math.parser import parse_math
from redocx.math.read import MathReader, raw_xml, split_number
from redocx.ooxml.reader import read_docx
from redocx.ooxml.validate import fragment_problems
from redocx.ooxml.xml import parse_fragment

CORPUS = Path(__file__).resolve().parents[1] / "corpus"
M = "http://schemas.openxmlformats.org/officeDocument/2006/math"
W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def read(xml: str) -> tuple[Row | None, dict[str, int]]:
    (element,) = parse_fragment(f"<m:oMath>{xml}</m:oMath>")
    reader = MathReader()
    return reader.equation(element), reader.kept


# --- what the writer writes reads back -----------------------------------------------------------

texts = st.sampled_from(["x", "2", "+", "-", "α", "sin", "", "ab", " "])
math_runs = st.builds(
    Run,
    texts,
    st.sampled_from([None, "p", "b", "bi"]),
    st.sampled_from([None, "double-struck", "script"]),
)
text_runs = st.builds(
    Run, st.sampled_from(["if ", "and"]), st.sampled_from([None, "b", "i"]), normal=st.just(True)
)


def _rows(children: st.SearchStrategy[Node]) -> st.SearchStrategy[Row]:
    return st.lists(children, max_size=3).map(tuple)


def _objects(children: st.SearchStrategy[Node]) -> st.SearchStrategy[Node]:
    row = _rows(children)
    some = st.lists(children, min_size=1, max_size=3).map(tuple)
    return st.one_of(
        st.builds(Frac, row, row, st.sampled_from(["bar", "noBar", "lin"])),
        st.builds(Rad, row, st.none() | row),
        st.builds(Scripts, row, st.none() | some, some),
        st.builds(Scripts, row, some, st.none()),
        st.builds(
            Nary,
            st.sampled_from("∑∫∏∮"),
            st.none() | some,
            st.none() | some,
            row,
            st.sampled_from(["undOvr", "subSup"]),
        ),
        st.builds(
            Delim, st.sampled_from(["(", "[", "{", "|", ""]), st.sampled_from([")", "]", ""]), row
        ),
        st.builds(Func, row, row),
        st.builds(
            BorderBox,
            row,
            st.sampled_from([(), BOX_SIDES, ("top",)]),
            st.sampled_from([(), ("bltr",), ("bltr", "tlbr"), ("h",)]),
        ),
        st.builds(Phant, row, st.booleans(), st.booleans(), st.booleans(), st.booleans()),
        st.builds(Limit, st.sampled_from(["low", "upp"]), row, row),
        st.builds(Accent, st.sampled_from(["̂", "̃", "⃗"]), row),
        st.builds(Bar, st.sampled_from(["top", "bot"]), row),
        st.builds(GroupChr, st.sampled_from(["⏞", "⏟"]), st.sampled_from(["top", "bot"]), row),
        st.integers(1, 3).flatmap(
            lambda width: st.builds(
                Matrix,
                st.lists(
                    st.lists(row, min_size=width, max_size=width).map(tuple), min_size=1, max_size=2
                ).map(tuple),
                st.sampled_from(["center", "left"]),
            )
        ),
        st.builds(
            EqArr,
            st.lists(
                st.lists(st.one_of(children, st.just(Align())), max_size=3).map(tuple),
                min_size=1,
                max_size=2,
            ).map(tuple),
        ),
    )


nodes: st.SearchStrategy[Node] = st.recursive(
    st.one_of(math_runs, text_runs), _objects, max_leaves=12
)


@settings(max_examples=300, deadline=None)
@given(_rows(nodes))
def test_what_the_writer_writes_reads_back(row: Row) -> None:
    reader = MathReader()
    back = reader.equation(omml(row, display=False))
    assert back is not None
    assert canonical(back) == canonical(row)
    assert reader.kept == {}


def test_parsed_formulas_read_back() -> None:
    for formula in (
        r"\int_0^\infty e^{-x^2}\,dx = \frac{\sqrt{\pi}}{2}",
        r"\begin{aligned} a &= b + c \\ &= d \end{aligned}",
        r"f(x) = \begin{cases} 1 & x > 0 \\ 0 & \text{otherwise} \end{cases}",
        r"\lim_{x \to 0} \frac{\sin x}{x} = 1",
        r"\hat{x} + \overline{y} + \overbrace{a+b}^{n} + \mathbb{R} + \sqrt[3]{z}",
        r"\left[ \begin{pmatrix} 1 & 2 \\ 3 & 4 \end{pmatrix} \right]",
    ):
        row = parse_math(formula)
        back = MathReader().equation(omml(row, display=False))
        assert back is not None
        assert canonical(back) == canonical(row), formula


# --- Word's own markup --------------------------------------------------------------------------


def test_words_equation_in_the_corpus() -> None:
    document = read_docx(CORPUS / "review.docx").document
    (math,) = [i for p in document.all_paragraphs() for i in p.content if isinstance(i, ir.Math)]
    assert not math.display
    assert linear_text(math.expr) == "x=(-b±sqrt(b^(2)-4ac))/(2a)"
    (frac,) = [n for n in math.expr if isinstance(n, Frac)]
    assert frac.den == (Run("2a"),)


def test_defaults_word_leaves_out() -> None:
    row, kept = read(
        "<m:nary><m:naryPr><m:chr m:val='∑'/><m:ctrlPr><w:rPr><w:i/></w:rPr></m:ctrlPr></m:naryPr>"
        "<m:sub><m:r><m:t>i</m:t></m:r></m:sub><m:sup/><m:e><m:r><m:t>i</m:t></m:r></m:e></m:nary>"
        "<m:nary><m:naryPr><m:supHide m:val='1'/></m:naryPr><m:sub/><m:sup/><m:e/></m:nary>"
        "<m:d><m:e><m:r><m:t>a</m:t></m:r></m:e></m:d>"
        "<m:acc><m:e><m:r><m:t>v</m:t></m:r></m:e></m:acc>"
        "<m:groupChr><m:groupChrPr><m:vertJc m:val='top'/></m:groupChrPr><m:e/></m:groupChr>"
        "<m:r><m:rPr><m:sty m:val='p'/></m:rPr><w:rPr><w:rFonts w:ascii='Cambria Math' "
        "w:hAnsi='Cambria Math'/><w:lang w:val='en-GB'/></w:rPr><m:t>sin</m:t></m:r>"
    )
    assert kept == {}
    assert row == (
        Nary("∑", (Run("i"),), None, (Run("i"),), "undOvr"),
        Nary("∫", None, None, (), "subSup"),
        Delim("(", ")", (Run("a"),)),
        Accent("̂", (Run("v"),)),
        GroupChr("⏟", "bot", ()),
        Run("sin", style="p"),
    )


def test_text_runs_take_bold_and_italic_from_the_run() -> None:
    row, _ = read("<m:r><m:rPr><m:nor/></m:rPr><w:rPr><w:b/></w:rPr><m:t>if </m:t></m:r>")
    assert row == (Run("if ", style="b", normal=True),)


# --- what the tree cannot hold ------------------------------------------------------------------

SPRE = (
    "<m:sPre><m:sPrePr><m:ctrlPr><w:rPr><w:rFonts w:ascii='Cambria Math' w:hAnsi='Cambria Math'/>"
    "<w:i/></w:rPr></m:ctrlPr></m:sPrePr><m:sub><m:r><m:t>6</m:t></m:r></m:sub>"
    "<m:sup><m:r><m:t>14</m:t></m:r></m:sup><m:e><m:r><m:t>C</m:t></m:r></m:e></m:sPre>"
)


def test_unknown_objects_are_kept_as_xml_in_place() -> None:
    row, kept = read(f"<m:r><m:t>a+</m:t></m:r>{SPRE}<m:r><m:t>=c</m:t></m:r>")
    assert kept == {"m:sPre": 1}
    assert row is not None
    first, raw, last = row
    assert (first, last) == (Run("a+"), Run("=c"))
    assert isinstance(raw, Raw)
    # tidy: no namespace declarations, no default font or empty control properties
    assert raw.xml == (
        "<m:sPre><m:sub><m:r><m:t>6</m:t></m:r></m:sub><m:sup><m:r><m:t>14</m:t></m:r></m:sup>"
        "<m:e><m:r><m:t>C</m:t></m:r></m:e></m:sPre>"
    )


def test_the_smallest_object_is_kept() -> None:
    row, kept = read(
        "<m:f><m:num><m:r><m:t>1</m:t></m:r></m:num><m:den>"
        "<m:r><w:rPr><w:color w:val='FF0000'/></w:rPr><m:t>x</m:t></m:r>"
        "<m:d><m:dPr><m:sepChr m:val=','/></m:dPr><m:e/><m:e/></m:d>"
        "<m:f><m:fPr><m:type m:val='skw'/></m:fPr><m:num/><m:den/></m:f>"
        "</m:den></m:f>"
    )
    assert kept == {"m:r": 1, "m:d": 1}
    assert row is not None
    (frac,) = row
    assert isinstance(frac, Frac)
    colour, delim, skewed = frac.den
    assert isinstance(colour, Raw)
    assert "w:color" in colour.xml
    assert isinstance(delim, Raw)
    assert "m:sepChr" in delim.xml
    assert skewed == Frac((), (), "skw")
    for raw in (colour, delim):
        assert fragment_problems(raw.xml, "math") == []


def test_w_content_at_the_top_keeps_the_whole_equation() -> None:
    row, _ = read("<w:bookmarkStart w:id='1' w:name='x'/><m:r><m:t>x</m:t></m:r>")
    assert row is None


def test_raw_xml_is_safe_inside_braces() -> None:
    (element,) = parse_fragment("<m:r><m:t>{a\\b}</m:t></m:r>", math=True)
    assert raw_xml(element) == "<m:r><m:t>&#x7B;a&#x5C;b&#x7D;</m:t></m:r>"
    (back,) = parse_fragment(raw_xml(element), math=True)
    assert back.findtext(f"{{{M}}}t") == "{a\\b}"


def test_alignment_marks_are_words_ampersands() -> None:
    row, _ = read(
        "<m:eqArr><m:e><m:r><m:t>a&amp;=b</m:t></m:r></m:e>"
        "<m:e><m:r><m:rPr><m:aln/></m:rPr><m:t>=c</m:t></m:r></m:e></m:eqArr>"
    )
    assert row == (EqArr(((Run("a"), Align(), Run("=b")), (Align(), Run("=c")))),)
    written = etree.tostring(omml(parse_math(r"\begin{aligned}a&=b\end{aligned}"), display=False))
    assert b"&amp;=b" in written
    assert b"aln" not in written


def test_the_number_of_a_numbered_equation() -> None:
    written = omml(parse_math("x=1"), display=False, number="2.1", bookmark="_Ref_eq_x")
    split = split_number(written)
    assert split is not None
    rest, number, bookmark = split
    assert (number, bookmark) == ("2.1", "_Ref_eq_x")
    assert MathReader().equation(rest) == canonical(parse_math("x=1"))
    rows = omml(parse_math(r"\begin{aligned}a&=b \\ &=c\end{aligned}"), display=False, number="3")
    split = split_number(rows)
    assert split is not None
    assert MathReader().equation(split[0]) == (
        EqArr(((Run("a"), Align(), Run("=b")), (Align(), Run("=c")))),
    )
    assert split_number(omml(parse_math("a#(1)"), display=False)) is None  # not in an array


def test_raw_xml_uses_the_usual_prefixes() -> None:
    element = etree.fromstring(f'<x:r xmlns:x="{M}" xmlns:y="{W}"><y:rPr><y:b/></y:rPr></x:r>')
    assert raw_xml(element) == "<m:r><w:rPr><w:b/></w:rPr></m:r>"


def test_display_equations_and_whole_equation_fallback(tmp_path: Path) -> None:
    body = (
        "<w:p><m:oMathPara><m:oMath><m:r><m:t>E</m:t></m:r></m:oMath></m:oMathPara></w:p>"
        "<w:p><m:oMathPara><m:oMathParaPr><m:jc m:val='left'/></m:oMathParaPr>"
        "<m:oMath><m:r><m:t>L</m:t></m:r></m:oMath></m:oMathPara></w:p>"
    )
    document = read_docx(_docx(tmp_path, body)).document
    first, second = document.all_paragraphs()
    (display,) = first.content
    assert isinstance(display, ir.Math)
    assert display.display
    assert display.expr == (Run("E"),)
    (kept,) = second.content
    assert isinstance(kept, ir.RawXml)
    assert kept.xml.startswith('<m:oMathPara><m:oMathParaPr><m:jc m:val="left"/>')


def _docx(folder: Path, body: str) -> Path:
    """A minimal package around `body` (w:p elements)."""
    path = folder / "math.docx"
    document = f'<w:document xmlns:w="{W}" xmlns:m="{M}"><w:body>{body}</w:body></w:document>'
    etree.fromstring(document)  # well-formed
    with zipfile.ZipFile(path, "w") as package:
        package.writestr(
            "[Content_Types].xml",
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
            "</Types>",
        )
        package.writestr(
            "_rels/.rels",
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
            "</Relationships>",
        )
        package.writestr("word/document.xml", document)
    return path
