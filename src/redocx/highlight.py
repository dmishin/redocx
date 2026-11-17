"""Syntax highlighting of code blocks with Pygments (design §3.15).

Tokens become runs in character styles named after what they are (Code Keyword, Code
String, Code Comment, ...). The styles are ordinary Word styles: their default look comes
from a Pygments theme (`vs` unless `\\settings[code-theme=...]` says otherwise), and style
sheets or templates can restyle them like any other.
"""

from __future__ import annotations

import difflib
from functools import cache

from pygments.lexer import Lexer, RegexLexer, bygroups  # pyright: ignore[reportUnknownVariableType]
from pygments.lexers import get_all_lexers, get_lexer_by_name  # pyright: ignore[reportUnknownVariableType]
from pygments.styles import get_all_styles, get_style_by_name  # pyright: ignore[reportUnknownVariableType]
from pygments.token import (
    Comment,
    Generic,
    Keyword,
    Name,
    Number,
    Operator,
    Punctuation,
    String,
    Text,
    Whitespace,
    _TokenType,  # pyright: ignore[reportPrivateUsage]
)
from pygments.util import ClassNotFound

from redocx.ir import Style, StyleKind
from redocx.props import run_props

DEFAULT_THEME = "vs"

TOKEN_STYLES: tuple[tuple[_TokenType, str], ...] = (
    (Comment.Preproc, "Code Preprocessor"),
    (Comment, "Code Comment"),
    (Keyword.Type, "Code Type"),
    (Keyword.Constant, "Code Constant"),
    (Keyword, "Code Keyword"),
    (Operator.Word, "Code Keyword"),
    (Name.Builtin, "Code Builtin"),
    (Name.Function, "Code Function"),
    (Name.Class, "Code Type"),
    (Name.Decorator, "Code Decorator"),
    (Name.Attribute, "Code Attribute"),
    (Name.Tag, "Code Keyword"),
    (Name.Label, "Code Label"),
    (Name.Variable, "Code Variable"),
    (String.Escape, "Code Escape"),
    (String, "Code String"),
    (Number, "Code Number"),
    (Operator, "Code Operator"),
    (Generic.Heading, "Code Heading"),
    (Generic.Subheading, "Code Heading"),
    (Generic.Inserted, "Code Inserted"),
    (Generic.Deleted, "Code Deleted"),
)
"""Pygments token types (most specific first) -> redocx character style."""

STYLE_NAMES = frozenset(name for _, name in TOKEN_STYLES)
_REPRESENTATIVE = {name: token for token, name in reversed(TOKEN_STYLES)}
"""The token whose theme colours a style takes (the first listed for that name)."""


def style_name(token: _TokenType) -> str | None:
    """The character style for a token, or None for plain text."""
    for parent, name in TOKEN_STYLES:
        if token in parent:
            return name
    return None


def themes() -> list[str]:
    return sorted(get_all_styles())


@cache
def theme_formatting(theme: str, name: str) -> tuple[tuple[str, object], ...]:
    """Run properties a theme gives a code style (empty when it leaves it plain)."""
    style = get_style_by_name(theme).style_for_token(_REPRESENTATIVE[name])
    props: list[tuple[str, object]] = []
    if style.get("color"):
        props.append(("color", "#" + str(style["color"]).upper()))
    if style.get("bold"):
        props.append(("bold", True))
    if style.get("italic"):
        props.append(("italic", True))
    if style.get("underline"):
        props.append(("underline", "single"))
    return tuple(props)


def code_style(name: str, theme: str = DEFAULT_THEME) -> Style:
    """The built-in character style for a token kind, coloured by `theme`."""
    return Style(
        StyleKind.CHARACTER,
        name,
        run=run_props(**{k.replace("-", "_"): v for k, v in theme_formatting(theme, name)}),
        quick_format=False,
    )


# --- lexers for redocx's own languages --------------------------------------------------


