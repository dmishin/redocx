"""`redocx build` and `check` for programs: standard input and output, warnings as errors,
JSON diagnostics."""

import io
import json
import sys
import zipfile
from pathlib import Path

import pytest

from redocx.cli import main

WARNS = "Text with an unclosed *star.\n"
FAILS = "Text \\b{never closed\n"


def _stdin(monkeypatch: pytest.MonkeyPatch, text: str) -> None:
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(text.encode("utf-8"))))


def _json_lines(err: str) -> list[dict[str, object]]:
    return [json.loads(line) for line in err.splitlines()]


def test_build_reads_standard_input_and_writes_standard_output(
    monkeypatch: pytest.MonkeyPatch, capsysbinary: pytest.CaptureFixture[bytes]
) -> None:
    _stdin(monkeypatch, "Hello from a pipe.\n")
    assert main(["build", "-", "-o", "-"]) == 0
    out, err = capsysbinary.readouterr()
    with zipfile.ZipFile(io.BytesIO(out)) as package:
        assert b"Hello from a pipe." in package.read("word/document.xml")
    assert b"wrote the document to standard output (0 errors, 0 warnings)" in err


def test_standard_input_finds_files_from_the_current_directory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "part.rdx").write_text("Included text.\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    _stdin(monkeypatch, "\\include{part.rdx}\n")
    assert main(["build", "-", "-o", "out.docx"]) == 0
    with zipfile.ZipFile(tmp_path / "out.docx") as package:
        assert b"Included text." in package.read("word/document.xml")


def test_standard_input_needs_an_output(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _stdin(monkeypatch, "x\n")
    assert main(["build", "-"]) == 1
    assert "reading from standard input needs `-o`" in capsys.readouterr().err


def test_check_reads_standard_input(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _stdin(monkeypatch, FAILS)
    assert main(["check", "-"]) == 1
    assert "--> <stdin>:1:" in capsys.readouterr().err


def test_warnings_as_errors(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = tmp_path / "w.rdx"
    path.write_text(WARNS, encoding="utf-8")
    assert main(["build", str(path)]) == 0
    assert main(["check", str(path)]) == 0
    (path.with_suffix(".docx")).unlink()
    capsys.readouterr()

    assert main(["build", str(path), "--warnings-as-errors"]) == 1
    assert "build failed: 0 errors, 1 warning (warnings are errors)" in capsys.readouterr().err
    assert not path.with_suffix(".docx").exists()
    assert main(["check", str(path), "--warnings-as-errors"]) == 1


def test_json_diagnostics(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = tmp_path / "bad.rdx"
    path.write_text(FAILS, encoding="utf-8")
    assert main(["build", str(path), "--diagnostics", "json"]) == 1
    first, summary = _json_lines(capsys.readouterr().err)
    assert first["type"] == "diagnostic"
    assert first["severity"] == "error"
    assert first["code"] == "RDX0101"
    assert first["message"] == "unclosed `{`"
    assert (first["file"], first["line"], first["column"]) == (str(path), 1, 8)
    assert summary == {"type": "summary", "errors": 1, "warnings": 0, "output": None}

    path.write_text(WARNS, encoding="utf-8")
    assert main(["build", str(path), "--diagnostics", "json"]) == 0
    warning, summary = _json_lines(capsys.readouterr().err)
    assert warning["severity"] == "warning"
    assert summary["output"] == str(path.with_suffix(".docx"))

    assert main(["check", str(tmp_path / "missing.rdx"), "--diagnostics", "json"]) == 1
    problem, _ = _json_lines(capsys.readouterr().err)
    assert problem["type"] == "error"
    assert str(problem["message"]).startswith("cannot read")
