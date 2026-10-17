import io
import struct
from pathlib import Path

import pytest
from PIL import Image as PILImage

from tests.support.xsd import validate_package
from wordgenx import ir
from wordgenx.compiler import build, compile_source
from wordgenx.props import cell_props, para_props
from wordgenx.values import Border, Color, Length

A4_TEXT_WIDTH = Length.mm(210).emu - 2 * Length.cm("2.54").emu


def document(text: str, base_dir: Path | None = None) -> ir.Document:
    result = compile_source(text, base_dir)
    assert result.document is not None, result.render_diagnostics()
    return result.document


def tables(doc: ir.Document) -> list[ir.Table]:
    return list(ir.iter_tables(doc.body))


def cell_text(cell: ir.Cell) -> str:
    return " / ".join("".join(r.text for r in p.runs()) for p in ir.iter_paragraphs(cell.content))


# --- IR layout --------------------------------------------------------------------------------


def test_layout_resolves_spans() -> None:
    widths = (Length.cm(2),) * 3
    table = ir.Table(
        (
            ir.Row((ir.Cell(rowspan=2), ir.Cell(colspan=2))),
            ir.Row((ir.Cell(), ir.Cell())),
        ),
        widths,
    )
    layout = ir.table_layout(table)
    assert [[(s.column, s.continues) for s in row] for row in layout] == [
        [(0, False), (1, False)],
        [(0, True), (1, False), (2, False)],
    ]