class RedocxLexer(RegexLexer):
    """redocx documents (.rdx)."""

    name = "redocx"
    aliases = ["rdx", "redocx"]  # noqa: RUF012 (Pygments' class attributes)
    filenames = ["*.rdx"]  # noqa: RUF012
    tokens = {  # noqa: RUF012
        "root": [
            (r"(^|(?<=\s))%.*?$", Comment.Single),
            (
                r"^(=+\*?)(\s.*?)(\{#[A-Za-z][\w:.-]*\})?$",
                bygroups(Generic.Heading, Generic.Heading, Name.Label),
            ),
            (r"\\[\\%{}\[\]*_`$#=|@~]", String.Escape),
            (r"\\[A-Za-z][A-Za-z0-9-]*\*?", Keyword),
            (r"\{#[A-Za-z][\w:.-]*\}", Name.Label),
            (r"(?<![\w.])@[A-Za-z][\w:.-]*[\w]", Name.Label),
            (r"#[A-Za-z][\w-]*", Name.Variable),
            (r'"(\\\\|\\"|[^"\n])*"', String),
            (r"`[^`\n]*`", String.Backtick),
            (r"[{}\[\]]", Punctuation),
            (r"\s+", Whitespace),
            (r"[^\\%{}\[\]\"`@#\s]+", Text),
            (r".", Text),
        ]
    }


class RedocxStyleSheetLexer(RegexLexer):
    """redocx style sheets (.rdxs and \\styles{...} bodies)."""

    name = "redocx style sheet"
    aliases = ["rdxs"]  # noqa: RUF012
    filenames = ["*.rdxs"]  # noqa: RUF012
    tokens = {  # noqa: RUF012
        "root": [
            (r"(^|(?<=\s))%.*?$", Comment.Single),
            (
                r"\b(defaults|paragraph|character|linked|table|numbering|modify|replace|level)\b",
                Keyword,
            ),
            (
                r"\b(header-row|first-row|last-row|first-column|last-column|banded-rows|banded-columns|odd|even|top-left-cell|top-right-cell|bottom-left-cell|bottom-right-cell)\b",
                Keyword,
            ),
            (r'"(\\\\|\\"|[^"\n])*"', String),
            (r"#[0-9A-Fa-f]{6}\b", Number),
            (r"theme\.[A-Za-z0-9]+(\.(lighter|darker)\(\d+%\))?", Name.Builtin),
            (r"[A-Za-z][\w.-]*(?=\s*:)", Name.Attribute),
            (r"-?\d+(\.\d+)?(pt|cm|mm|in|pc|em|fr|%)?", Number),
            (r"[{}();:,=]", Punctuation),
            (r"\s+", Whitespace),
            (r"[^\s{}();:,=\"%#]+", Text),
            (r".", Text),
        ]
    }


_OWN_LEXERS: dict[str, type[Lexer]] = {
    alias: lexer for lexer in (RedocxLexer, RedocxStyleSheetLexer) for alias in lexer.aliases
}


@cache
def lexer_for(language: str) -> Lexer | None:
    key = language.lower()
    if key in _OWN_LEXERS:
        return _OWN_LEXERS[key](stripnl=False, ensurenl=False)
    try:
        return get_lexer_by_name(key, stripnl=False, ensurenl=False)
    except ClassNotFound:
        return None


@cache
def _aliases() -> tuple[str, ...]:
    names = {alias for _, aliases, _, _ in get_all_lexers() for alias in aliases}
    return tuple(sorted(names | set(_OWN_LEXERS)))


def suggest_language(language: str) -> str | None:
    close = difflib.get_close_matches(language.lower(), _aliases(), n=1)
    return close[0] if close else None


def highlight(code: str, language: str) -> list[list[tuple[str, str | None]]] | None:
    """Lines of (text, style name) for `code`, or None when no lexer knows the language."""
    lexer = lexer_for(language)
    if lexer is None:
        return None
    lines: list[list[tuple[str, str | None]]] = [[]]
    for token, value in lexer.get_tokens(code):
        name = style_name(token)
        for i, piece in enumerate(value.split("\n")):
            if i:
                lines.append([])
            if piece:
                line = lines[-1]
                if line and line[-1][1] == name:
                    line[-1] = (line[-1][0] + piece, name)
                else:
                    line.append((piece, name))
    expected = code.count("\n") + 1
    return (lines + [[] for _ in range(expected - len(lines))])[:expected]


__all__ = [
    "DEFAULT_THEME",
    "STYLE_NAMES",
    "code_style",
    "highlight",
    "lexer_for",
    "style_name",
    "suggest_language",
    "theme_formatting",
    "themes",
]
