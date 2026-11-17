from pathlib import Path

import pytest

from redocx.cli import main
from redocx.compiler import build
from redocx.syntax import Source, parse
from tests.support.xsd import validate_package

EXAMPLES = Path(__file__).resolve().parents[2] / "examples"


BUILT = sorted(
    p for p in EXAMPLES.rglob("*.rdx") if "spike" not in p.parts and p.parent.name != "chapters"
)


@pytest.mark.parametrize("path", BUILT, ids=lambda p: p.stem)
def test_examples_compile_to_schema_valid_documents(path: Path) -> None:
    data, result = build(Source.from_path(path))
    assert data is not None, result.render_diagnostics()
    assert validate_package(data) == {}


def test_syntax_tour_has_no_diagnostics() -> None:
    result = parse(Source.from_path(EXAMPLES / "syntax-tour.rdx"))
    assert result.diagnostics == (), result.render_diagnostics()
    assert result.cst.text == result.source.text


@pytest.mark.parametrize("path", sorted((EXAMPLES / "spike").rglob("*.rdx")), ids=lambda p: p.name)
def test_spike_samples_parse_losslessly(path: Path) -> None:
    # The spike samples predate v0.2, so diagnostics are expected; parsing must still be total.
    source = Source.from_path(path)
    assert parse(source).cst.text == source.text


def test_cli_check(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    assert main(["check", str(EXAMPLES / "syntax-tour.rdx")]) == 0
    # The tour uses examples/styles/house.rdxs, whose table style is a known M2 warning.
    assert "0 errors" in capsys.readouterr().err

    bad = tmp_path / "bad.rdx"
    bad.write_text("Hello \\b{world\n", encoding="utf-8")
    assert main(["check", str(bad)]) == 1
    err = capsys.readouterr().err
    assert "error[RDX0101]: unclosed `{`" in err
    assert "1 error, 0 warnings" in err


def test_cli_parse_dumps_trees(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    path = tmp_path / "a.rdx"
    path.write_text("= Hi\n", encoding="utf-8")
    assert main(["parse", str(path)]) == 0
    assert capsys.readouterr().out == '(heading 1 :numbered (text "Hi"))\n'
    assert main(["parse", str(path), "--tree", "cst"]) == 0
    assert "HEADING_MARKER@0 '='" in capsys.readouterr().out


def test_cli_missing_file(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["check", "does-not-exist.rdx"]) == 1
    assert "cannot read" in capsys.readouterr().err