@pytest.mark.parametrize(
    ("rows", "message"),
    [
        ((ir.Row((ir.Cell(colspan=3),)),), "more columns"),
        ((ir.Row((ir.Cell(),)),), "covers 1 of the table's 2 columns"),
        ((ir.Row((ir.Cell(rowspan=2), ir.Cell())),), "past the last row"),
    ],
)
def test_layout_errors(rows: tuple[ir.Row, ...], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        ir.Table(rows, (Length.cm(2), Length.cm(2)))


# --- lowering ----------------------------------------------------------------------------------


def test_pipe_table_defaults() -> None:
    (table,) = tables(document("| a | b |\n| c | d |"))
    assert table.style == "Table Grid"
    assert table.widths == (Length(A4_TEXT_WIDTH // 2),) * 2
    assert [[cell_text(c) for c in row.cells] for row in table.rows] == [["a", "b"], ["c", "d"]]
    assert "Table Grid" in {s.name for s in document("| a |").styles}


def test_table_options_widths_and_columns() -> None:
    (table,) = tables(
        document(
            "\\table[header-rows=1, widths=(4cm, 25%, 1fr), columns=(left, right, center), align=center]{\n"
            "| H | 1 | x |\n| \\cell[colspan=3, align=justify, shade=#FFF2CC]{wide} |||\n}"
        )
    )
    quarter = A4_TEXT_WIDTH * 25 // 100
    assert table.widths == (
        Length.cm(4),
        Length(quarter),
        Length(A4_TEXT_WIDTH - Length.cm(4).emu - quarter),
    )
    assert table.align == "center"
    header, wide = table.rows
    assert header.header
    assert not wide.header
    first_paragraphs = [next(ir.iter_paragraphs(c.content)) for c in header.cells]
    assert [p.props for p in first_paragraphs] == [
        para_props(align="left"), para_props(align="right"), para_props(align="center"),
    ]  # fmt: skip
    (cell,) = wide.cells
    (paragraph,) = ir.iter_paragraphs(cell.content)
    assert (cell.colspan, cell.props.get("shading"), paragraph.props) == (
        3,
        Color.parse("#FFF2CC"),
        para_props(align="justify"),
    )


def test_block_form_rows_and_cells() -> None:
    (outer, nested) = tables(
        document(
            "\\table[widths=(1fr, 2fr)]{\n"
            "  \\row[header, height=(1cm, exact), cant-split]{ \\cell{A} \\cell{B} }\n"
            "  \\row{ \\cell[rowspan=2, valign=center]{Merged} \\cell{\n    Para.\n\n    - item\n  } }\n"
            "  \\row{ \\cell{| n | m |} }\n"
            "}"
        )
    )
    first, second, third = outer.rows
    assert (first.header, first.height, first.exact_height, first.cant_split) == (
        True,
        Length.cm(1),
        True,
        True,
    )
    assert (second.cells[0].rowspan, second.cells[0].props.get("valign")) == (2, "center")
    list_paragraph = [p for p in ir.iter_paragraphs(second.cells[1].content) if p.list_item]
    assert len(list_paragraph) == 1
    assert list_paragraph[0].style == "List Paragraph"
    assert len(third.cells) == 1  # the other position is covered by the rowspan
    assert len(nested.widths) == 2


def test_short_pipe_rows_are_padded() -> None:
    (table,) = tables(document("| a | b | c |\n| d |"))
    assert [len(row.cells) for row in table.rows] == [3, 3]


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("\\table[widths=(1cm)]{| a | b |}", "the table has 2 columns but widths lists 1"),
        ("\\table[widths=(30cm, 1fr)]{| a | b |}", "leave no room"),
        ("\\table[widths=(huge)]{| a |}", "invalid column width"),
        ("\\table[bogus=1]{| a |}", "unknown option `bogus` for `\\table`"),
        ("\\table{Some text}", "a \\table contains pipe rows"),
        ("| \\cell[colspan=2]{a} | not empty |", "covered by a colspan"),
        ("\\table{\\row{\\cell[rowspan=3]{a}}}", "past the last row"),
        ("\\row{x}", "`\\row` is only valid directly inside \\table"),
        ("\\table{\\row{text}}", "a \\row contains \\cell commands"),
        ("\\table{| \\cell[shade=blue]{a} |}", "not a color"),
        ("\\table{| \\cell[borders=(top=wiggly)]{a} |}", "not a border part"),
        ("\\table[look=(header-row, zebra)]{| a |}", "`look`: unknown value `zebra`"),
        ("\\table[cell-margins=(middle=1cm)]{| a |}", "cell-margins parts"),
        ("\\table[style=Fancy]{| a |}", "table style `Fancy` is not defined"),
    ],
)
def test_table_errors(text: str, message: str) -> None:
    result = compile_source(text)
    assert result.document is None
    assert any(message in d.message for _, d in result.diagnostics), result.render_diagnostics()


def test_borderless_table() -> None:
    (table,) = tables(document("\\table[style=none]{| a |}"))
    assert table.style is None


def test_table_and_cell_formatting_options() -> None:
    (table,) = tables(
        document(
            "\\table[look=(header-row, banded-columns), borders=(outer=(double, 1.5pt, #C00000), "
            "inner=none), cell-margins=0.2cm]{\n"
            "| \\cell[shade=theme.accent1.lighter(80%), borders=(bottom=(thick, 3pt)), "
            "valign=bottom]{a} |\n}"
        )
    )
    assert table.look == frozenset({"header-row", "banded-columns"})
    assert table.props["border-top"] == Border("double", Length.pt("1.5"), Color.parse("#C00000"))
    assert table.props["border-inside-v"] == Border("nil")
    assert table.props["cell-margin-left"] == Length.cm("0.2")
    (cell,) = table.rows[0].cells
    assert cell.props == cell_props(
        shading="theme.accent1.lighter(80%)", border_bottom="(thick, 3pt)", valign="bottom"
    )


def test_table_style_repeats_its_header_row() -> None:
    doc = document(
        '\\styles{ table Data : "Table Grid" { header-row { bold; repeat; } } }\n\n'
        "\\table[style=Data]{| h |\n| a |}"
    )
    (table,) = tables(doc)
    assert [row.header for row in table.rows] == [True, False]
    assert {"Data", "Table Grid"} <= {s.name for s in doc.styles}  # the built-in base comes along


def test_styled_tables_build_valid_docx() -> None:
    data, result = build(
        '\\styles{ table Data : "Table Grid" {\n'
        "  borders: (all=(single, 0.5pt, #BFBFBF)); cell-margins: (left=0.15cm, right=0.15cm);\n"
        "  header-row { bold; shading: theme.accent1; border-bottom: (single, 1.5pt); }\n"
        "  banded-rows { odd { shading: #F2F2F2; } }\n"
        "} }\n\n"
        "\\table[style=Data, look=(header-row, banded-rows, last-row)]{| h | i |\n| a | b |}\n\n"
        "\\table[style=none, borders=(bottom=(double, 1pt))]{| \\cell[borders=none]{x} |}"
    )
    assert data is not None, result.render_diagnostics()
    assert validate_package(data) == {}


# --- images ------------------------------------------------------------------------------------


@pytest.fixture
def images(tmp_path: Path) -> Path:
    PILImage.new("RGB", (192, 96), "red").save(tmp_path / "wide.png", dpi=(96, 96))
    PILImage.new("RGB", (100, 50), "blue").save(tmp_path / "hires.jpg", dpi=(300, 300))
    PILImage.new("RGB", (4000, 100), "green").save(tmp_path / "huge.png")
    (tmp_path / "notes.txt").write_text("not an image", encoding="utf-8")
    buffer = io.BytesIO()
    PILImage.new("RGB", (10, 10)).save(buffer, "TIFF")
    (tmp_path / "scan.tif").write_bytes(buffer.getvalue())
    return tmp_path


def _images(doc: ir.Document) -> list[ir.Image]:
    return [i for p in ir.iter_paragraphs(doc.body) for i in p.content if isinstance(i, ir.Image)]


def test_image_sizes(images: Path) -> None:
    doc = document(
        "\\image{wide.png} \\image{hires.jpg} \\image{huge.png} \\image[width=4cm, alt=Logo]{wide.png} "
        "\\image[height=1in]{wide.png} \\image[width=50%]{wide.png}",
        images,
    )
    natural, hires, huge, by_width, by_height, half = _images(doc)
    assert (natural.width, natural.height) == (Length.inch(2), Length.inch(1))
    assert natural.format == "png"
    assert hires.format == "jpeg"
    assert abs(hires.width.emu - Length.inch(1).emu // 3) <= 1  # 100 px at 300 dpi
    assert huge.width.emu == A4_TEXT_WIDTH  # shrunk to the text width
    assert (by_width.width, by_width.height, by_width.description) == (
        Length.cm(4),
        Length.cm(2),
        "Logo",
    )
    assert (by_height.width, by_height.height) == (Length.inch(2), Length.inch(1))
    assert half.width.emu == A4_TEXT_WIDTH // 2


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("\\image{missing.png}", "cannot read image"),
        ("\\image{notes.txt}", "not an image"),
        ("\\image{scan.tif}", "unsupported image format TIFF"),
        ("\\image[size=1cm]{wide.png}", "unknown option `size` for `\\image`"),
    ],
)
def test_image_errors(images: Path, text: str, message: str) -> None:
    result = compile_source(text, images)
    assert result.document is None
    assert any(message in d.message for _, d in result.diagnostics), result.render_diagnostics()


