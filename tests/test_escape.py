"""`redocx.escape`: any text written through it compiles back to the same text."""

from hypothesis import example, given, settings
from hypothesis import strategies as st

from redocx import escape, ir
from redocx.compiler import compile_source
from redocx.lower.typography import EN_DASH

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
    "# ",
    "> ",
    "+ ",
    "**",
    "![",
    "5 %",
    "|---|",
]
words = st.lists(st.sampled_from(ALPHABET), max_size=40).map("".join)

PREAMBLE = "\\document[smart-quotes=off]\n\n"


def _paragraphs(source: str) -> list[str]:
    result = compile_source(PREAMBLE + source)
    assert not result.diagnostics, result.render_diagnostics()
    assert result.document is not None
    return [b.text() for b in result.document.body if isinstance(b, ir.Paragraph)]


def _expected(text: str) -> str:
    """Escaped text reads back exactly; a single line break in the data reads as a space."""
    return text.replace("\n", " ")


@settings(max_examples=300, deadline=None)
@given(words)
@example("- not a list")
@example("a. not a list")
@example("= not a heading")
@example("| not | a table |")
@example("*not strong* and _not emphasis_ and snake_case")
@example("100% sure % not a comment")
@example("#{reserved} and #param")
@example("## not a heading\n> not a quote\n+ not an item\n**not bold** ![not](a picture) 50 %")
def test_text_in_a_paragraph_reads_back_the_same(text: str) -> None:
    if not text.strip():
        return
    assert _paragraphs(escape.text(text)) == [_expected(text)]


@settings(max_examples=200, deadline=None)
@given(words)
def test_text_in_a_p_body_reads_back_the_same(text: str) -> None:
    if not text.strip():
        return  # a line of only spaces is a blank line, as in any source
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


def test_quotes_dashes_and_spaces_are_kept_with_smart_punctuation_on() -> None:
    data = '  He said "don\'t" -- twice,   then left --- 5\'10".  '
    result = compile_source(f'"Quoted" -- {escape.inline(data)} -- "end"')
    assert not result.diagnostics, result.render_diagnostics()
    assert result.document is not None
    (paragraph,) = result.document.body
    assert isinstance(paragraph, ir.Paragraph)
    open_quote, close_quote = "\N{LEFT DOUBLE QUOTATION MARK}", "\N{RIGHT DOUBLE QUOTATION MARK}"
    assert paragraph.text() == (
        f"{open_quote}Quoted{close_quote} {EN_DASH} {data} {EN_DASH} {open_quote}end{close_quote}"
    )


def test_inline_turns_line_breaks_into_spaces() -> None:
    result = compile_source(f"| {escape.inline('two\nlines')} | b |\n| c | d |")
    assert not result.diagnostics, result.render_diagnostics()
    assert result.document is not None
    (table,) = result.document.body
    assert isinstance(table, ir.Table)
    assert len(table.rows) == 2
    (paragraph,) = table.rows[0].cells[0].content
    assert isinstance(paragraph, ir.Paragraph)
    assert paragraph.text() == "two lines"


def test_text_with_indent_stays_in_a_list_item() -> None:
    data = "first line\n- not an item\n\nsecond paragraph"
    source = f"1. {escape.text(data, indent=3)}\n2. next"
    result = compile_source(source)
    assert not result.diagnostics, result.render_diagnostics()
    assert result.document is not None
    items = [(p.text(), p.list_item) for p in result.document.body if isinstance(p, ir.Paragraph)]
    assert [text for text, _ in items] == [
        "first line - not an item",
        "second paragraph",
        "next",
    ]
    first, _, second = (ref for _, ref in items)
    assert first is not None
    assert second is not None
    assert first.instance == second.instance  # one list: the data did not split it


def test_lines_breaks_lines_inside_the_paragraph() -> None:
    source = f"\\p{{{escape.lines('Acme Ltd\n1 High St\n\nLondon')}}}"
    result = compile_source(source)
    assert not result.diagnostics, result.render_diagnostics()
    assert result.document is not None
    (paragraph,) = result.document.body
    assert isinstance(paragraph, ir.Paragraph)
    assert paragraph.text() == "Acme Ltd\n1 High St\n\nLondon"


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
