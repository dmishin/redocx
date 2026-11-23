"""The RTF tokenizer: control words, symbols, hex bytes, binary data; total on any input."""

from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st

from redocx.rtf.tokens import (
    Binary,
    GroupEnd,
    GroupStart,
    Hex,
    Symbol,
    Text,
    Word,
    tokenize,
)


def test_words_with_and_without_parameters() -> None:
    assert list(tokenize(rb"{\rtf1\b\fs24 Hi\b0 \li-360 x}")) == [
        GroupStart(),
        Word("rtf", 1),
        Word("b"),
        Word("fs", 24),
        Text(b"Hi"),
        Word("b", 0),
        Word("li", -360),
        Text(b"x"),
        GroupEnd(),
    ]


def test_the_space_after_a_word_is_part_of_it() -> None:
    assert list(tokenize(rb"\b  two")) == [Word("b"), Text(b" two")]
    assert list(tokenize(rb"\b;x")) == [Word("b"), Text(b";x")]


def test_hex_symbols_and_escaped_characters() -> None:
    assert list(tokenize(rb"\'e9\~\-\_\*\{\}\\")) == [
        Hex(0xE9),
        Symbol("~"),
        Symbol("-"),
        Symbol("_"),
        Symbol("*"),
        Text(b"{"),
        Text(b"}"),
        Text(b"\\"),
    ]


def test_line_breaks_are_dropped_and_a_backslash_before_one_is_a_paragraph() -> None:
    assert list(tokenize(b"a\r\nb\\\nc")) == [Text(b"a"), Text(b"b"), Word("par"), Text(b"c")]


def test_binary_data_is_taken_as_it_is() -> None:
    assert list(tokenize(b"\\bin3 {}\\x}")) == [Binary(b"{}\\"), Text(b"x"), GroupEnd()]


def test_a_bad_hex_escape_and_a_trailing_backslash() -> None:
    assert list(tokenize(rb"\'zz\'")) == [Symbol("'"), Text(b"zz"), Symbol("'")]
    assert list(tokenize(b"a\\")) == [Text(b"a")]


@settings(max_examples=300, deadline=None)
@given(st.binary(max_size=200))
def test_any_bytes_tokenize(data: bytes) -> None:
    list(tokenize(data))
