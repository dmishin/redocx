"""AST shape for each construct, as compact s-expressions."""

import pytest

from redocx.syntax import ast, parse


def blocks(text: str) -> list[str]:
    result = parse(text)
    assert result.ok, result.render_diagnostics()
    return [ast.dump(block) for block in result.ast.blocks]


def one(text: str) -> str:
    (block,) = blocks(text)
    return block


CASES = [
    # Paragraphs and text
    ("Hello world.", '(paragraph (text "Hello world."))'),
    ("one\ntwo", '(paragraph (text "one") (soft_break) (text "two"))'),
    ("one\n   two", '(paragraph (text "one") (soft_break) (text "two"))'),
    ("12% of $48.2M", '(paragraph (text "12% of $48.2M"))'),
    ("a % comment\nb", '(paragraph (text "a ") (soft_break) (text "b"))'),
    ("100%done", '(paragraph (text "100%done"))'),
    ("a~b", '(paragraph (text "a") (non_breaking_space) (text "b"))'),
    (
        r"\* \_ \% \{ \} \\ \` \$ \# \= \| \@ \~ \[ \]",
        r'(paragraph (text "* _ % { } \\ ` $ # = | @ ~ [ ]"))',
    ),
    (r"literal \#{ here", '(paragraph (text "literal #{ here"))'),
    # Emphasis and flanking
    (
        "*strong* and _emph_",
        '(paragraph (strong (text "strong")) (text " and ") (emph (text "emph")))',
    ),
    ("*a _b_ c*", '(paragraph (strong (text "a ") (emph (text "b")) (text " c")))'),
    ("snake_case_name", '(paragraph (text "snake_case_name"))'),
    ("5 * 3 * 2", '(paragraph (text "5 * 3 * 2"))'),
    ("2*3*4", '(paragraph (text "2*3*4"))'),
    ("*multi\nline*", '(paragraph (strong (text "multi") (soft_break) (text "line")))'),
    # Code, links, references
    ("`a*b_c`", '(paragraph (code_span "a*b_c"))'),
    ("``a ` b``", '(paragraph (code_span "a ` b"))'),
    ("[the site](https://x.org)", '(paragraph (link (text "the site") "https://x.org"))'),
    ("[not a link] here", '(paragraph (text "[not a link] here"))'),
    ("<https://x.org/a_b>", '(paragraph (autolink "https://x.org/a_b"))'),
    ("a < b > c", '(paragraph (text "a < b > c"))'),
    ("see @fig:arch.", '(paragraph (text "see ") (ref "fig:arch") (text "."))'),
    ("(@sec:a)", '(paragraph (text "(") (ref "sec:a") (text ")"))'),
    ("jane@example.com", '(paragraph (text "jane@example.com"))'),
    # Commands
    (r"\pagebreak", '(paragraph (command "pagebreak"))'),
    (r"\b{bold}", '(paragraph (command "b" (markup_body (paragraph (text "bold")))))'),
    (r"\product{}s", '(paragraph (command "product" (markup_body)) (text "s"))'),
    (r"The \product is", '(paragraph (text "The ") (command "product") (text " is"))'),
    (r"\embed-docx{a.docx}", '(paragraph (command "embed-docx" (verbatim_body :string "a.docx")))'),
    (
        r"\image{a_b~c%20.png}",
        '(paragraph (command "image" (verbatim_body :string "a_b~c%20.png")))',
    ),
    (r"\url{a\}b}", '(paragraph (command "url" (verbatim_body :string "a}b")))'),
    (
        r'\field{IF { MERGEFIELD x } = 1 "y" \* MERGEFORMAT}',
        r'(paragraph (command "field" '
        r'(verbatim_body :raw "IF { MERGEFIELD x } = 1 \"y\" \\* MERGEFORMAT")))',
    ),
    (
        r"\m{\left\{ x \right.}",
        r'(paragraph (command "m" (verbatim_body :math "\\left\\{ x \\right.")))',
    ),
    # Options
    (
        r'\x[a=1, b="two words", c, d=(1cm, at-least), e=theme.accent1.darker(25%)]',
        '(paragraph (command "x" (options (option "a" (bare_value "1")) '
        '(option "b" (string_value "two words")) (option (bare_value "c")) '
        '(option "d" (tuple_value (option (bare_value "1cm")) (option (bare_value "at-least")))) '
        '(option "e" (bare_value "theme.accent1.darker(25%)")))))',
    ),
    (
        "\\x[\n  a = 1,  % note\n  b = {*rich*},\n]",
        '(paragraph (command "x" (options (option "a" (bare_value "1")) '
        '(option "b" (markup_body (paragraph (strong (text "rich"))))))))',
    ),
    (
        r'\x[s="say \"hi\" \\ ok"]',
        r'(paragraph (command "x" (options (option "s" (string_value "say \"hi\" \\ ok")))))',
    ),
    # Definitions
    (
        r"\define{note}[kind=info]{Note #kind: #body}",
        '(paragraph (define "note" (options (option "kind" (bare_value "info"))) '
        '(markup_body (paragraph (text "Note ") (param "kind") (text ": ") (param "body")))))',
    ),
    (r"\let{p}{Prod}", '(paragraph (let "p" (markup_body (paragraph (text "Prod")))))'),
    ("#hashtag outside a macro", '(paragraph (text "#hashtag outside a macro"))'),
    # Headings
    ("= Title", '(heading 1 :numbered (text "Title"))'),
    ("=== Deep {#sec:deep}", '(heading 3 :numbered (text "Deep") (label "sec:deep"))'),
    ("==* Unnumbered", '(heading 2 (text "Unnumbered"))'),
    ("=no space", '(paragraph (text "=no space"))'),
    # Display math and code
    ("$$ x^2 $$ {#eq:1}", '(display_math "x^2" (label "eq:1"))'),
    (
        '```py {#lst:a, caption="C"}\nx = {1}\n  y\n```',
        '(code_block "py" (options (option "id" (bare_value "lst:a")) '
        '(option "caption" (string_value "C"))) "x = {1}\\n  y")',
    ),
    ("  ```\n  indented\n    more\n  ```", '(code_block "indented\\n  more")'),
    # Pipe tables
    (
        "| a | `x|y` |\n| \\cell[colspan=2]{b} ||",
        '(pipe_table (pipe_row (pipe_cell (text "a")) (pipe_cell (code_span "x|y"))) '
        '(pipe_row (pipe_cell (command "cell" (options (option "colspan" (bare_value "2"))) '
        '(markup_body (paragraph (text "b"))))) (pipe_cell)))',
    ),
]


