"""Labels, cross-references, captions, \\seq counters, tables of contents (design §3.9-3.13)."""

from pathlib import Path

import pytest

from tests.support.xsd import validate_package
from wordgenx import ir
from wordgenx.compiler import build, compile_source
from wordgenx.values import Length, TabStop

HEADINGS = (
    '\\styles{ paragraph "Heading 1" { outline-level: 1; } paragraph "Heading 2" { outline-level: 2; }\n'
    '  numbering Headings { level 1 { style: "Heading 1"; text: "%1"; }\n'
    '  level 2 { style: "Heading 2"; text: "%1.%2"; } } }\n\n'
)
EXAMPLES = Path(__file__).parents[2] / "examples"


def compiled(text: str, base_dir: Path | None = None) -> ir.Document:
    result = compile_source(text, base_dir)
    assert result.document is not None, result.render_diagnostics()
    assert not result.diagnostics, result.render_diagnostics()
    return result.document


def errors(text: str) -> list[str]:
    result = compile_source(text)
    return [f"{d.code.value} {d.message}" for _, d in result.diagnostics if d.is_error]


def fields(doc: ir.Document) -> list[tuple[str, str]]:
    """(instruction, cached result) of every field in body paragraphs, in order."""
    out: list[tuple[str, str]] = []
    for paragraph in doc.all_paragraphs():
        for item in paragraph.content:
            parts = item.content if isinstance(item, ir.Hyperlink) else (item,)
            out += [(p.code.strip(), p.result) for p in parts if isinstance(p, ir.Field)]
    return out


def refs(doc: ir.Document) -> list[tuple[str, str]]:
    return [(code, result) for code, result in fields(doc) if code.startswith(("REF", "PAGEREF"))]


def bookmarks(paragraph: ir.Paragraph) -> list[str]:
    return [i.name for i in paragraph.content if isinstance(i, ir.BookmarkStart)]


# --- headings, paragraphs, list items ------------------------------------------------------------


def test_reference_to_numbered_heading_prints_its_number() -> None:
    doc = compiled(
        HEADINGS
        + "= One\n\n== Two {#sec:two}\n\n= Three\n\nSee @sec:two and \\ref[form=text]{sec:two}."
    )
    two = list(doc.all_paragraphs())[1]
    assert bookmarks(two) == ["_Ref_sec_two"]
    assert refs(doc) == [("REF _Ref_sec_two \\w \\h", "1.1"), ("REF _Ref_sec_two \\h", "Two")]
    assert not doc.settings.update_fields_on_open


def test_unnumbered_heading_and_labelled_paragraph_print_their_text() -> None:
    doc = compiled(
        "=* Appendix {#app:a}\n\n\\p[id=p:note]{A *note*.}\n\n@app:a, @p:note, \\ref[form=above-below]{p:note}"
    )
    assert refs(doc) == [
        ("REF _Ref_app_a \\h", "Appendix"),
        ("REF _Ref_p_note \\h", "A note."),
        ("REF _Ref_p_note \\p \\h", "above"),
    ]


def test_labelled_list_item() -> None:
    doc = compiled("1. first\n2. {#it:b} second\n\nItem @it:b.")
    item = list(doc.all_paragraphs())[1]
    assert bookmarks(item) == ["_Ref_it_b"]
    assert refs(doc) == [("REF _Ref_it_b \\w \\h", "2")]


def test_page_reference_asks_word_to_update_fields() -> None:
    doc = compiled("= Intro {#sec:intro}\n\npage \\pageref{sec:intro}")
    assert refs(doc) == [("PAGEREF _Ref_sec_intro \\h", "1")]
    assert doc.settings.update_fields_on_open


def test_forward_reference_and_below() -> None:
    doc = compiled("\\ref[form=above-below]{sec:z} @sec:z\n\n= Z {#sec:z}")
    assert refs(doc) == [("REF _Ref_sec_z \\p \\h", "below"), ("REF _Ref_sec_z \\h", "Z")]


def test_undefined_and_duplicate_labels_are_errors() -> None:
    assert errors("= A {#sec:intro}\n\nSee @sec:intr.") == ["WGX0308 unknown label `sec:intr`"]
    assert errors("= A {#x}\n\n= B {#x}") == ["WGX0309 label `x` is defined twice"]


