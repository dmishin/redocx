"""The RTF reader: text and encodings, formatting, paragraphs, fields, tables, pictures,
and what it leaves out."""

from __future__ import annotations

import io
import struct

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from PIL import Image as PILImage

from redocx import ir
from redocx.ooxml.reader import ImportResult
from redocx.rtf import NotAnRtfError, is_rtf, read_rtf
from redocx.values import Border, Color, Length, LineSpacing, TabStop

BS = "\\"
"""A backslash, for `\\u` words written as BS + "u8593" (see the module docstring)."""
FONTS = r"{\fonttbl{\f0\fswiss Arial;}{\f1\fmodern Courier New;}}"
COLORS = r"{\colortbl;\red255\green0\blue0;\red255\green255\blue0;\red18\green52\blue86;}"


def result(
    body: str, header: str = FONTS + COLORS, start: str = r"{\rtf1\ansi\deff0"
) -> ImportResult:
    return read_rtf((start + header + body + "}").encode("latin-1"))


def read(body: str, **kwargs: str) -> ir.Document:
    return result(body, **kwargs).document


def paragraphs(doc: ir.Document) -> list[ir.Paragraph]:
    return [b for b in doc.body if isinstance(b, ir.Paragraph)]


def texts(doc: ir.Document) -> list[str]:
    return [p.text() for p in doc.all_paragraphs()]


def runs(doc: ir.Document) -> list[tuple[str, dict[str, object]]]:
    return [(r.text, dict(r.props.items())) for p in doc.all_paragraphs() for r in p.runs()]


def left_out(body: str) -> dict[str, int]:
    return dict(result(body).report.unsupported)


def png(width: int = 4, height: int = 2) -> bytes:
    buffer = io.BytesIO()
    PILImage.new("RGB", (width, height), "red").save(buffer, "PNG")
    return buffer.getvalue()


# --- text ---------------------------------------------------------------------------------


def test_paragraphs_and_text_after_the_last_par() -> None:
    assert texts(read(r"\pard One\par Two\par\par Three")) == ["One", "Two", "", "Three"]


def test_not_rtf() -> None:
    assert is_rtf(b"\xef\xbb\xbf  {\\rtf1 x}")
    with pytest.raises(NotAnRtfError):
        read_rtf(b"<html/>")


def test_defaults_are_rtf_s() -> None:
    doc = read(r"\deflang1031 x", start=r"{\rtf1\ansi\deff1")
    assert dict(doc.default_run.items()) == {
        "font": "Courier New",
        "size": Length.pt(12),
        "lang": "de-DE",
    }
    assert dict(doc.default_paragraph.items()) == {
        "space-before": Length(0),
        "space-after": Length(0),
        "widow-control": False,
    }


def test_code_pages_and_fonts_charsets() -> None:
    cyrillic = r"{\fonttbl{\f0\fcharset204 Arial;}}"
    assert texts(read(r"\'cf\'f0\'e8", header=cyrillic)) == ["\u041f\u0440\u0438"]
    western = r"{\fonttbl{\f0\fcharset0 Arial;}}"
    assert texts(read(r"\'cf\'f0", header=western, start=r"{\rtf1\ansi\ansicpg1251\deff0")) == [
        "\u00cf\u00f0"  # \fcharset0 is cp1252, whatever \ansicpg says
    ]
    plain = r"{\fonttbl{\f0 Arial;}}"
    assert texts(read(r"\'cf\'f0", header=plain, start=r"{\rtf1\ansi\ansicpg1251\deff0")) == [
        "\u041f\u0440"
    ]
    japanese = r"{\fonttbl{\f0\fcharset128 MS Mincho;}}"
    assert texts(read(r"\'82\'a0", header=japanese)) == ["\u3042"]  # one character, two bytes


def test_symbol_fonts_and_their_names() -> None:
    doc = read(r"{\f1\'b7}", header=r"{\fonttbl{\f0 Arial;}{\f1\fcharset2 Symbol;}}")
    assert runs(doc) == [("\uf0b7", {"font": "Symbol"})]


def test_the_real_name_of_a_font_for_one_charset() -> None:
    header = r"{\fonttbl{\f0 Arial;}{\f1\fcharset161{\*\fname Arial;}Arial Greek;}}"
    assert runs(read(r"{\f1\'e1}", header=header)) == [("\u03b1", {})]  # Arial: the default


