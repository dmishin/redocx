"""`root`: every file a document names must be inside one folder (RDX0313)."""

import shutil
from pathlib import Path

import pytest

from redocx.cli import main
from redocx.compiler import compile_source
from redocx.syntax.diagnostics import Code

EXAMPLES = Path(__file__).resolve().parents[2] / "examples"


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    """tmp/outside/: secret.rdx, logo.png, refs.bib, s.rdxs; tmp/inside/: part.rdx, logo.png."""
    outside, inside = tmp_path / "outside", tmp_path / "inside"
    inside.mkdir()
    outside.mkdir()
    for folder in (inside, outside):
        (folder / "part.rdx").write_text("Part text.\n", encoding="utf-8")
        shutil.copy(EXAMPLES / "img" / "logo.png", folder / "logo.png")
    shutil.copy(EXAMPLES / "citations" / "refs.bib", outside / "refs.bib")
    (outside / "s.rdxs").write_text('paragraph "X" { bold; }\n', encoding="utf-8")
    return tmp_path


def _codes(source: str, base: Path, root: Path | None) -> list[Code]:
    result = compile_source(source, base, root=root)
    return [d.code for _, d in result.diagnostics]


def test_files_inside_the_root_are_read(tree: Path) -> None:
    inside = tree / "inside"
    source = "\\include{part.rdx}\n\n\\image{logo.png}\n\n\\include{./sub/../part.rdx}\n"
    assert _codes(source, inside, inside) == []


@pytest.mark.parametrize(
    "source",
    [
        "\\include{../outside/part.rdx}\n",
        "\\image{../outside/logo.png}\n",
        "\\use{../outside/s.rdxs}\n",
        "\\bibliography[file=../outside/refs.bib]\n",
        "\\document[template=../outside/t.dotx]\n",
    ],
)
def test_files_outside_the_root_are_errors(tree: Path, source: str) -> None:
    inside = tree / "inside"
    assert _codes(source, inside, inside) == [Code.OUTSIDE_ROOT]
    assert Code.OUTSIDE_ROOT not in _codes(source, inside, None)


def test_absolute_paths_outside_the_root_are_errors(tree: Path) -> None:
    secret = (tree / "outside" / "part.rdx").as_posix()
    result = compile_source(f"\\include{{{secret}}}\n", tree / "inside", root=tree / "inside")
    ((_, diagnostic),) = result.diagnostics
    assert diagnostic.code is Code.OUTSIDE_ROOT
    assert diagnostic.message == f"`{secret}` is outside the root folder"
    assert result.document is None


def test_cli_root(tree: Path, capsys: pytest.CaptureFixture[str]) -> None:
    doc = tree / "inside" / "doc.rdx"
    doc.write_text("\\include{../outside/part.rdx}\n", encoding="utf-8")
    assert main(["check", str(doc)]) == 0
    assert main(["check", str(doc), "--root", str(tree / "inside")]) == 1
    assert "error[RDX0313]" in capsys.readouterr().err
    assert main(["build", str(doc), "--root", str(tree)]) == 0
