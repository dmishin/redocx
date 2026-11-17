"""The formatter: canonical whitespace, the same document, idempotent (``redocx fmt``)."""

import json
from pathlib import Path

import pytest
from hypothesis import given, settings

from redocx.cli import main
from redocx.syntax.format import format_source
from tests.syntax.test_lossless import markup

ROOT = Path(__file__).resolve().parents[2]


def fmt(text: str) -> str:
    result = format_source(text)
    assert result.text is not None, [d.message for d in result.diagnostics]
    return result.text


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("a  \nb\t\n", "a\nb\n"),
        ("\n\n= Title\n\n\n\nText.", "= Title\n\nText.\n"),
        ("=   Title\n", "= Title\n"),
        ("Text.\n\n\n", "Text.\n"),
        ("a\r\nb  \r\n\r\n\r\n\r\nc\r\n", "a\r\nb\r\n\r\nc\r\n"),
        ("% note   \ntext\n", "% note\ntext\n"),
        ("\\p[ a = 1 ,b=2 ]{x}\n", "\\p[a=1, b=2]{x}\n"),
        ("\\p[a=( x , y ),]{x}\n", "\\p[a=(x, y),]{x}\n"),
        ('\\image[ alt = "a  b" ]{x.png}\n', '\\image[alt="a  b"]{x.png}\n'),
        (
            '```python {#lst:a,caption="x" }\nprint(1)\n```\n',
            '```python {#lst:a, caption="x"}\nprint(1)\n```\n',
        ),
        ("", ""),
    ],
)
def test_layout(text: str, expected: str) -> None:
    assert fmt(text) == expected


def test_verbatim_is_kept() -> None:
    code = "```\nline   \n\n\n\nlast  \n```\n"
    assert fmt(code) == code
    math = "$$\n a  \n\n b \n$$\n"
    assert fmt(math) == math
    assert fmt("Use `a  =  b`  \n") == "Use `a  =  b`\n"
    assert fmt("\\raw[ooxml]{<w:p>  </w:p>}\n") == "\\raw[ooxml]{<w:p>  </w:p>}\n"


def test_multiline_option_lists_are_kept() -> None:
    text = "\\p[\n  a=1,  % first\n  b = 2,\n]{x}\n"
    assert fmt(text) == text


def test_markup_values_are_formatted_as_markup() -> None:
    assert fmt("\\p[title={A  \n\n\n\nB}]{x}\n") == "\\p[title={A\n\nB}]{x}\n"


def test_errors_are_not_formatted() -> None:
    result = format_source("\\x[a b]{y}")
    assert result.text is None
    assert result.diagnostics


def test_examples_are_formatted() -> None:
    paths = [*ROOT.glob("examples/**/*.rdx"), *ROOT.glob("docs/book/**/*.rdx")]
    for path in paths:
        if "spike" in path.parts:
            continue  # samples written before the language was settled
        text = path.read_text(encoding="utf-8")
        assert fmt(text) == text, path


@settings(max_examples=600, deadline=None)
@given(markup)
def test_formatting_keeps_the_document_and_is_idempotent(text: str) -> None:
    result = format_source(text)  # raises FormatError if the meaning would change
    if result.text is not None:
        assert fmt(result.text) == result.text


def test_cli_fmt(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    messy = tmp_path / "messy.rdx"
    messy.write_bytes(b"\xef\xbb\xbf=   Title  \r\n\r\n\r\nText.\r\n")
    tidy = tmp_path / "tidy.rdx"
    tidy.write_text("= Tidy\n", encoding="utf-8")
    assert main(["fmt", "--check", str(messy), str(tidy)]) == 1
    assert "1 file would be reformatted, 1 unchanged" in capsys.readouterr().err
    assert main(["fmt", "--diff", str(messy)]) == 1
    assert "+= Title" in capsys.readouterr().out
    assert main(["fmt", str(messy), str(tidy)]) == 0
    assert messy.read_bytes() == b"\xef\xbb\xbf= Title\r\n\r\nText.\r\n"  # BOM and CRLF kept
    assert main(["fmt", "--check", str(messy)]) == 0


def test_cli_fmt_leaves_broken_files(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    broken = tmp_path / "broken.rdx"
    broken.write_text("Text  \n\\b{open\n", encoding="utf-8")
    assert main(["fmt", str(broken)]) == 1
    assert "not formatted: 1 syntax error" in capsys.readouterr().err
    assert broken.read_text(encoding="utf-8") == "Text  \n\\b{open\n"
    sheet = tmp_path / "house.rdxs"
    sheet.write_text("", encoding="utf-8")
    assert main(["fmt", str(sheet)]) == 0
    assert "style sheets are not formatted yet" in capsys.readouterr().err


def test_cli_emit_ir(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    source = tmp_path / "doc.rdx"
    source.write_text("= Hi\n\nText.\n", encoding="utf-8")
    out = tmp_path / "doc.json"
    assert main(["build", str(source), "--emit-ir", str(out)]) == 0
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["type"] == "Document"
    assert [b["type"] for b in data["body"]] == ["Paragraph", "Paragraph"]
    assert (tmp_path / "doc.docx").exists()
    capsys.readouterr()
    assert main(["build", str(source), "--emit-ir", "-"]) == 0
    assert json.loads(capsys.readouterr().out) == data