def test_unicode_with_its_stand_in() -> None:
    assert texts(read("a" + BS + "u8593?b" + BS + "uc2" + BS + "u8594??c")) == ["a\u2191b\u2192c"]
    assert texts(read(BS + "u-3913?")) == ["\uf0b7"]  # negative: 65536 more
    assert texts(read(BS + "u55357?" + BS + "u56832?")) == ["\U0001f600"]  # a surrogate pair
    assert texts(read(BS + "uc0" + BS + "u8593 x")) == ["\u2191x"]


def test_unicode_and_its_ansi_copy() -> None:
    assert texts(read(r"{\upr{ansi copy}{\*\ud{unicode " + BS + "u8593?}}}")) == ["unicode \u2191"]


def test_special_characters() -> None:
    doc = read(r"a\tab b\line c\~d\_e\-f\emdash\endash\bullet\lquote\rquote\ldblquote\rdblquote")
    assert texts(doc) == ["a\tb\nc\u00a0d\u2011e\u00adf\u2014\u2013\u2022\u2018\u2019\u201c\u201d"]
    doc = read(r"a\page b")
    assert paragraphs(doc)[0].content[1] == ir.Break("page")


def test_escaped_braces_and_backslash() -> None:
    assert texts(read(r"\{x\}\\")) == ["{x}\\"]


# --- formatting ---------------------------------------------------------------------------


def test_character_formatting_and_groups() -> None:
    doc = read(r"\b bold {\i both} \b0 \ul under\ulnone \strike s\strike0 \scaps c\scaps0 end")
    assert runs(doc) == [
        ("bold ", {"bold": True}),
        ("both", {"bold": True, "italic": True}),
        (" ", {"bold": True}),
        ("under", {"underline": "single"}),
        ("s", {"strike": True}),
        ("c", {"small-caps": True}),
        ("end", {}),
    ]


def test_underline_kinds() -> None:
    doc = read(r"{\uldb a}{\ulwave b}{\uldashd c}{\ulw d}")
    assert [props["underline"] for _, props in runs(doc)] == ["double", "wave", "dotDash", "words"]


def test_size_font_colour_and_plain() -> None:
    doc = read(r"\fs20\f1\cf1 code\plain plain")
    assert runs(doc) == [
        ("code", {"size": Length.pt(10), "font": "Courier New", "color": Color(rgb="FF0000")}),
        ("plain", {}),
    ]


def test_highlight_is_one_of_word_s_or_shading() -> None:
    doc = read(r"{\highlight2 yellow}{\highlight3 other}{\cb1 back}")
    assert runs(doc) == [
        ("yellow", {"highlight": "yellow"}),
        ("other", {"shading": Color(rgb="123456")}),
        ("back", {"shading": Color(rgb="FF0000")}),
    ]


def test_raised_lowered_and_spacing() -> None:
    doc = read(r"{\super 2}{\sub i}{\up6 u}{\dn4 d}{\expndtw20 w}{\charscalex80 s}")
    assert [props for _, props in runs(doc)] == [
        {"vertical-align": "superscript"},
        {"vertical-align": "subscript"},
        {"position": Length.pt(3)},
        {"position": Length.pt(-2)},
        {"character-spacing": Length.pt(1)},
        {"scale": 80},
    ]


def test_language() -> None:
    assert runs(read(r"{\lang1049 x}")) == [("x", {"lang": "ru-RU"})]


def test_paragraph_formatting() -> None:
    doc = read(
        r"\pard\qc\li720\ri360\fi-360\sb120\sa240\sl360\slmult1\keepn\keep A\par"
        r"\pard\qj\fi240\sl-300 B\par"
        r"\pard\sl300\slmult0 C\par"
    )
    a, b, c = (dict(p.props.items()) for p in paragraphs(doc))
    assert a == {
        "align": "center",
        "indent-left": Length.twips(720),
        "indent-right": Length.twips(360),
        "hanging-indent": Length.twips(360),
        "space-before": Length.twips(120),
        "space-after": Length.twips(240),
        "line-spacing": LineSpacing.lines("1.5"),
        "keep-with-next": True,
        "keep-lines": True,
    }
    assert b == {
        "align": "both",
        "first-line-indent": Length.twips(240),
        "line-spacing": LineSpacing.exact(Length.twips(300)),
    }
    assert c == {"line-spacing": LineSpacing.at_least(Length.twips(300))}


def test_pard_resets_and_paragraph_formatting_lasts() -> None:
    doc = read(r"\pard\qr A\par B\par\pard C\par")
    assert [p.props.get("align") for p in paragraphs(doc)] == ["right", "right", None]


