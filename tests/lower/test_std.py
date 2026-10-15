from wordgenx import ir
from wordgenx.compiler import compile_source
from wordgenx.lower.lowering import std_modules
from wordgenx.syntax import ast, parse
from wordgenx.syntax.diagnostics import Code
from wordgenx.values import Color


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