@pytest.mark.parametrize(("text", "expected"), CASES, ids=[c[0][:30] for c in CASES])
def test_ast(text: str, expected: str) -> None:
    assert one(text) == expected


def test_paragraphs_are_separated_by_blank_lines() -> None:
    assert blocks("a\n\n\nb") == ['(paragraph (text "a"))', '(paragraph (text "b"))']


def test_comment_only_lines_do_not_split_paragraphs() -> None:
    assert blocks("a\n% note\nb") == ['(paragraph (text "a") (soft_break) (text "b"))']


def test_lists() -> None:
    text = "- one\n- two\n  cont\n  - nested\n1. first\n   a. letter\n"
    assert blocks(text) == [
        '(list_block (list_item "-" (paragraph (text "one"))) '
        '(list_item "-" (paragraph (text "two") (soft_break) (text "cont")) '
        '(list_block (list_item "-" (paragraph (text "nested"))))))',
        '(list_block :ordered (list_item "1." (paragraph (text "first")) '
        '(list_block :ordered (list_item "a." (paragraph (text "letter"))))))',
    ]


def test_list_item_labels_and_paragraphs() -> None:
    text = "1. {#cl:a} Clause\n\n   Second paragraph.\n2. Next"
    assert blocks(text) == [
        '(list_block :ordered (list_item "1." (label "cl:a") (paragraph (text "Clause")) '
        '(paragraph (text "Second paragraph."))) (list_item "2." (paragraph (text "Next"))))'
    ]


def test_letter_marker_only_inside_lists() -> None:
    assert blocks("a. not a list") == ['(paragraph (text "a. not a list"))']


def test_dedent_ends_list_item() -> None:
    assert blocks("- item\nafter") == [
        '(list_block (list_item "-" (paragraph (text "item"))))',
        '(paragraph (text "after"))',
    ]


def test_blocks_inside_command_bodies() -> None:
    text = "\\cell{\n  First.\n\n  - a\n  - b\n}"
    assert one(text) == (
        '(paragraph (command "cell" (markup_body (paragraph (text "First.")) '
        '(list_block (list_item "-" (paragraph (text "a"))) '
        '(list_item "-" (paragraph (text "b")))))))'
    )


def test_pipe_row_right_after_opening_brace() -> None:
    assert one("\\table{| a | b |}") == (
        '(paragraph (command "table" (markup_body (pipe_table (pipe_row '
        '(pipe_cell (text "a")) (pipe_cell (text "b")))))))'
    )


def test_crlf_line_endings() -> None:
    assert blocks("a\r\nb\r\n\r\nc") == [
        '(paragraph (text "a") (soft_break) (text "b"))',
        '(paragraph (text "c"))',
    ]


def test_spans_point_into_source() -> None:
    text = "Intro *bold* text."
    result = parse(text)
    paragraph = result.ast.blocks[0]
    assert isinstance(paragraph, ast.Paragraph)
    strong = paragraph.content[1]
    assert isinstance(strong, ast.Strong)
    assert result.source.slice(strong.span) == "*bold*"