def test_tab_stops() -> None:
    doc = read(r"\pard\tx1440\tqr\tldot\tx5000\tqc\tx3000\tb4000 a\tab b\par")
    assert paragraphs(doc)[0].props.get("tabs") == (
        TabStop(Length.twips(1440), "left", "none"),
        TabStop(Length.twips(5000), "right", "dot"),
        TabStop(Length.twips(3000), "center", "none"),
        TabStop(Length.twips(4000), "bar", "none"),
    )


def test_paragraph_borders_and_shading() -> None:
    doc = read(r"\pard\brdrb\brdrdb\brdrw15\brsp20\brdrcf1\cbpat2 x\par")
    props = dict(paragraphs(doc)[0].props.items())
    assert props == {
        "border-bottom": Border("double", Length.twips(15), Color(rgb="FF0000"), Length.pt(1)),
        "shading": Color(rgb="FFFF00"),
    }


def test_lists_keep_their_numbers_as_text() -> None:
    body = r"{\listtext\pard\plain\f1 \'b7\tab}\pard\fi-360\li720 Item\par"
    doc = read(body)
    assert texts(doc) == ["\u00b7\tItem"]
    assert runs(doc)[0] == ("\u00b7\t", {"font": "Courier New"})
    assert paragraphs(doc)[0].props.get("hanging-indent") == Length.twips(360)
    assert left_out(body) == {"list (its numbers kept as text)": 1}


# --- fields and bookmarks -----------------------------------------------------------------


def test_hyperlinks() -> None:
    doc = read(
        r"See {\field{\*\fldinst{HYPERLINK " + '"https://example.com/"' + r"}}"
        r"{\fldrslt{\ul\cf1 the site}}} and "
        r"{\field{\*\fldinst HYPERLINK \\l " + '"part2"' + r"}{\fldrslt part 2}}.\par"
    )
    content = paragraphs(doc)[0].content
    first, second = (item for item in content if isinstance(item, ir.Hyperlink))
    assert first.url == "https://example.com/"
    assert [(r.text, dict(r.props.items())) for r in first.content if isinstance(r, ir.Run)] == [
        ("the site", {"underline": "single", "color": Color(rgb="FF0000")})
    ]
    assert second.anchor == "part2"
    assert texts(doc) == ["See the site and part 2."]


def test_other_fields_keep_their_result() -> None:
    assert texts(read(r"Page {\field{\*\fldinst PAGE}{\fldrslt 3}}.")) == ["Page 3."]


def test_bookmarks() -> None:
    doc = read(r"{\*\bkmkstart here}x{\*\bkmkend here}")
    assert paragraphs(doc)[0].content == (
        ir.BookmarkStart("here"),
        ir.Run("x"),
        ir.BookmarkEnd("here"),
    )


# --- what is left out ---------------------------------------------------------------------


def test_skipped_destinations() -> None:
    body = (
        r"{\stylesheet{\s0 Normal;}}{\*\generator Me;}{\*\unknown text}"
        r"{\info{\title The title}{\author Ann}{\creatim\yr2024}}Body"
    )
    res = result(body)
    assert texts(res.document) == ["Body"]
    assert (res.document.meta.title, res.document.meta.author) == ("The title", "Ann")
    assert res.report.unsupported == ()


def test_left_out_parts_are_reported() -> None:
    body = (
        r"Text{\footnote\pard Note\par}{\header\pard Head\par}{\annotation x} more"
        r"\sect After"
    )
    assert texts(read(body)) == ["Text more", "After"]
    assert left_out(body) == {
        "footnote": 1,
        "header or footer": 1,
        "comment": 1,
        "section break": 1,
    }


# --- tables -------------------------------------------------------------------------------

ROW = r"\trowd\trgaph108\trleft-108\cellx2000\cellx5000"


def the_table(doc: ir.Document) -> ir.Table:
    (table,) = list(doc.all_tables())
    return table


def cell_texts(table: ir.Table) -> list[list[str]]:
    return [
        ["".join(p.text() for p in ir.iter_paragraphs(c.content)) for c in r.cells]
        for r in table.rows
    ]