def test_invalid_ref_form() -> None:
    assert errors("= A {#a}\n\n\\ref[form=sideways]{a}") == [
        "WGX0302 `form`: unknown value `sideways`"
    ]


# --- captions and figures ------------------------------------------------------------------------


def test_caption_bookmarks_and_reference_forms() -> None:
    doc = compiled(
        "\\caption[Table, id=tbl:rev]{Revenue}\n| a | b |\n\n"
        "@tbl:rev; \\ref[form=number]{tbl:rev}; \\ref[form=text]{tbl:rev}; \\ref[form=full]{tbl:rev}"
    )
    caption = next(iter(doc.all_paragraphs()))
    assert caption.style == "Caption"
    assert caption.text() == "Table 1: Revenue"
    assert caption.props.get("keep-with-next") is True  # above its table
    assert bookmarks(caption) == [
        "_Ref_tbl_rev_f",
        "_Ref_tbl_rev",
        "_Ref_tbl_rev_n",
        "_Ref_tbl_rev_t",
    ]
    assert refs(doc) == [
        ("REF _Ref_tbl_rev \\h", "Table 1"),
        ("REF _Ref_tbl_rev_n \\h", "1"),
        ("REF _Ref_tbl_rev_t \\h", "Revenue"),
        ("REF _Ref_tbl_rev_f \\h", "Table 1: Revenue"),
    ]


def test_caption_kind_is_inferred_from_neighbours() -> None:
    doc = compiled(
        "\\caption{Above a table}\n\n| a |\n\n"
        "| b |\n\n\\caption{Below a table}\n\n"
        "\\caption{Above code}\n\n```\nx\n```\n\n"
        "Text.\n\n\\caption{Standalone}"
    )
    captions = [p.text() for p in doc.all_paragraphs() if p.style == "Caption"]
    assert captions == [
        "Table 1: Above a table",
        "Table 2: Below a table",
        "Listing 1: Above code",
        "Figure 1: Standalone",
    ]


def test_seq_numbers_captions_per_kind() -> None:
    doc = compiled("\\caption[Figure]{A}\n\n\\caption[Table]{B}\n\n\\caption[Figure]{C}")
    assert [r for c, r in fields(doc) if c.startswith("SEQ")] == ["1", "1", "2"]


def test_figure_centres_and_keeps_image_with_caption() -> None:
    doc = compiled(
        "\\figure[id=fig:logo, width=2cm]{\n  \\image{img/logo.png}\n  \\caption{The logo}\n}\n\n"
        "As @fig:logo shows.",
        EXAMPLES,
    )
    image, caption, _ = list(doc.all_paragraphs())
    picture = image.content[0]
    assert isinstance(picture, ir.Image)
    assert picture.width.emu == 720000  # 2cm
    assert image.props.get("align") == "center"
    assert image.props.get("keep-with-next") is True
    assert caption.props.get("align") == "center"
    assert caption.props.get("keep-with-next") is False
    assert caption.text() == "Figure 1: The logo"
    assert refs(doc) == [("REF _Ref_fig_logo \\h", "Figure 1")]


def test_labelled_figure_needs_a_caption() -> None:
    result = compile_source("\\figure[id=fig:x]{\n  \\image{img/logo.png}\n}", EXAMPLES)
    assert [d.message for _, d in result.diagnostics] == [
        "a labelled \\figure needs a \\caption: references point at the caption"
    ]


def test_code_block_caption() -> None:
    doc = compiled('```py {#lst:x, caption="Example"}\nprint(1)\n```\n\nSee @lst:x.')
    caption, code, _ = list(doc.all_paragraphs())
    assert (caption.style, caption.text(), code.style) == (
        "Caption",
        "Listing 1: Example",
        "Code Block",
    )
    assert refs(doc) == [("REF _Ref_lst_x \\h", "Listing 1")]


def test_block_command_on_its_own_line_ends_the_paragraph() -> None:
    doc = compiled("Text before\n\\caption{A caption}\nText after")
    assert [p.text() for p in doc.all_paragraphs()] == [
        "Text before",
        "Figure 1: A caption",
        "Text after",
    ]
    assert errors("Text \\caption{inline} more") == [
        "WGX0303 `\\caption` is a block command and cannot appear inside a paragraph"
    ]


# --- counters ----------------------------------------------------------------------------------


