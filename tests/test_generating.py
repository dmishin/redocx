"""The generation guide's example and `examples/generated/invoice.py` run and read back their data."""

import re
import runpy
from io import BytesIO
from pathlib import Path

import pytest
from docx import Document

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


def test_guide_example_runs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    match = re.search(r"^```python\n(.*?)^```$", GUIDE.read_text("utf-8"), re.M | re.S)
    assert match is not None
    script = tmp_path / "example.py"
    script.write_text(match.group(1), encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    runpy.run_path(str(script))
    assert "Widgets *large* | blue" in _text((tmp_path / "invoice.docx").read_bytes())
