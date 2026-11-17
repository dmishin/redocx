"""Diagnostics point at the right file and line, with the macro calls they came through."""

import contextlib
import io
from pathlib import Path

import pytest

from redocx import compiler
from redocx.cli import main
from redocx.compiler import CompileResult, compile_source
from redocx.ooxml.parts.styles import StyleError
from redocx.syntax.diagnostics import Code
from redocx.syntax.source import Source


def located(result: CompileResult) -> list[tuple[str, int, str, tuple[str, ...]]]:
    """(file, line, message, notes) of each diagnostic; rendering must not fail."""
    result.render_diagnostics()
    out: list[tuple[str, int, str, tuple[str, ...]]] = []
    for source, d in result.diagnostics:
        out.append((Path(source.name).name, source.position(d.span.start).line, d.message, d.notes))
    return out


def compile_in(folder: Path, text: str) -> CompileResult:
    path = folder / "doc.rdx"
    path.write_text(text, encoding="utf-8")
    return compile_source(Source.from_path(path), folder)


def test_errors_in_a_macro_library_point_into_it(tmp_path: Path) -> None:
    (tmp_path / "lib.rdx").write_text(
        "% library\n\n\\define{logo}{\\image{missing.png}}\n", encoding="utf-8"
    )
    result = compile_in(tmp_path, "\\use{lib.rdx}\n\nText \\logo here.\n")
    [(file, line, message, notes)] = located(result)
    assert (file, line) == ("lib.rdx", 3)
    assert message.startswith("cannot read image `missing.png`")
    assert notes == (f"in `\\logo`, called at {tmp_path / 'doc.rdx'}:3:6",)


def test_nested_calls_are_all_noted(tmp_path: Path) -> None:
    (tmp_path / "lib.rdx").write_text(
        "\\define{inner}{\\b[x]{y}}\n\\define{outer}{\\inner}\n", encoding="utf-8"
    )
    [(file, line, message, notes)] = located(compile_in(tmp_path, "\\use{lib.rdx}\n\\outer\n"))
    assert (file, line, message) == ("lib.rdx", 1, "unknown option `x` for `\\b`")
    assert [n.split(", called at ")[0] for n in notes] == ["in `\\inner`", "in `\\outer`"]
    assert notes[0].endswith("lib.rdx:2:16")


def test_a_misspelt_parameter_is_suggested() -> None:
    result = compile_source("\\define{greet}[name]{Hello #nmae}\n\\greet[name=Ann]")
    [(_, d)] = result.diagnostics
    assert (d.code, d.message, d.label) == (
        Code.MACRO,
        "unknown parameter `#nmae`",
        "did you mean `#name`?",
    )
    assert d.notes == ("in `\\greet`, called at <input>:2:1",)


def test_a_macro_bringing_blocks_into_text_is_reported_at_the_call() -> None:
    result = compile_source("\\use{std:callout}\n\nText \\note{x} more.")
    [(file, line, message, _)] = located(result)
    assert (file, line) == ("<input>", 3)
    assert message == "`\\note` brings in `\\table`, which cannot appear inside a paragraph"


def test_blocks_in_text_are_named() -> None:
    [(_, _, message, _)] = located(compile_source("Text \\table{| a |} more."))
    assert message == "`\\table` is a block command and cannot appear inside a paragraph"


def test_a_list_level_style_is_reported_once_in_its_style_sheet() -> None:
    result = compile_source(
        '\\styles{numbering Steps { level 1 { text: "%1."; style: "Nope"; } }}\n\n'
        "\\list[Steps]{\n1. one\n}"
    )
    assert [d.message for _, d in result.diagnostics] == [
        "numbering `Steps` level 1: style `Nope` is not defined"
    ]


def test_a_style_linked_to_two_list_levels_is_a_located_error() -> None:
    text = (
        "\\styles{\n"
        'numbering A { level 1 { text: "%1."; style: "Heading 1"; } }\n'
        'numbering B { level 1 { text: "%1."; style: "Heading 1"; } }\n'
        "}\n\n= Title"
    )
    [(_, line, message, _)] = located(compile_source(text))
    assert (line, message) == (
        3,
        "style `Heading 1` is linked to two list levels: A level 1 and B level 1",
    )


def test_an_unclosed_verbatim_body_causes_no_further_errors() -> None:
    result = compile_source("\\m{\\frac{1}{}\n\n\\cite{x}\n")
    assert [d.code for _, d in result.diagnostics] == [Code.UNCLOSED_BRACE]


def test_files_are_listed_in_the_order_they_were_reported(tmp_path: Path) -> None:
    (tmp_path / "part.rdx").write_text("\\bogus", encoding="utf-8")
    result = compile_in(tmp_path, "\\include{part.rdx}\n\n\\alsobogus\n")
    assert [file for file, *_ in located(result)] == ["doc.rdx", "part.rdx"]


def run(*argv: str) -> tuple[int, str]:
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        code = main(list(argv))
    return code, err.getvalue()


def test_a_bad_template_option_is_a_command_line_error(tmp_path: Path) -> None:
    doc = tmp_path / "doc.rdx"
    doc.write_text("Hello", encoding="utf-8")
    (tmp_path / "fake.dotx").write_text("x", encoding="utf-8")
    code, err = run("build", str(doc), "--template", str(tmp_path / "fake.dotx"))
    assert code == 1
    assert err.startswith("error: --template: `fake.dotx` is not a Word document")
    assert "-->" not in err


def test_internal_errors_are_reported_without_a_traceback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail(*args: object, **kwargs: object) -> object:
        raise StyleError("something the lowering missed")

    monkeypatch.setattr(compiler, "build_package", fail)
    doc = tmp_path / "doc.rdx"
    doc.write_text("Hello", encoding="utf-8")
    for command in ("build", "check"):
        code, err = run(command, str(doc))
        assert code == 1
        assert "internal error while writing the document: something the lowering missed" in err
        assert "please report it" in err
        assert "Traceback" not in err
