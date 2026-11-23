"""The index: \\ix marks as XE fields, \\index as an INDEX field (design §3.12)."""

from __future__ import annotations

import io
import zipfile
from typing import cast

from lxml import etree

from redocx import ir
from redocx.compiler import build, compile_source
from redocx.lower.index import IndexMark, xe_instruction
from redocx.values import TabStop
from tests.support.xsd import validate_package

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def compiled(text: str) -> ir.Document:
    result = compile_source(text)
    assert result.document is not None, result.render_diagnostics()
    assert not result.diagnostics, result.render_diagnostics()
    return result.document


def errors(text: str) -> list[str]:
    return [d.message for _, d in compile_source(text).diagnostics if d.is_error]


def marks(doc: ir.Document) -> list[IndexMark]:
    return [
        item
        for paragraph in doc.all_paragraphs()
        for item in paragraph.content
        if isinstance(item, IndexMark)
    ]


def index_block(doc: ir.Document) -> ir.FieldBlock:
    (block,) = [b for b in doc.body if isinstance(b, ir.FieldBlock)]
    return block


def entries(doc: ir.Document) -> list[tuple[str | None, str]]:
    return [(p.style, p.text()) for p in index_block(doc).content]


def test_ix_is_an_xe_field_without_text() -> None:
    doc = compiled("Traffic flow\\ix{traffic!flow} here.")
    (paragraph,) = doc.all_paragraphs()
    assert paragraph.text() == "Traffic flow here."
    (mark,) = marks(doc)
    assert mark.code == ' XE "traffic:flow" '
    assert not mark.has_result
    assert mark.terms == ("traffic", "flow")


def test_xe_options_and_escapes() -> None:
    assert xe_instruction(('a: "b"',), None, False, False) == (' XE "a\\: \\"b\\"" ',)
    doc = compiled(
        '\\ix[bold]{a}\\ix[italic]{b}\\ix[see="c"]{d}\\ix[see-also="e"]{f}\\ix[xref="Siehe g"]{h}'
    )
    assert [m.code for m in marks(doc)] == [
        ' XE "a" \\b ',
        ' XE "b" \\i ',
        ' XE "d" \\t "See c" ',
        ' XE "f" \\t "See also e" ',
        ' XE "h" \\t "Siehe g" ',
    ]


def test_xe_is_written_as_word_writes_it() -> None:
    data, result = build("A\\ix{term} b.")
    assert data is not None, result.render_diagnostics()
    document = etree.fromstring(zipfile.ZipFile(io.BytesIO(data)).read("word/document.xml"))
    kinds = [c.get(f"{W}fldCharType") for c in document.iter(f"{W}fldChar")]
    assert kinds == ["begin", "end"]  # no separate: XE has no result


def test_index_entries_are_sorted_nested_and_merged() -> None:
    doc = compiled(
        "Text\\ix{traffic!flow}\\ix{Ortúzar}\\ix{apple}\\ix{traffic}\\ix{traffic!density!jam}"
        '\\ix{traffic!flow}\\ix[see="queueing"]{waiting lines}\\ix{Zebra}\\ix{ortolan}.\n\n'
        "\\index"
    )
    assert entries(doc) == [
        ("index 1", "apple"),
        ("index 1", "ortolan"),
        ("index 1", "Ortúzar"),
        ("index 1", "traffic"),
        ("index 2", "density"),
        ("index 3", "jam"),
        ("index 2", "flow"),
        ("index 1", "waiting lines. See queueing"),
        ("index 1", "Zebra"),
    ]
    block = index_block(doc)
    assert block.field.code == ' INDEX \\c "2" '
    assert block.field.dirty
    assert doc.settings.update_fields_on_open
    styles = {s.name for s in doc.styles}
    assert {"index 1", "index 2", "index 3"} <= styles


def test_index_options() -> None:
    doc = compiled(
        'A\\ix{beta}\\ix{alpha}\\ix{Äpfel}.\n\n\\index[columns=1, headings, right-align, title="Index"]'
    )
    heading = next(p for p in doc.all_paragraphs() if p.style == "Heading 1")
    assert heading.text() == "Index"
    assert heading.unnumbered
    block = index_block(doc)
    assert block.field.code == ' INDEX \\e "\t" \\h "A" '
    assert entries(doc) == [
        ("index heading", "A"),
        ("index 1", "alpha"),
        ("index 1", "Äpfel"),
        ("index heading", "B"),
        ("index 1", "beta"),
    ]
    tabs = block.content[1].props.get("tabs")
    assert isinstance(tabs, tuple)
    (tab,) = cast("tuple[TabStop, ...]", tabs)
    assert tab.align == "right"
    assert tab.leader == "dot"


def test_marks_in_notes_and_tables_count() -> None:
    doc = compiled("A\\footnote{Note\\ix{in a note}.}\n\n| x\\ix{in a table} |\n| y |\n\n\\index")
    assert [text for _, text in entries(doc)] == ["in a note", "in a table"]


def test_empty_index_says_so() -> None:
    doc = compiled("No marks.\n\n\\index")
    assert entries(doc) == [(None, "No index entries found.")]


def test_index_errors() -> None:
    assert errors("\\ix{}") == ["`\\ix` needs a term"]
    assert errors("\\ix{a!!b}") == [
        "\\ix needs a term, with `!` before each subentry: \\ix{traffic!flow}"
    ]
    assert errors("\\ix{a!b!c!d!e!f!g!h!i!j}") == ["index entries nest at most 9 levels deep"]
    assert errors("\\ix[colour=red]{a}") == ["unknown option `colour` for `\\ix`"]
    assert errors("\\index[columns=7]") == ["`columns`: expected a whole number from 1 to 4, got 7"]
    assert errors("| \\index |\n|---|") != []


def test_builds_valid_docx() -> None:
    data, result = build(
        "= Traffic\n\nFlow\\ix{traffic!flow}\\ix[bold]{queue}.\n\n"
        '\\index[headings, right-align, title="Index"]\n'
    )
    assert data is not None, result.render_diagnostics()
    assert not result.diagnostics, result.render_diagnostics()
    assert validate_package(data) == {}
