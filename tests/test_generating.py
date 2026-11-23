"""The generation guide's example and `examples/generated/invoice.py` run and read back their data."""

import re
import runpy
from io import BytesIO
from pathlib import Path

import pytest
from docx import Document

from redocx import escape, ir
from redocx.compiler import compile_source

ROOT = Path(__file__).resolve().parents[1]
GUIDE = ROOT / "docs" / "guide" / "generating.md"
INVOICE = ROOT / "examples" / "generated" / "invoice.py"


def _text(data: bytes) -> str:
    document = Document(BytesIO(data))
    cells = [c.text for t in document.tables for row in t.rows for c in row.cells]
    return "\n".join([p.text for p in document.paragraphs] + cells)


def test_invoice_example_keeps_its_data_as_text() -> None:
    invoice = runpy.run_path(str(INVOICE))
    text = _text(invoice["render"](invoice["SAMPLE"], INVOICE.parent))
    for expected in (
        "Smith & Sons {Holdings}",
        "1. floor, #3",
        "Widgets *large* | blue",
        "Support @ 50% off",
        "- Payment due in 30 days. = Thank you!",
        "130.00",
    ):
        assert expected in text


def test_guide_outline_example_compiles_cleanly() -> None:
    blocks = re.findall(r"^```python\n(.*?)^```$", GUIDE.read_text("utf-8"), re.M | re.S)
    (code,) = [b for b in blocks if "def outline" in b]
    namespace: dict[str, object] = {"escape": escape}
    exec(code, namespace)  # the guide's own example
    outline = namespace["outline"]
    assert callable(outline)
    data = [("Scope *and* aims", ["Line one\nline two", "- not an item"]), ("Next", ["x"])]
    result = compile_source(str(outline(data)))
    assert not result.diagnostics, result.render_diagnostics()
    assert result.document is not None
    texts = [p.text() for p in result.document.body if isinstance(p, ir.Paragraph)]
    assert texts == ["Scope *and* aims", "Line one line two", "- not an item", "Next", "x"]


def test_guide_example_runs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    match = re.search(r"^```python\n(.*?)^```$", GUIDE.read_text("utf-8"), re.M | re.S)
    assert match is not None
    script = tmp_path / "example.py"
    script.write_text(match.group(1), encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    runpy.run_path(str(script))
    assert "Widgets *large* | blue" in _text((tmp_path / "invoice.docx").read_bytes())
