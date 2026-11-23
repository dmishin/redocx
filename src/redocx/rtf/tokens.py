r"""RTF tokens: groups, control words and symbols, hex bytes, text and binary data.

The tokenizer is total: any bytes give a token stream, so a damaged file reads as far as it
makes sense. Line breaks in RTF text carry no meaning and are dropped; `\binN` takes the
next N bytes as they are.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass


@dataclass(frozen=True)
class GroupStart:
    pass


@dataclass(frozen=True)
class GroupEnd:
    pass


@dataclass(frozen=True)
class Word:
    r"""A control word: `\b`, `\fs24`, `\li-360`. `param` is None when it has none."""

    name: str
    param: int | None = None


@dataclass(frozen=True)
class Symbol:
    r"""A control symbol: `\~`, `\-`, `\_`, `\*`, `\|`, `\:`; `\\`, `\{` and `\}` are text."""

    char: str


@dataclass(frozen=True)
class Hex:
    r"""A byte given as `\'hh`."""

    value: int


@dataclass(frozen=True)
class Text:
    data: bytes


@dataclass(frozen=True)
class Binary:
    r"""The data of `\binN`."""

    data: bytes


Token = GroupStart | GroupEnd | Word | Symbol | Hex | Text | Binary

_TOKEN = re.compile(
    rb"""
    \\(?P<word>[a-zA-Z]{1,32})(?P<param>-?[0-9]{1,10})?\x20?
    | \\'(?P<hex>[0-9a-fA-F]{2})
    | \\(?P<symbol>.)
    | (?P<open>\{)
    | (?P<close>\})
    | (?P<text>[^\\{}\r\n]+)
    | [\r\n]+
    | \\\Z
    """,
    re.VERBOSE | re.DOTALL,
)
_KINDS = ("word", "hex", "symbol", "open", "close", "text")
_GROUP_START = GroupStart()
_GROUP_END = GroupEnd()
_MAX_PARAM = 2**31 - 1


def tokenize(data: bytes) -> Iterator[Token]:
    position, end = 0, len(data)
    while position < end:
        match = _TOKEN.match(data, position)
        if match is None:  # every byte matches one alternative; this is a safety net
            position += 1
            continue
        position = match.end()
        # Not match.lastgroup: for `\fs24` that is "param", the group inside the word.
        kind = next((name for name in _KINDS if match[name] is not None), None)
        if kind == "word":
            name = match["word"].decode("ascii")
            raw = match["param"]
            param = None if raw is None else max(-_MAX_PARAM, min(_MAX_PARAM, int(raw)))
            if name == "bin" and param is not None and param > 0:
                # The delimiting space (if any) is already consumed; the data follows.
                yield Binary(data[position : position + param])
                position += param
                continue
            yield Word(name, param)
        elif kind == "hex":
            yield Hex(int(match["hex"], 16))
        elif kind == "symbol":
            char = match["symbol"].decode("latin-1")
            if char in "\\{}":
                yield Text(char.encode("latin-1"))
            elif char in "\r\n":
                yield Word("par")  # a backslash before a line break is \par
            else:
                yield Symbol(char)
        elif kind == "open":
            yield _GROUP_START
        elif kind == "close":
            yield _GROUP_END
        elif kind == "text":
            yield Text(match["text"])