def test_a_table_between_paragraphs() -> None:
    doc = read(
        r"Before\par"
        + ROW
        + r"\pard\intbl A1\cell B1\cell\row"
        + ROW
        + r"\pard\intbl A2\cell B2\cell\row"
        r"\pard After\par"
    )
    kinds = [type(b).__name__ for b in doc.body]
    assert kinds == ["Paragraph", "Table", "Paragraph"]
    table = the_table(doc)
    assert cell_texts(table) == [["A1", "B1"], ["A2", "B2"]]
    assert [w.in_twips for w in table.widths] == [2108, 3000]
    assert table.style is None
    assert dict(table.props.items()) == {
        "cell-margin-left": Length.twips(108),
        "cell-margin-right": Length.twips(108),
    }


def test_word_writes_the_row_definition_again_before_row() -> None:
    row = r"\trowd\trgaph108\cellx2000\cellx4000"
    doc = read(row + r"\pard\intbl A\cell B\cell\pard\intbl{" + row + r"\row}\pard After\par")
    assert cell_texts(the_table(doc)) == [["A", "B"]]


def test_paragraphs_in_a_cell() -> None:
    doc = read(r"\trowd\cellx3000\pard\intbl One\par Two\cell\row")
    (cell,) = the_table(doc).rows[0].cells
    assert [p.text() for p in ir.iter_paragraphs(cell.content)] == ["One", "Two"]


def test_merged_cells() -> None:
    doc = read(
        r"\trowd\clmgf\cellx2000\clmrg\cellx4000\cellx6000\pard\intbl Across\cell\cell C\cell\row"
        r"\trowd\clvmgf\cellx2000\cellx4000\cellx6000\pard\intbl Down\cell B\cell C\cell\row"
        r"\trowd\clvmrg\cellx2000\cellx4000\cellx6000\pard\intbl\cell B\cell C\cell\row"
    )
    table = the_table(doc)
    assert [[(c.colspan, c.rowspan) for c in row.cells] for row in table.rows] == [
        [(2, 1), (1, 1)],
        [(1, 2), (1, 1), (1, 1)],
        [(1, 1), (1, 1)],
    ]
    assert cell_texts(table)[0] == ["Across", "C"]


def test_rows_of_different_widths() -> None:
    doc = read(
        r"\trowd\cellx2000\cellx4000\pard\intbl A\cell B\cell\row"
        r"\trowd\cellx3000\pard\intbl Wide\cell\row"
    )
    table = the_table(doc)
    assert [w.in_twips for w in table.widths] == [2000, 1000, 1000]
    assert [[c.colspan for c in row.cells] for row in table.rows] == [[1, 2], [3]]


def test_cell_borders_shading_and_alignment() -> None:
    doc = read(
        r"\trowd\trhdr\trrh-400\trqc\trbrdrt\brdrs\brdrw10"
        r"\clvertalc\clbrdrt\brdrs\brdrw20\brdrcf1\clbrdrb\brdrnone\clcbpat2\cellx2000"
        r"\pard\intbl x\cell\row"
    )
    table = the_table(doc)
    assert table.align == "center"
    assert table.props.get("border-top") == Border("single", Length.twips(10))
    (row,) = table.rows
    assert (row.header, row.height, row.exact_height) == (True, Length.twips(400), True)
    assert dict(row.cells[0].props.items()) == {
        "border-top": Border("single", Length.twips(20), Color(rgb="FF0000")),
        "border-bottom": Border("nil"),
        "shading": Color(rgb="FFFF00"),
        "valign": "center",
    }


def test_nested_tables_keep_their_text() -> None:
    body = (
        r"\trowd\cellx5000\pard\intbl Outer"
        r"\par\pard\intbl\itap2 In1\nestcell In2\nestcell"
        r"{\*\nesttableprops\trowd\cellx2000\cellx4000\nestrow}"
        r"\pard\intbl\itap1 \cell\row"
    )
    (cell,) = the_table(read(body)).rows[0].cells
    assert [p.text() for p in ir.iter_paragraphs(cell.content)] == ["Outer", "In1", "In2", ""]
    assert "nested table (its text kept)" in left_out(body)


# --- pictures -----------------------------------------------------------------------------


def pictures(doc: ir.Document) -> list[ir.Image]:
    return [i for p in doc.all_paragraphs() for i in p.content if isinstance(i, ir.Image)]


def test_a_png_in_hex() -> None:
    data = png()
    hexed = data.hex()
    body = (
        r"{\pict{\*\picprop{\sp{\sn x}{\sv 1}}}\pngblip\picw4\pich2\picwgoal1440\pichgoal720 "
        + "\n"
        + hexed[:50]
        + "\n"
        + hexed[50:]
        + "}"
    )
    (image,) = pictures(read(body))
    assert image.data == data
    assert (image.format, image.width, image.height) == ("png", Length.inch(1), Length.inch("0.5"))