def test_seq_counters_with_labels_formats_and_restarts() -> None:
    doc = compiled(
        HEADINGS
        + "= A\n\nR\\seq[Req, restart=heading-1] R\\seq[Req, id=req:b, restart=heading-1]\n\n"
        "= B\n\nR\\seq[Req, restart=heading-1] \\seq[Step, format=lower-roman] \\seq[Step, format=lower-roman]"
        " \\seq[Step, restart=7]\n\n@req:b"
    )
    assert fields(doc) == [
        ("SEQ Req \\* ARABIC \\s 1", "1"),
        ("SEQ Req \\* ARABIC \\s 1", "2"),
        ("SEQ Req \\* ARABIC \\s 1", "1"),
        ("SEQ Step \\* roman", "i"),
        ("SEQ Step \\* roman", "ii"),
        ("SEQ Step \\* ARABIC \\r 7", "7"),
        ("REF _Ref_req_b \\h", "2"),
    ]


def test_seq_needs_a_name() -> None:
    assert errors("\\seq") == ["WGX0302 `\\seq` needs `name`"]


# --- tables of contents --------------------------------------------------------------------------


def test_toc_entries_link_to_headings() -> None:
    doc = compiled(
        HEADINGS + '\\toc[levels=1-2, title="Contents"]\n\n= One\n\n== Sub\n\n=== Deep\n\n=* Annex'
    )
    title, toc = doc.body[:2]
    assert isinstance(title, ir.Paragraph)
    assert (title.style, title.text()) == ("TOC Heading", "Contents")
    assert isinstance(toc, ir.FieldBlock)
    assert toc.field.code == ' TOC \\o "1-2" \\h \\z \\u '
    entries = [(p.style, p.text()) for p in toc.content]
    assert entries == [("TOC 1", "1\tOne\t"), ("TOC 2", "1.1\tSub\t"), ("TOC 1", "Annex\t")]
    # as Word writes them: a tab stop after a heading number, and the dotted page-number one
    page = TabStop(ir.PageSetup().text_width, "right", "dot")
    stops = [p.props.get("tabs") for p in toc.content]
    assert stops == [
        (TabStop(Length.twips(440), "left"), page),
        (TabStop(Length.twips(880), "left"), page),
        (page,),
    ]
    link = toc.content[0].content[0]
    assert isinstance(link, ir.Hyperlink)
    heading = doc.body[2]
    assert isinstance(heading, ir.Paragraph)
    assert link.anchor == bookmarks(heading)[0]
    assert doc.settings.update_fields_on_open
    styles = {s.name: s for s in doc.styles}
    assert {"TOC Heading", "TOC 1", "TOC 2"} <= set(styles)
    assert "TOC 3" not in styles
    tabs = styles["TOC 1"].paragraph.get("tabs")
    assert isinstance(tabs, tuple)
    assert tabs[0].leader == "dot"  # pyright: ignore[reportUnknownMemberType]


def test_list_of_figures() -> None:
    doc = compiled(
        "\\listof[Figure]\n\n\\caption[Figure]{One}\n\n\\caption[Table]{Other}\n\n\\caption[Figure]{Two}"
    )
    toc = doc.body[0]
    assert isinstance(toc, ir.FieldBlock)
    assert toc.field.code == ' TOC \\h \\z \\c "Figure" '
    assert [(p.style, p.text()) for p in toc.content] == [
        ("table of figures", "Figure 1: One\t"),
        ("table of figures", "Figure 2: Two\t"),
    ]


def test_empty_toc_says_how_to_fill_it() -> None:
    doc = compiled("\\toc")
    toc = doc.body[0]
    assert isinstance(toc, ir.FieldBlock)
    assert "update the field" in toc.content[0].text()


def test_toc_only_in_the_body() -> None:
    assert errors("\\table{\\row{\\cell{\\toc}}}") == [
        "WGX0303 `\\toc` must be in the document body"
    ]


@pytest.mark.parametrize(
    "text",
    [
        HEADINGS + "\\toc\n\n\\listof[Table]\n\n= A {#a}\n\n\\caption[Table, id=t]{T}\n| x |\n\n"
        "@a @t \\pageref{t} \\seq[N, id=n] @n",
    ],
)
def test_references_build_valid_docx(text: str) -> None:
    data, result = build(text)
    assert data is not None, result.render_diagnostics()
    assert validate_package(data) == {}