def test_identical_images_are_stored_once(images: Path) -> None:
    data, result = build(
        "\\image{wide.png} \\image[width=1cm]{wide.png} \\image{hires.jpg}", images
    )
    assert data is not None, result.render_diagnostics()
    import zipfile

    names = zipfile.ZipFile(io.BytesIO(data)).namelist()
    assert sorted(n for n in names if n.startswith("word/media/")) == [
        "word/media/image1.png",
        "word/media/image2.jpeg",
    ]
    assert validate_package(data) == {}


def test_tables_and_images_build_valid_documents(images: Path) -> None:
    data, result = build(
        "| a | b |\n\n\\table{| x | \\image[width=1cm]{wide.png} |}\n\n- item\n\n  | in | list |",
        images,
    )
    assert data is not None, result.render_diagnostics()
    assert validate_package(data) == {}


def _emf() -> bytes:
    """A minimal Enhanced Metafile: the header record and the end record."""
    header = struct.pack(
        "<II4i4i4sIIIHHIIIiiii",
        1, 88, 0, 0, 99, 49, 0, 0, 2645, 1322, b" EMF", 0x10000, 108, 2, 1, 0, 0, 0, 0,
        1024, 768, 320, 240,
    )  # fmt: skip
    return header + struct.pack("<IIIII", 14, 20, 0, 16, 20)


def test_metafile_pictures(tmp_path: Path) -> None:
    """EMF previews of embedded objects come back from an import as \\image files."""
    (tmp_path / "chart.emf").write_bytes(_emf())
    data, result = build("\\image[width=2cm, height=1cm]{chart.emf}", tmp_path)
    assert data is not None, result.render_diagnostics()
    assert result.document is not None
    (picture,) = _images(result.document)
    assert (picture.format, picture.width, picture.height) == ("emf", Length.cm(2), Length.cm(1))
    assert validate_package(data) == {}