def test_picture_size_from_pixels_and_scale() -> None:
    body = r"{\pict\pngblip\picscalex50\picscaley200 " + png(96, 48).hex() + "}"
    (image,) = pictures(read(body))
    assert (image.width, image.height) == (Length.inch("0.5"), Length.inch(1))


def test_a_picture_in_binary() -> None:
    data = png()
    rtf = b"{\\rtf1\\ansi{\\pict\\pngblip\\bin" + str(len(data)).encode() + b" " + data + b"}}"
    (image,) = pictures(read_rtf(rtf).document)
    assert image.data == data


def test_the_newer_picture_is_taken() -> None:
    body = (
        r"{\*\shppict{\pict\pngblip " + png().hex() + r"}}"
        r"{\nonshppict{\pict\wmetafile8 0100}}"
    )
    assert [i.format for i in pictures(read(body))] == ["png"]


def test_a_shape_s_picture() -> None:
    body = (
        r"{\shp{\*\shpinst\shpleft0\shpright1440{\sp{\sn shapeType}{\sv 75}}"
        r"{\sp{\sn pib}{\sv {\pict\pngblip\picwgoal1440\pichgoal720 " + png().hex() + r"}}}}"
        r"{\shprslt\par\pard fallback\par}}after"
    )
    doc = read(body)
    assert [i.format for i in pictures(doc)] == ["png"]
    assert texts(doc) == ["after"]


def test_a_metafile_gets_its_placeable_header() -> None:
    metafile = struct.pack("<HHHIHIH", 1, 9, 0x300, 12, 0, 3, 0) + b"\x03\x00\x00\x00\x00\x00"
    body = r"{\pict\wmetafile8\picw2540\pich1270\picwgoal1440\pichgoal720 " + metafile.hex() + "}"
    (image,) = pictures(read(body))
    assert image.format == "wmf"
    assert image.data[:4] == b"\xd7\xcd\xc6\x9a"
    assert image.data[22:] == metafile
    checksum = 0
    for (word,) in struct.iter_unpack("<H", image.data[:20]):
        checksum ^= word
    assert struct.unpack_from("<H", image.data, 20)[0] == checksum


def test_a_device_independent_bitmap_becomes_a_bmp() -> None:
    buffer = io.BytesIO()
    PILImage.new("RGB", (3, 2), "blue").save(buffer, "BMP")
    dib = buffer.getvalue()[14:]
    (image,) = pictures(read(r"{\pict\dibitmap0\picw3\pich2 " + dib.hex() + "}"))
    assert image.format == "bmp"
    with PILImage.open(io.BytesIO(image.data)) as opened:
        assert opened.size == (3, 2)
        assert opened.getpixel((0, 0)) == (0, 0, 255)


def test_pictures_that_cannot_be_read_are_reported() -> None:
    body = r"{\pict\macpict 0011}{\pict\pngblip 00112233}"
    assert pictures(read(body)) == []
    assert left_out(body) == {"picture (a format not read)": 1, "picture (png)": 1}


# --- robustness ---------------------------------------------------------------------------


def test_unbalanced_braces() -> None:
    assert texts(read_rtf(rb"{\rtf1 a}}} b{").document) == ["a b"]
    assert texts(read_rtf(rb"{\rtf1{\b a").document) == ["a"]


@settings(max_examples=300, deadline=None)
@given(st.binary(max_size=200))
def test_any_bytes_after_the_header_read(data: bytes) -> None:
    read_rtf(b"{\\rtf1 " + data)


ALPHABET = st.sampled_from(
    [
        "{", "}", "\\par ", "\\b ", "\\b0 ", "\\trowd", "\\cellx2000", "\\intbl ", "\\cell ",
        "\\row ", "\\pard ", "{\\*\\x ", "{\\field{\\*\\fldinst HYPERLINK x}{\\fldrslt ",
        "\\clvmrg", "\\clvmgf", "\\clmgf", "\\clmrg", "\\itap2 ", "\\nestcell ", "{\\pict ",
        "\\pngblip ", "89ab", "text ", "\\'e9", "\\" + "u8593?", "\\sect ", "{\\listtext ",
    ]
)  # fmt: skip


@settings(max_examples=400, deadline=None)
@given(st.lists(ALPHABET, max_size=40))
def test_any_mix_of_rtf_pieces_reads(pieces: list[str]) -> None:
    read_rtf(("{\\rtf1 " + "".join(pieces)).encode("latin-1"))
