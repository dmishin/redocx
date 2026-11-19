from pathlib import Path

import pytest

from redocx import ir
from redocx.cli import main
from redocx.commands.builtin import COMMANDS
from redocx.commands.latex import LATEX
from redocx.compiler import build, compile_source
from redocx.explain import std_macros
from redocx.lower.preamble import std_modules
from redocx.syntax import ast, parse
from redocx.syntax.diagnostics import Code
from redocx.values import Color


def compiled(text: str) -> ir.Document:
    result = compile_source(text)
    assert result.document is not None, result.render_diagnostics()
    return result.document


def test_std_modules_are_packaged() -> None:
    assert {"callout", "signature"} <= set(std_modules())


def test_callouts() -> None:
    doc = compiled(
        "\\use{std:callout}\n\n\\note{Info.}\n\n\\warning[title=Careful]{Hot.\n\n- item}\n\n"
        "\\callout[shade=#EDE7F6]{Custom.}"
    )
    tables = [b for b in doc.body if isinstance(b, ir.Table)]
    shades = [t.rows[0].cells[0].props.get("shading") for t in tables]
    assert shades == [Color.parse(c) for c in ("#E8F0FA", "#FFF2CC", "#EDE7F6")]
    assert all(t.style is None for t in tables)  # borderless
    texts = [
        ["".join(r.text for r in p.runs()) for p in ir.iter_paragraphs(t.rows[0].cells[0].content)]
        for t in tables
    ]
    assert texts == [["Note", "Info."], ["Careful", "Hot.", "item"], ["Note", "Custom."]]


def test_signature_blocks() -> None:
    doc = compiled(
        '\\use{std:signature}\n\n\\signature-block[party="the Customer", name="Jane Doe"]\n\n\\approval'
    )
    paragraph, block, approval = doc.body
    assert isinstance(paragraph, ir.Paragraph)
    assert "".join(r.text for r in paragraph.runs()) == "Signed for and on behalf of the Customer:"
    assert isinstance(block, ir.Table)
    assert isinstance(approval, ir.Table)
    first_cell = next(ir.iter_paragraphs(block.rows[0].cells[0].content))
    assert first_cell.runs()[1].text == "\t" * 5  # an underlined line made of tabs


def test_std_is_loaded_once_and_unknown_modules_are_errors() -> None:
    result = compile_source("\\use{std:callout}\n\\use{std:callout}\n\\use{std:calout}")
    messages = [(d.code, d.message, d.label) for _, d in result.diagnostics]
    assert messages == [
        (
            Code.INCLUDE,
            "unknown standard library module `std:calout`",
            "did you mean `std:callout`?",
        )
    ]


def test_hex_colours_are_not_parameters_in_macros() -> None:
    result = parse("\\define{m}{\\cell[shade=#E8F0FA, x=#name]{}}")
    define = result.ast.blocks[0]
    assert isinstance(define, ast.Paragraph)
    macro = define.content[0]
    assert isinstance(macro, ast.Define)
    assert macro.body is not None
    body = macro.body.blocks[0]
    assert isinstance(body, ast.Paragraph)
    cell = body.content[0]
    assert isinstance(cell, ast.Command)
    assert cell.options is not None
    shade, name = cell.options.items
    assert isinstance(shade.value, ast.BareValue)
    assert isinstance(name.value, ast.ParamValue)


def test_text_options_accept_plain_markup() -> None:
    doc = compiled("\\meta[title={A long\n   title}]\n\nx")
    assert doc.meta.title == "A long title"
    result = compile_source("\\meta[title={*bold*}]")
    assert any("plain text in braces" in d.message for _, d in result.diagnostics)


def test_unclosed_string_reports_once() -> None:
    result = parse('\\x[a="unclosed\nmore words, b=1] after')
    assert [d.code for d in result.diagnostics] == [Code.UNCLOSED_STRING]
    assert result.cst.text == '\\x[a="unclosed\nmore words, b=1] after'


# --- loaded by default, and std:latex ---------------------------------------------------------


def unknown(text: str, std: frozenset[str] | tuple[()] | None = None) -> list[str]:
    result = compile_source(text, std=std)
    return [d.message for _, d in result.diagnostics if d.code is Code.UNKNOWN_COMMAND]


def test_std_is_loaded_by_default() -> None:
    assert unknown("\\note{Info.}\n\n\\textbf{x}") == []


def test_std_can_be_left_out() -> None:
    no_latex = frozenset(std_modules()) - {"latex"}
    assert unknown("\\note{Info.}\n\n\\textbf{x}", no_latex) == ["unknown command `\\textbf`"]
    assert unknown("\\note{Info.}\n\n\\textbf{x}", ()) == [
        "unknown command `\\note`",
        "unknown command `\\textbf`",
    ]
    # \use still loads a module that was left out.
    assert unknown("\\use{std:latex}\n\n\\textbf{x}", ()) == []


def test_a_document_macro_overrides_std() -> None:
    assert compiled("\\define{note}{\\i{#body}}\n\n\\note{x}").body == compiled("\\i{x}").body


def test_std_adds_nothing_to_an_unused_document() -> None:
    text = "= Title\n\nText."
    assert build(text)[0] == build(text, std=())[0]


@pytest.mark.parametrize(
    ("latex", "redocx"),
    [
        ("\\textbf{x} \\textit{y} \\underline{z}", "\\b{x} \\i{y} \\u{z}"),
        ("\\texttt{x} \\textsc{y}", "\\code{x} \\font[small-caps]{y}"),
        ("H\\textsubscript{2}O, x\\textsuperscript{2}", "H\\sub{2}O, x\\sup{2}"),
        ("a\\newline b, \\thepage, a\\ldots{}b\\dots", "a\\br b, \\pagenum, a…b…"),
        ("\\chapter{A}\n\n\\subsection{B}\n\n\\subsubsection{C}", "= A\n\n== B\n\n=== C"),
        ("\\newpage\n\n\\clearpage", "\\pagebreak\n\n\\pagebreak"),
        (
            "\\tableofcontents\n\n\\listoffigures\n\n\\listoftables",
            "\\toc\n\n\\listof[Figure]\n\n\\listof[Table]",
        ),
        ("\\today", "\\date"),
    ],
)
def test_latex_commands_are_their_redocx_form(latex: str, redocx: str) -> None:
    assert compiled(latex).body == compiled(redocx).body


def test_latex_module_names_have_hints() -> None:
    # Each name std:latex defines is in the hint table, so --no-latex still helps.
    names = {m.name for m in std_macros().values() if m.where.startswith("std:latex:")}
    assert names
    assert names <= set(LATEX)
    assert not names & set(COMMANDS)


@pytest.mark.parametrize(("flags", "code"), [([], 0), (["--no-latex"], 1), (["--no-std"], 1)])
def test_cli_flags(tmp_path: Path, flags: list[str], code: int) -> None:
    file = tmp_path / "doc.rdx"
    file.write_text("\\textbf{x}\n", encoding="utf-8")
    assert main(["check", *flags, str(file)]) == code
    assert main(["build", *flags, str(file)]) == code


def test_cli_flags_exclude_each_other(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        main(["check", "--no-std", "--no-latex", str(tmp_path / "doc.rdx")])
