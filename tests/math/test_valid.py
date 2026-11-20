"""Every math tree is written as valid OMML: the schemas accept it, and the build-time check
of math objects (RDX0401) finds nothing."""

from __future__ import annotations

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
    Row,
    Run,
    Scripts,
    SPre,
)
from redocx.math.omml import omml
from redocx.ooxml.integrity import math_problems, package_problems
from redocx.ooxml.validate import schema
from redocx.ooxml.writer import build_package
from redocx.ooxml.xml import E, nsmap
from tests.math.test_read import colors, math_runs, text_runs


def _rows(children: st.SearchStrategy[Node]) -> st.SearchStrategy[Row]:
    return st.lists(children, max_size=3).map(tuple)


def _separated(beg: str, end: str, e: Row, sep: str, rest: list[Row]) -> Delim:
    """A delimiter with parts after separators, or one part with the default separator."""
    return Delim(beg, end, e, sep=sep, rest=tuple(rest)) if rest else Delim(beg, end, e)


def _objects(children: st.SearchStrategy[Node]) -> st.SearchStrategy[Node]:
    """Any shape, empty arguments included: what no parser makes must still be valid."""
    row = _rows(children)
    maybe = st.none() | row
    return st.one_of(
        st.builds(Frac, row, row, st.sampled_from(["bar", "noBar", "lin", "skw"]), color=colors),
        st.builds(Rad, row, maybe),
        st.builds(Scripts, row, maybe, maybe),
        st.builds(
            Nary, st.sampled_from("∑∫"), maybe, maybe, row, st.sampled_from(["undOvr", "subSup"])
        ),
        st.builds(Delim, st.sampled_from(["(", ""]), st.sampled_from([")", ""]), row),
        st.builds(Func, row, row),
        st.builds(
            BorderBox,
            row,
            st.sampled_from([(), BOX_SIDES, ("top",)]),
            st.sampled_from([(), ("bltr",), ("bltr", "tlbr"), ("h",)]),
            color=colors,
        ),
        st.builds(Phant, row, st.booleans(), st.booleans(), st.booleans(), st.booleans()),
        st.builds(Limit, st.sampled_from(["low", "upp"]), row, row),
        st.builds(Accent, st.just("̂"), row),
        st.builds(Bar, st.sampled_from(["top", "bot"]), row),
        st.builds(
            GroupChr,
            st.just("⏞"),
            st.sampled_from(["top", "bot"]),
            row,
            st.sampled_from(["", "bot"]),
        ),
        st.builds(
            _separated, st.just("("), st.just(")"), row, st.just("|"), st.lists(row, max_size=2)
        ),
        st.builds(SPre, row, row, row),
        st.builds(
            Matrix,
            st.lists(st.lists(row, max_size=3).map(tuple), max_size=3).map(tuple),
            st.sampled_from(["center", "left"]),
        ),
        st.builds(
            EqArr, st.lists(_rows(st.one_of(children, st.just(Align()))), max_size=3).map(tuple)
        ),
    )


trees = st.recursive(st.one_of(math_runs, text_runs, st.just(Align())), _objects, max_leaves=10)


@settings(max_examples=400, deadline=None)
@given(_rows(trees), st.booleans())
def test_any_tree_is_valid_omml(row: Row, display: bool) -> None:
    math = omml(row, display=display)
    document = E("w:document", E("w:body", E("w:p", math)), nsmap=nsmap("w"))
    xsd = schema()
    assert xsd.validate(document), [e.message for e in xsd.error_log]
    body = (ir.Paragraph((ir.Math(row, display),)),)
    package = build_package(ir.Document(body=body))
    assert math_problems(package) == []
    assert package_problems(package) == []


def test_empty_scripts_are_empty_arguments() -> None:
    math = omml((Scripts((Run("y"),), (), ()),), display=False)
    (scripts,) = math
    assert [etree.QName(child).localname for child in scripts] == ["sSubSupPr", "e", "sub", "sup"]
