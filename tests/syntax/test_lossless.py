"""Property tests: the parser never crashes, always terminates, and is lossless."""

import time

import pytest
from hypothesis import example, given, settings
from hypothesis import strategies as st

from redocx.syntax import ast, parse
from redocx.syntax.cst import Node, Token

# Biased towards characters that matter to the grammar.
ALPHABET = [
    *"\\%~*_`<>[]()@#{}|=-.,:;\"' \t\n\r$!0123456789abcxyzABC",
    "\\b",
    "\\p[",
    "\\field{",
    "\\m{",
    "\\define{x}",
    "\\let{y}",
    "```",
    "$$",
    "{#a:b}",
    "\n- ",
    "\n1. ",
    "\n  a. ",
    "\n= ",
    "\n| ",
    "https://x.y",
    " % c",
    "\r\n",
]

markup = st.lists(st.sampled_from(ALPHABET), max_size=60).map("".join)


def _check_tree(node: Node, text: str) -> None:
    offset = node.offset
    for child in node.children:
        if isinstance(child, Token):
            assert child.offset == offset, (child, offset)
            assert child.text, child
            assert text[child.offset : child.offset + len(child.text)] == child.text
            offset += len(child.text)
        else:
            assert child.offset == offset, (child.kind, child.offset, offset)
            _check_tree(child, text)
            offset = child.span.end


def _check_spans(node: ast.AstNode, length: int) -> None:
    assert 0 <= node.span.start <= node.span.end <= length
    for value in vars(node).values():
        children: tuple[object, ...] = value if isinstance(value, tuple) else (value,)  # pyright: ignore[reportUnknownVariableType]
        for child in children:
            if isinstance(child, ast.AstNode):
                _check_spans(child, length)


@settings(max_examples=600, deadline=None)
@given(markup)
@example("\\p[\\define{x}=")  # a command before `=`, where an option name belongs
def test_parser_is_lossless_and_total(text: str) -> None:
    result = parse(text)
    assert result.cst.text == text
    _check_tree(result.cst, text)
    _check_spans(result.ast, len(text))
    for diagnostic in result.diagnostics:
        assert 0 <= diagnostic.span.start <= diagnostic.span.end <= len(text)
        result.render_diagnostics()  # rendering never fails either


@settings(max_examples=200, deadline=None)
@given(st.text(max_size=80))
def test_arbitrary_unicode_text(text: str) -> None:
    assert parse(text).cst.text == text


def test_pathological_delimiters_are_fast() -> None:
    # Unclosed openers must not trigger exponential backtracking.
    for text in ("*a _b [c " * 400, "*a _b [c " * 100 + "d* e_ f](x)"):
        start = time.perf_counter()
        assert parse(text).cst.text == text
        assert time.perf_counter() - start < 5


@pytest.mark.parametrize("opener", ["\\b{", "{", "\\x[a=(", "*", "["])
def test_deep_nesting_is_an_error_not_a_crash(opener: str) -> None:
    text = opener * 300 + "x"
    assert parse(text).cst.text == text


def test_deeply_nested_lists() -> None:
    text = "".join(" " * (2 * i) + "- item\n" for i in range(200))
    assert parse(text).cst.text == text
