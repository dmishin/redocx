"""Highlighted code blocks (design §3.15)."""

from __future__ import annotations

from pygments.token import Comment, Keyword, Name, Operator, String, Text

from wordgenx import ir
from wordgenx.compiler import compile_source
from wordgenx.highlight import code_style, highlight, style_name
from wordgenx.values import Color, Length

PYTHON = "```python\ndef f(x):\n    # note\n    return x + 1\n```"


def compiled(text: str) -> ir.Document:
    result = compile_source(text)
    assert result.document is not None, result.render_diagnostics()
    return result.document


def code_lines(doc: ir.Document) -> list[list[tuple[str, str | None]]]:
    return [
        [(r.text, r.style) for r in p.runs()]
        for p in doc.all_paragraphs()
        if p.style == "Code Block"
    ]


def test_token_styles() -> None:
    assert style_name(Keyword.Namespace) == "Code Keyword"
    assert style_name(Keyword.Type) == "Code Type"
    assert style_name(String.Doc) == "Code String"
    assert style_name(Comment.Preproc) == "Code Preprocessor"
    assert style_name(Name.Function) == "Code Function"
    assert style_name(Operator) == "Code Operator"
    assert style_name(Text) is None


def test_highlight_keeps_lines() -> None:
    lines = highlight("a = 1\n\n# c", "python")
    assert lines is not None
    assert len(lines) == 3
    assert lines[1] == []
    assert lines[2] == [("# c", "Code Comment")]
    assert highlight("x", "no-such-language") is None


def test_own_languages() -> None:
    lines = highlight('\\p[style="Body"]{x} % note\n= Title {#sec:a}', "wgx")
    assert lines is not None
    assert lines[0][0] == ("\\p", "Code Keyword")
    assert ('"Body"', "Code String") in lines[0]
    assert ("% note", "Code Comment") in lines[0]
    assert lines[1][-1] == ("{#sec:a}", "Code Label")
    sheet = highlight("paragraph X { space-after: 6pt; color: #FF0000; }", "wgxs")
    assert sheet is not None
    assert ("paragraph", "Code Keyword") in sheet[0]
    assert ("space-after", "Code Attribute") in sheet[0]


def test_themes() -> None:
    assert code_style("Code Keyword").run.get("color") == Color.parse("#0000FF")  # vs
    friendly = code_style("Code Comment", "friendly").run
    assert (friendly.get("italic"), friendly.get("color")) == (True, Color.parse("#60A0B0"))


def test_code_blocks_are_highlighted() -> None:
    doc = compiled(PYTHON)
    lines = code_lines(doc)
    assert lines[0] == [("def", "Code Keyword"), (" f(x):", None)]  # vs leaves names plain
    assert lines[1] == [("    ", None), ("# note", "Code Comment")]
    assert lines[2][-1] == (" x + 1", None)  # the vs theme leaves operators plain: no style
    styles = {s.name for s in doc.styles}
    assert {"Code Keyword", "Code Comment"} <= styles
    assert "Code Operator" not in styles


def test_style_sheets_restyle_tokens() -> None:
    doc = compiled('\\styles{ character "Code Operator" { bold; } }\n\n' + PYTHON)
    assert ("+", "Code Operator") in code_lines(doc)[2]


def test_code_theme_setting() -> None:
    doc = compiled("\\settings[code-theme=friendly]\n\n" + PYTHON)
    keyword = next(s for s in doc.styles if s.name == "Code Keyword")
    assert keyword.run.get("bold") is True
    result = compile_source("\\settings[code-theme=nope]")
    assert any("unknown code theme" in d.message for _, d in result.diagnostics)


def test_line_numbers() -> None:
    doc = compiled("```python {line-numbers, first-line=9}\na\nb\n```")
    first, second = (p for p in doc.all_paragraphs() if p.style == "Code Block")
    assert [r.text for r in first.runs()][:2] == [" 9", "\t"]
    assert first.runs()[0].style == "Code Line Number"
    assert [r.text for r in second.runs()][:2] == ["10", "\t"]
    hang = first.props.get("hanging-indent")
    assert isinstance(hang, Length)
    assert first.props.get("indent-left") == hang


def test_highlight_off_and_unknown_languages() -> None:
    doc = compiled("```python {highlight=off}\ndef f(): pass\n```")
    assert code_lines(doc) == [[("def f(): pass", None)]]
    result = compile_source("```pyhton\nx\n```")
    (diagnostic,) = [d for _, d in result.diagnostics]
    assert "no highlighting for the language `pyhton`" in diagnostic.message
    assert diagnostic.help == "did you mean `python`?"
