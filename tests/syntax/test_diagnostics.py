import pytest

from wordgenx.syntax import Source, parse
from wordgenx.syntax.diagnostics import Code, Diagnostic, Severity, error, render
from wordgenx.syntax.source import Span

CASES = [
    ("\\b{never closed", Code.UNCLOSED_BRACE, "{"),
    ("stray } here", Code.UNEXPECTED_CLOSING_BRACE, "}"),
    ("a { b }", Code.UNEXPECTED_OPENING_BRACE, "{"),
    ("\\x[a=1", Code.UNCLOSED_OPTIONS, "["),
    ("\\x[a b]", Code.INVALID_OPTION, "b"),
    ("\\x[=1]", Code.INVALID_OPTION, "="),
    ('\\x[s="open]', Code.UNCLOSED_STRING, '"'),
    ("a `tick", Code.UNCLOSED_CODE_SPAN, "`"),
    ("```\ncode", Code.UNCLOSED_CODE_FENCE, "```"),
    ("$$ x", Code.UNCLOSED_DISPLAY_MATH, "$$"),
    ("#{x}", Code.RESERVED_SYNTAX, "#{"),
    ("a \\? b", Code.INVALID_ESCAPE, "\\?"),
    ("*never closed", Code.UNCLOSED_DELIMITER, "*"),
    ("\\define[x]{y}", Code.MALFORMED_DEFINITION, "\\define"),
    ("\\let{name}", Code.MALFORMED_DEFINITION, "\\let{name}"),
]


@pytest.mark.parametrize(("text", "code", "marked"), CASES, ids=[c[1].name for c in CASES])
def test_error_codes_and_spans(text: str, code: Code, marked: str) -> None:
    result = parse(text)
    matching = [d for d in result.diagnostics if d.code is code]
    assert matching, result.render_diagnostics()
    assert result.source.slice(matching[0].span) == marked


def test_unclosed_delimiter_is_only_a_warning() -> None:
    result = parse("price *approx")
    assert result.ok
    assert [d.severity for d in result.diagnostics] == [Severity.WARNING]


def test_valid_input_has_no_diagnostics() -> None:
    assert parse("Fine *text* with \\b{commands}.").diagnostics == ()


def test_errors_recover_and_continue() -> None:
    result = parse("a } b\n\n\\x[=1] c\n\n= Heading")
    assert len([d for d in result.diagnostics if d.is_error]) == 2
    assert type(result.ast.blocks[-1]).__name__ == "Heading"


def test_render_format() -> None:
    source = Source("chapter.wgx", "Intro.\n\\p[style=Body, space-befor=12pt]{x}\n")
    diagnostic = error(
        Code.INVALID_OPTION,
        "unknown paragraph property `space-befor`",
        Span(22, 33),
        "did you mean `space-before`?",
    )
    assert render(diagnostic, source) == (
        "error[WGX0104]: unknown paragraph property `space-befor`\n"
        " --> chapter.wgx:2:16\n"
        "  |\n"
        "2 | \\p[style=Body, space-befor=12pt]{x}\n"
        "  |                ^^^^^^^^^^^ did you mean `space-before`?\n"
    )


def test_render_with_help_tabs_and_multiline_span() -> None:
    source = Source("t.wgx", "\tx = {\nnext line\n")
    diagnostic = Diagnostic(Severity.WARNING, Code.UNCLOSED_GROUP, "msg", Span(5, 12), help="do X")
    assert render(diagnostic, source) == (
        "warning[WGX0115]: msg\n"
        " --> t.wgx:1:6\n"
        "  |\n"
        "1 |     x = {\n"
        "  |         ^\n"
        "  |\n"
        "  = help: do X\n"
    )


def test_diagnostics_are_sorted_by_position() -> None:
    result = parse("} a\n\\x[=1]\n#{")
    starts = [d.span.start for d in result.diagnostics]
    assert starts == sorted(starts)
