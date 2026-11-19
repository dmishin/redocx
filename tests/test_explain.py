"""`redocx explain`: commands, properties, macros and std: modules."""

from __future__ import annotations

from pathlib import Path

import pytest

from redocx.cli import main
from redocx.commands.builtin import COMMANDS
from redocx.explain import explain, overview


def test_a_command() -> None:
    text, found = explain("table")
    assert found
    assert text.startswith("\\table: A table of pipe rows and/or \\row commands.")
    assert "  Where:  a block of its own" in text
    assert "    header-rows  rows repeated on each page" in text
    assert "(redocx explain --properties table)" in text
    assert "More: docs/reference.md#cmd-table" in text


def test_a_latex_command() -> None:
    text, found = explain("textbf")
    assert found
    assert text.startswith("\\textbf is LaTeX, not redocx. Write `*x*` (Strong style) or `\\b{x}`.")
    assert 'See "Text" in docs/guide/tex.md' in text


def test_a_latex_environment_list() -> None:
    text, found = explain("begin")
    assert found
    assert "  {itemize}: Start each item on its own line with `- `." in text


def test_a_command_that_means_something_else_in_latex() -> None:
    text, found = explain("section")
    assert found
    assert text.startswith("\\section: ")
    assert "\\section means something else in LaTeX." in text


def test_a_part_and_a_leading_backslash() -> None:
    text, found = explain("\\cell")
    assert found
    assert "Where:  directly inside `\\table`" not in text
    assert "Where:  directly inside `\\row`" in text


def test_a_name_that_is_a_command_and_a_property() -> None:
    text, _ = explain("lang")
    assert "\\lang: Text in another language." in text
    assert "lang (character property): Language for proofing" in text


def test_a_property() -> None:
    text, found = explain("space-before")
    assert found
    assert text.startswith("space-before (paragraph property): Space above.")
    assert "in style sheets as `space-before: ...;`" in text


def test_a_standard_library_macro_and_module() -> None:
    text, found = explain("note")
    assert found
    assert text.startswith("\\note: a macro defined at std:callout:")
    assert '    title (default "Note")' in text
    assert "Load it with \\use{std:callout}" in text
    module, found = explain("std:callout")
    assert found
    assert "Macros: \\callout, \\note, \\tip, \\warning, \\caution" in module
    assert "highlighted boxes for notes, tips and warnings" in module


def test_the_macros_of_a_document(tmp_path: Path) -> None:
    (tmp_path / "lib.rdx").write_text("\\let{product}{Widget}\n", encoding="utf-8")
    document = tmp_path / "doc.rdx"
    document.write_text(
        "\\use{lib.rdx}\n\n"
        "% A shaded box.\n% The title is bold.\n"
        '\\define{box}[title="Note", id]{\\b{#title} #body}\n\n'
        "Text.\n",
        encoding="utf-8",
    )
    text, found = explain("box", document)
    assert found
    assert text.splitlines()[0] == f"\\box: a macro defined at {document}:5"
    assert "  A shaded box.\n  The title is bold." in text
    assert '    title (default "Note")\n    id (optional)\n  Body: its #body' in text
    assert '    \\define{box}[title="Note", id]{\\b{#title} #body}' in text
    constant, found = explain("product", document)
    assert found
    assert "\\product: a constant defined at" in constant
    assert "Body" not in constant


def test_an_unknown_name_suggests() -> None:
    text, found = explain("tabel")
    assert not found
    assert "did you mean `table`" in text
    assert "--in FILE.rdx" in text


def test_the_overview_lists_every_command() -> None:
    text = overview()
    assert all(f"\\{name}" in text for name in COMMANDS)


def test_the_command_line(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["explain", "figure"]) == 0
    assert capsys.readouterr().out.startswith("\\figure: An image and its caption")
    assert main(["explain", "--properties", "cell"]) == 0
    assert "  valign" in capsys.readouterr().out
    assert main(["explain", "nosuch"]) == 1
    assert "`nosuch` is not a command, property or macro" in capsys.readouterr().err
    assert main(["explain", "x", "--in", "missing.rdx"]) == 1
