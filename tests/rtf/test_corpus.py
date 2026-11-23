"""The RTF reader against Word: each RTF file in tests/corpus/rtf, read by redocx, against
Word's own reading of it saved as .docx (tools/corpus/make-rtf-corpus.ps1).

Compared: every paragraph's text, its effective paragraph formatting and the effective
formatting of its text (defaults, styles and direct formatting resolved on both sides),
the tables' grids and spans, and the pictures. Word keeps an RTF's styles, theme
references and real lists; the reader flattens them, so values are compared, not where
they come from. Not compared: what Word adds on its own (its default kerning and
language), widow control, and outline levels.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from redocx import ir
from redocx.importer import import_docx
from redocx.importer.check import in_twips
from redocx.ooxml.reader import read_docx
from redocx.ooxml.style_names import stored_name
from redocx.props import PropertySet, Target
from redocx.rtf import read_rtf
from redocx.values import Color

CORPUS = Path(__file__).parent.parent / "corpus" / "rtf"
NAMES = sorted(p.stem for p in CORPUS.glob("*.rtf"))
IGNORED_PARAGRAPH = {"outline-level", "widow-control", "contextual-spacing"}
IGNORED_RUN = {"kerning", "lang"}
LIST_INDENTS = {"indent-left", "hanging-indent", "first-line-indent", "tabs"}
"""A list's indents come from its numbering definition in Word's reading."""
THEME = "theme"

Look = dict[str, object]
Piece = tuple[str, Look]


def _paragraphs(
    blocks: tuple[ir.Block, ...], where: tuple[ir.Table, int, int] | None = None
) -> Iterator[tuple[ir.Paragraph, tuple[ir.Table, int, int] | None]]:
    for block in blocks:
        if isinstance(block, ir.Paragraph):
            yield block, where
        elif isinstance(block, ir.Table):
            for r, row in enumerate(block.rows):
                for c, cell in enumerate(row.cells):
                    yield from _paragraphs(cell.content, (block, r, c))


def _neutral(value: object) -> object:
    """None for a value that is the default anyway; THEME for a theme reference (Word's
    reading has `theme.minor` where the RTF names the font)."""
    if value in (0, "left", False, ("auto", 240)):  # line spacing as in_twips gives it
        return None
    if isinstance(value, str) and value.startswith("theme."):
        return THEME
    if isinstance(value, Color) and (value.theme or value.rgb is None):
        return THEME
    return value


def _normal(props: dict[str, object], ignored: set[str]) -> Look:
    out: Look = {}
    for key, value in props.items():
        if key not in ignored and (value := _neutral(value)) is not None:
            out[key] = value
    return out


def _differences(a: Look, b: Look) -> dict[str, tuple[object, object]]:
    return {k: (a.get(k), b.get(k)) for k in {*a, *b} if not _same(k, a.get(k), b.get(k))}


def _same(key: str, a: object, b: object) -> bool:
    if a == b or THEME in (a, b):
        return True
    # A font for one charset (`Arial CYR`) is that font: Word keeps the name when Windows
    # knows it as a substitute, and the reader takes the font's own name (`\fname`).
    return key == "font" and isinstance(a, str) and isinstance(b, str) and a.startswith(f"{b} ")


def _effective(doc: ir.Document) -> list[tuple[str, Look, list[Piece], bool]]:
    """Each paragraph's text, effective formatting and pieces of differently formatted
    text, and whether it is a list item."""
    styles = {stored_name(s.name).lower(): s for s in doc.styles}

    def chain(name: str | None) -> list[ir.Style]:
        found: list[ir.Style] = []
        while name is not None and (style := styles.get(stored_name(name).lower())):
            if style in found:
                break
            found.append(style)
            name = style.based_on
        return list(reversed(found))

    out: list[tuple[str, Look, list[Piece], bool]] = []
    legacy = PropertySet(Target.RUN, {"font": "Times New Roman", "size": "10pt"})
    for paragraph, where in _paragraphs(doc.body):
        ppr, rpr = doc.default_paragraph, legacy.merged(doc.default_run)
        if where is not None:
            table, r, c = where
            for style in chain(table.style):
                ppr, rpr = ppr.merged(style.paragraph), rpr.merged(style.run)
                for part in style.conditions:
                    if (part.kind == "firstRow" and r == 0 and "header-row" in table.look) or (
                        part.kind == "firstCol" and c == 0 and "first-column" in table.look
                    ):
                        ppr, rpr = ppr.merged(part.paragraph), rpr.merged(part.run)
        numbered = paragraph.list_item is not None
        for style in chain(paragraph.style or "Normal"):
            ppr, rpr = ppr.merged(style.paragraph), rpr.merged(style.run)
            numbered = numbered or style.numbering is not None
        pieces: list[Piece] = []
        for run in paragraph.runs():
            look = rpr
            for style in chain(run.style):
                look = look.merged(style.run)
            normal = _normal(in_twips(look.merged(run.props)), IGNORED_RUN)
            if not run.text.strip():
                continue
            if pieces and pieces[-1][1] == normal:
                pieces[-1] = (pieces[-1][0] + run.text, normal)
            else:
                pieces.append((run.text, normal))
        props = _normal(in_twips(ppr.merged(paragraph.props)), IGNORED_PARAGRAPH)
        out.append((paragraph.text(), props, pieces, numbered))
    return out


def _without_label(text: str, pieces: list[Piece]) -> tuple[str, list[Piece]]:
    """A list item's text and pieces without the number or bullet the reader keeps as text
    (Word's reading has a real list)."""
    label, _, rest = text.partition("\t")
    left = len("".join(label.split()))  # pieces leave whitespace out of the count
    out: list[Piece] = []
    for piece_text, look in pieces:
        kept = piece_text
        while left and kept:
            if not kept[0].isspace():
                left -= 1
            kept = kept[1:]
        kept = kept.lstrip("\t") if not out and len(kept) < len(piece_text) else kept
        if kept.strip():
            out.append((kept, look))
    return rest, out


@pytest.fixture(scope="module", params=NAMES)
def name(request: pytest.FixtureRequest) -> str:
    return str(request.param)


def test_the_corpus_is_there() -> None:
    assert {"richedit-formatting", "word-text", "word-tables", "word-drawings"} <= set(NAMES)


def test_paragraphs_look_as_in_word(name: str) -> None:
    words = _effective(read_docx(CORPUS / f"{name}.docx").document)
    ours = _effective(read_rtf(CORPUS / f"{name}.rtf").document)
    assert len(ours) == len(words)
    for i, ((w_text, w_props, w_pieces, listed), (o_text, o_props, o_pieces, _)) in enumerate(
        zip(words, ours, strict=True)
    ):
        if listed and "\t" in o_text:
            o_text, o_pieces = _without_label(o_text, o_pieces)
            w_props = {k: v for k, v in w_props.items() if k not in LIST_INDENTS}
            o_props = {k: v for k, v in o_props.items() if k not in LIST_INDENTS}
        assert o_text == w_text, f"paragraph {i}"
        assert _differences(w_props, o_props) == {}, f"paragraph {i}: {w_text[:30]!r}"
        joined_w = [("".join(t.split()), look) for t, look in w_pieces]
        joined_o = [("".join(t.split()), look) for t, look in o_pieces]
        assert "".join(t for t, _ in joined_w) == "".join(t for t, _ in joined_o)
        for (text, w_look), (_, o_look) in zip(
            _by_character(joined_w), _by_character(joined_o), strict=True
        ):
            assert _differences(w_look, o_look) == {}, f"paragraph {i}: {text!r}"


def _by_character(pieces: list[tuple[str, Look]]) -> Iterator[tuple[str, Look]]:
    for text, look in pieces:
        for char in text:
            yield char, look


def test_tables_and_pictures_are_as_in_word(name: str) -> None:
    word = read_docx(CORPUS / f"{name}.docx").document
    ours = read_rtf(CORPUS / f"{name}.rtf").document

    def tables(doc: ir.Document) -> list[object]:
        return [
            (
                [w.in_twips for w in t.widths],
                t.align,
                [[(c.colspan, c.rowspan) for c in row.cells] for row in t.rows],
                [row.header for row in t.rows],
            )
            for t in doc.all_tables()
        ]

    def pictures(doc: ir.Document) -> list[tuple[int, int]]:
        """Sizes only: Word may store a metafile it read as a PNG."""
        return [
            (item.width.in_twips, item.height.in_twips)
            for p in doc.all_paragraphs()
            for item in p.content
            if isinstance(item, ir.Image)
        ]

    assert tables(ours) == tables(word)
    assert len(pictures(ours)) == len(pictures(word))
    for word_size, our_size in zip(pictures(word), pictures(ours), strict=True):
        for w, o in zip(word_size, our_size, strict=True):  # Word rounds cropped sizes its way
            assert abs(w - o) <= max(1, w / 200), (word_size, our_size)


def test_the_import_compiles_to_what_was_read(name: str, tmp_path: Path) -> None:
    imported = import_docx(CORPUS / f"{name}.rtf", tmp_path)
    assert imported.errors == 0
    assert imported.comparison is not None
    assert imported.comparison.same, imported.comparison.render()
    assert not imported.printed.lost
