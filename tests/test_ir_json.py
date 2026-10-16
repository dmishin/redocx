"""The IR as JSON (wordgenx build --emit-ir)."""

import json

from wordgenx import ir
from wordgenx.compiler import compile_source
from wordgenx.ir_json import encode, length, to_json
from wordgenx.values import Color, Length


def test_values_read_as_in_the_language() -> None:
    assert length(Length.pt(12)) == "12pt"
    assert length(Length.pt("10.5")) == "10.5pt"
    assert length(Length.cm("9.552")) == "9.552cm"
    assert length(Length(1)) == "1emu"
    assert encode(Color.parse("#1f4e79")) == "#1F4E79"
    assert encode(b"abc") == {
        "bytes": 3,
        "sha256": "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
    }


def test_only_fields_that_differ_from_defaults() -> None:
    assert encode(ir.Run("Hi")) == {"type": "Run", "text": "Hi"}
    assert encode(ir.Paragraph((ir.Run("x"),), "Heading 1")) == {
        "type": "Paragraph",
        "content": [{"type": "Run", "text": "x"}],
        "style": "Heading 1",
    }


def test_a_document_is_json() -> None:
    result = compile_source("= Title\n\nSome \\b{bold} text.\n")
    assert result.document is not None
    data = json.loads(to_json(result.document))
    heading, paragraph = data["body"]
    assert heading["style"] == "Heading 1"
    assert [run.get("props", {}) for run in paragraph["content"]] == [{}, {"bold": True}, {}]
