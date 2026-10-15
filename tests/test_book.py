"""The design documents rebuilt in wordgenx (docs/book): the M2 exit criterion.

The chapters are generated from docs/*.md by tools/md2wgx.py, and the templates from
their sources; these tests keep the committed files in step and the book building.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

from tests.support.xsd import validate_package
from wordgenx.compiler import build, compile_file
from wordgenx.syntax import Source

ROOT = Path(__file__).resolve().parents[1]
BOOK = ROOT / "docs" / "book"
REGENERATE = "python tools/md2wgx.py docs/0*.md --out docs/book/chapters"


def md2wgx() -> ModuleType:
    spec = importlib.util.spec_from_file_location("md2wgx", ROOT / "tools" / "md2wgx.py")
    assert spec is not None and spec.loader is not None  # noqa: PT018
    module = importlib.util.module_from_spec(spec)
    sys.modules["md2wgx"] = module
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("markdown", sorted((ROOT / "docs").glob("0*.md")), ids=lambda p: p.stem)
def test_chapters_are_up_to_date(markdown: Path) -> None:
    chapter = BOOK / "chapters" / markdown.with_suffix(".wgx").name
    header = f"% Generated from docs/{markdown.name} by tools/md2wgx.py; do not edit.\n\n"
    expected = header + md2wgx().convert(markdown.read_text(encoding="utf-8"))
    assert chapter.read_text(encoding="utf-8") == expected, f"run: {REGENERATE}"


@pytest.mark.parametrize(
    ("source", "template"),
    [
        (BOOK / "template.wgx", BOOK / "wordgenx.dotx"),
        (
            ROOT / "examples" / "templates" / "corporate.wgx",
            ROOT / "examples" / "templates" / "corporate.dotx",
        ),
    ],
    ids=["book", "corporate"],
)
def test_templates_are_up_to_date(source: Path, template: Path) -> None:
    data, result = build(Source.from_path(source), source.parent, as_template=True)
    assert data is not None, result.render_diagnostics()
    assert data == template.read_bytes(), f"run: wordgenx build {source} -o {template}"


def test_the_book_builds_cleanly() -> None:
    result = compile_file(BOOK / "design.wgx")
    assert result.document is not None, result.render_diagnostics()
    assert not result.diagnostics, result.render_diagnostics()
    data, _ = build(Source.from_path(BOOK / "design.wgx"), BOOK)
    assert data is not None
    assert validate_package(data) == {}


@pytest.mark.parametrize(
    ("markdown", "wgx"),
    [
        ("# 3. Language Design", "= Language Design\n"),
        (
            "Plain **bold** and *it* with `code` and 5 * 3.",
            "Plain *bold* and _it_ with `code` and 5 \\* 3.\n",
        ),
        ("Fences: ` ``` ` and ``` ``a ` b`` ```", "Fences: ```` ``` ```` and ``` ``a ` b`` ```\n"),
        (
            "[docs](https://example.org) and [roadmap](06-roadmap.md)",
            "[docs](https://example.org) and roadmap\n",
        ),
        ("- a\n  - b\n- c", "- a\n   - b\n- c\n"),
        ("> **Note:** quoted", "\\p[style=Note]{*Note:* quoted}\n"),
        ("100% {x} @y #z ~w", "100\\% \\{x\\} \\@y \\#z \\~w\n"),
    ],
)
def test_conversion(markdown: str, wgx: str) -> None:
    assert md2wgx().convert(markdown) == wgx


def test_tables_and_code() -> None:
    converted = md2wgx().convert(
        "| Name | Value |\n|------|------:|\n| `a\\|b` | 1 |\n\n```python {#x}\nprint(1)\n```"
    )
    assert '\\table[style="Book Table", header-rows=1, widths=(' in converted
    assert "columns=(left, right)" in converted
    assert "| `a|b` | 1 |" in converted
    assert "```python\nprint(1)\n```" in converted
