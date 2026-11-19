"""`redocx.escape`: any text written through it compiles back to the same text."""

from hypothesis import example, given, settings
from hypothesis import strategies as st

from redocx import escape, ir
from redocx.compiler import compile_source

# Biased towards characters and sequences that mean something in markup.
ALPHABET = [
    *"\\%~*_`<>[]()@#{}|=-.,:;\"'$!0123456789abcxyzABC ",
    "\N{NO-BREAK SPACE}",
    "a. ",
    "1. ",
    "- ",
    "= ",
    "$$",
    "```",
    "#{",
    "#name",
    "{#a:b}",
    "@ref",
    "<https://x.y>",
    "\\b{x}",
    " % c",
    "](x)",
]
words = st.lists(st.sampled_from(ALPHABET), max_size=40).map("".join)

PREAMBLE = "\\document[smart-quotes=off]\n\n"


def _paragraphs(source: str) -> list[str]:
    result = compile_source(PREAMBLE + source)
    assert not result.diagnostics, result.render_diagnostics()
    assert result.document is not None
    return [b.text() for b in result.document.body if isinstance(b, ir.Paragraph)]


def _expected(text: str) -> str:
    return " ".join(text.replace("\N{NO-BREAK SPACE}", "\x00").split()).replace(
        "\x00", "\N{NO-BREAK SPACE}"
    )


@settings(max_examples=300, deadline=None)
@given(words)
@example("- not a list")
@example("a. not a list")
@example("= not a heading")
@example("| not | a table |")
@example("*not strong* and _not emphasis_ and snake_case")
@example("100% sure % not a comment")
@example("#{reserved} and #param")
def test_text_in_a_paragraph_reads_back_the_same(text: str) -> None:
    if not text.strip():
        return
    assert _paragraphs(escape.text(text)) == [_expected(text)]


@settings(max_examples=200, deadline=None)
@given(words)
def test_text_in_a_p_body_reads_back_the_same(text: str) -> None:
    assert _paragraphs(f"\\p{{{escape.text(text)}}}") == [_expected(text)]


@settings(max_examples=200, deadline=None)
@given(words)
def test_text_in_a_pipe_cell_reads_back_the_same(text: str) -> None:
    if not text.strip():
        return
    result = compile_source(f"{PREAMBLE}| {escape.inline(text)} | b |\n")
    assert not result.diagnostics, result.render_diagnostics()
    assert result.document is not None
    (table,) = [b for b in result.document.body if isinstance(b, ir.Table)]
    (paragraph,) = table.rows[0].cells[0].content
    assert isinstance(paragraph, ir.Paragraph)
    assert paragraph.text() == _expected(text)


def test_text_keeps_line_breaks_as_source_line_breaks() -> None:
    assert _paragraphs(escape.text("- one\n= two\n\n1. three")) == ["- one = two", "1. three"]


@settings(max_examples=200, deadline=None)
@given(words)
def test_value_reads_back_the_same(text: str) -> None:
    result = compile_source(f"\\meta[title={escape.value('t' + text)}]\n\nx\n")
    assert not result.diagnostics, result.render_diagnostics()
    assert result.document is not None
    assert result.document.meta.title == "t" + text


@settings(max_examples=200, deadline=None)
@given(words)
def test_string_reads_back_the_same(text: str) -> None:
    result = compile_source(f"{PREAMBLE}\\url{{x{escape.string(text)}}}\n")
    assert result.document is not None
    (paragraph,) = [b for b in result.document.body if isinstance(b, ir.Paragraph)]
    (link,) = [i for i in paragraph.content if isinstance(i, ir.Hyperlink)]
    assert link.url == ("x" + text).strip()
