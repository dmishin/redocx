"""The LaTeX hint table: in sync with the TeX guide and naming only real commands."""

import re
from pathlib import Path

import pytest

from redocx.commands.builtin import COMMANDS
from redocx.commands.latex import GUIDE, LATEX, LATEX_ENVIRONMENTS, LatexHint, latex_hint
from redocx.syntax import ast, parse

ROOT = Path(__file__).resolve().parents[2]
GUIDE_TEXT = (ROOT / GUIDE).read_text(encoding="utf-8")
HEADINGS = set(re.findall(r"^## (.+)$", GUIDE_TEXT, re.MULTILINE))
COLLISIONS = {"section", "index"}
"""LaTeX names that are redocx commands with another meaning."""
SYNTAX = {"define", "let", "begin", "end"}
"""Backslash words hints may name that are not in the command table: macro definitions,
and `\\begin{aligned}` inside math or quoted from LaTeX."""


def all_hints() -> list[tuple[str, LatexHint]]:
    return [*LATEX.items(), *(("{" + k + "}", h) for k, h in LATEX_ENVIRONMENTS.items())]


@pytest.mark.parametrize(("name", "hint"), all_hints())
def test_topic_is_a_guide_section(name: str, hint: LatexHint) -> None:
    assert hint.topic in HEADINGS, name


@pytest.mark.parametrize(("name", "hint"), all_hints())
def test_hint_names_only_real_commands(name: str, hint: LatexHint) -> None:
    for command in re.findall(r"\\([a-z]+)", hint.use):
        assert command in COMMANDS or command in SYNTAX, (name, command)


@pytest.mark.parametrize("name", list(LATEX))
def test_command_is_in_the_guide(name: str) -> None:
    assert re.search(rf"\\{name}(?![A-Za-z])", GUIDE_TEXT), f"`\\{name}` is not in {GUIDE}"


@pytest.mark.parametrize("name", list(LATEX_ENVIRONMENTS))
def test_environment_is_in_the_guide(name: str) -> None:
    assert re.search(rf"(?<![A-Za-z]){re.escape(name)}(?![A-Za-z*])", GUIDE_TEXT), name


def test_only_collisions_are_redocx_commands() -> None:
    assert {name for name in LATEX if name in COMMANDS} == COLLISIONS
    # The hint shows when the LaTeX-style body is rejected, so the command must take none.
    assert all(COMMANDS[name].body is None for name in COLLISIONS)


def command(text: str) -> ast.Command:
    (paragraph,) = parse(text).ast.blocks
    assert isinstance(paragraph, ast.Paragraph)
    (node,) = paragraph.content
    assert isinstance(node, ast.Command)
    return node


@pytest.mark.parametrize(
    ("text", "use"),
    [
        (r"\textbf{x}", "`*x*`"),
        (r"\begin{itemize}", "`- `"),
        (r"\begin{equation*}", "$$"),
        (r"\begin{nosuch}", "no environments"),
        (r"\begin", "no environments"),
        (r"\end{itemize}", "see the error on `\\begin{itemize}`"),
        (r"\end", "remove it"),
    ],
)
def test_latex_hint(text: str, use: str) -> None:
    hint = latex_hint(command(text))
    assert hint is not None
    assert use in hint.use


def test_end_takes_the_topic_of_its_environment() -> None:
    hint = latex_hint(command(r"\end{itemize}"))
    assert hint is not None
    assert hint.topic == "Lists"


def test_other_names_have_no_hint() -> None:
    assert latex_hint(command(r"\textt{x}")) is None
