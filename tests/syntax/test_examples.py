from pathlib import Path

import pytest

from wordgenx.cli import main
from wordgenx.syntax import Source, parse

EXAMPLES = Path(__file__).resolve().parents[2] / "examples"


def test_syntax_tour_has_no_diagnostics() -> None:
    result = parse(Source.from_path(EXAMPLES / "syntax-tour.wgx"))
    assert result.diagnostics == (), result.render_diagnostics()
    assert result.cst.text == result.source.text


@pytest.mark.parametrize("path", sorted((EXAMPLES / "spike").rglob("*.wgx")), ids=lambda p: p.name)
def test_spike_samples_parse_losslessly(path: Path) -> None:
    # The spike samples predate v0.2, so diagnostics are expected; parsing must still be total.
    source = Source.from_path(path)
    assert parse(source).cst.text == source.text


def test_cli_check(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    assert main(["check", str(EXAMPLES / "syntax-tour.wgx")]) == 0
    # The tour uses examples/styles/house.wgxs, whose table style is a known M2 warning.
    assert "0 errors" in capsys.readouterr().err

    bad = tmp_path / "bad.wgx"
    bad.write_text("Hello \\b{world\n", encoding="utf-8")
    assert main(["check", str(bad)]) == 1
    err = capsys.readouterr().err
    assert "error[WGX0101]: unclosed `{`" in err
    assert "1 error, 0 warnings" in err


def test_cli_parse_dumps_trees(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    path = tmp_path / "a.wgx"
    path.write_text("= Hi\n", encoding="utf-8")
    assert main(["parse", str(path)]) == 0
    assert capsys.readouterr().out == '(heading 1 :numbered (text "Hi"))\n'
    assert main(["parse", str(path), "--tree", "cst"]) == 0
    assert "HEADING_MARKER@0 '='" in capsys.readouterr().out


def test_cli_missing_file(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["check", "does-not-exist.wgx"]) == 1
    assert "cannot read" in capsys.readouterr().err
