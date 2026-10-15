from pathlib import Path

import pytest

from wordgenx import ir
from wordgenx.cli import main
from wordgenx.compiler import CompileResult, build, compile_source
from wordgenx.props import para_props, run_props
from wordgenx.syntax.diagnostics import Code, Severity
from wordgenx.values import Length


def compiled(text: str, base_dir: Path | None = None) -> ir.Document:
    result = compile_source(text, base_dir)
    assert result.document is not None, result.render_diagnostics()
    return result.document


def diagnostics(text: str, base_dir: Path | None = None) -> CompileResult:
    return compile_source(text, base_dir)


def texts(document: ir.Document) -> list[str]:
    return ["".join(run.text for run in p.runs()) for p in document.body]


def style_names(document: ir.Document) -> set[str]:
    return {s.name for s in document.styles}


# --- text -------------------------------------------------------------------------------


def test_whitespace_collapses_and_smart_punctuation() -> None:
    doc = compiled('  Several   spaces\nand "quotes" -- dashes --- here; it\'s  ')
    assert texts(doc) == [
        "Several spaces and \u201cquotes\u201d \u2013 dashes \u2014 here; it\u2019s"
    ]


def test_smart_quotes_can_be_disabled() -> None:
    doc = compiled('\\document[smart-quotes=off]\n\n"plain" -- text')
    assert texts(doc) == ['"plain" -- text']


def test_code_spans_are_verbatim() -> None:
    doc = compiled('Run `a  --  "b"` now')
    (paragraph,) = doc.body
    code = paragraph.runs()[1]
    assert (code.text, code.style) == ('a  --  "b"', "Code")


def test_inline_sugar_uses_character_styles() -> None:
    doc = compiled("*strong* _emph_ `code`")
    assert [(r.text, r.style) for r in doc.body[0].runs()] == [
        ("strong", "Strong"), (" ", None), ("emph", "Emphasis"), (" ", None), ("code", "Code"),
    ]  # fmt: skip
    assert {"Strong", "Emphasis", "Code"} <= style_names(doc)


def test_nested_character_styles_keep_the_outer_look() -> None:
    doc = compiled("_a *b*_")
    runs = doc.body[0].runs()
    assert (runs[1].text, runs[1].style, runs[1].props) == ("b", "Strong", run_props(italic=True))


def test_formatting_commands() -> None:
    doc = compiled(
        r"\b{B}\i{I}\u[double]{U}\s{S}\sup{2}\sub{i}\hl{H}\lang[de-DE]{L}\hidden{X}\font[size=14pt, color=#C00000, caps]{F}"
    )
    props = [r.props for r in doc.body[0].runs()]
    assert props == [
        run_props(bold=True), run_props(italic=True), run_props(underline="double"),
        run_props(strike=True), run_props(vertical_align="superscript"),
        run_props(vertical_align="subscript"), run_props(highlight="yellow"),
        run_props(lang="de-DE"), run_props(hidden=True),
        run_props(size="14pt", color="#C00000", caps=True),
    ]  # fmt: skip


def test_breaks_and_tabs_trim_spaces() -> None:
    doc = compiled(r"Name: \tab value \br next \pagebreak after")
    (paragraph,) = doc.body
    assert paragraph.content[0] == ir.Run("Name:\tvalue\nnext")
    assert paragraph.content[1] == ir.Break("page")
    assert paragraph.content[2] == ir.Run("after")


def test_links() -> None:
    doc = compiled(
        r'[site](https://x.org) <https://y.org> \link[to="https://z.org"]{*z*} \url{mailto:a@b.c}'
    )
    links = [item for item in doc.body[0].content if isinstance(item, ir.Hyperlink)]
    assert [(link.url, "".join(r.text for r in link.content)) for link in links] == [
        ("https://x.org", "site"), ("https://y.org", "https://y.org"),
        ("https://z.org", "z"), ("mailto:a@b.c", "a@b.c"),
    ]  # fmt: skip
    assert links[0].content[0].style == "Hyperlink"
    assert links[2].content[0].style == "Strong"  # inner style; Hyperlink look as direct formatting
    assert "Hyperlink" in style_names(doc)


# --- blocks ------------------------------------------------------------------------------


def test_headings_and_unnumbered_headings() -> None:
    doc = compiled("= One\n\n=== Three\n\n==* Plain")
    assert [(p.style, p.unnumbered) for p in doc.body] == [
        ("Heading 1", False), ("Heading 3", False), ("Heading 2", True),
    ]  # fmt: skip
    assert {"Heading 1", "Heading 2", "Heading 3"} <= style_names(doc)


def test_p_and_align() -> None:
    doc = compiled(
        '\\styles{ paragraph "Body Text" { justify; } }\n\n'
        '\\p[style="Body Text", space-before=12pt, keep-with-next]{Text}\n'
        "\\align[center]{One\n\nTwo}"
    )
    first, *centred = doc.body
    assert (first.style, first.props) == (
        "Body Text",
        para_props(space_before="12pt", keep_with_next=True),
    )
    assert [p.props for p in centred] == [para_props(align="center")] * 2


def test_code_block_lines() -> None:
    doc = compiled("```\na  b\n\nc\n```")
    assert [(p.style, texts(ir.Document(body=(p,)))[0]) for p in doc.body] == [
        ("Code Block", "a  b"), ("Code Block", ""), ("Code Block", "c"),
    ]  # fmt: skip


def test_preamble_commands() -> None:
    doc = compiled(
        '\\document[lang=de-DE]\n\\meta[title="T", author=A, keywords=(x, y)]\n'
        "\\settings[even-odd-headers, update-fields-on-open=on, default-tab-stop=1cm]\n"
        "\\section[size=Letter, orientation=landscape, margins=(all=2cm, left=3cm)]\n\nText"
    )
    assert (doc.meta.title, doc.meta.author, doc.meta.keywords, doc.meta.language) == (
        "T",
        "A",
        ("x", "y"),
        "de-DE",
    )
    assert doc.settings == ir.Settings(Length.cm(1), True, True)
    assert (doc.page.width, doc.page.height) == (Length.inch(11), Length.inch("8.5"))
    assert (doc.page.margin_top, doc.page.margin_left) == (Length.cm(2), Length.cm(3))


# --- lists -------------------------------------------------------------------------------------


def test_lists_numbering_levels_and_instances() -> None:
    doc = compiled("- a\n- b\n  - c\n\nText\n\n1. d\n   a. e\n\nText\n\n1. f")
    refs = [p.list_item for p in doc.body]
    assert [(r.numbering, r.level) if r else None for r in refs] == [
        ("Bullets", 1), ("Bullets", 1), ("Bullets", 2), None, ("Numbers", 1), ("Numbers", 2), None, ("Numbers", 1),
    ]  # fmt: skip
    instances = [r.instance for r in refs if r]
    assert instances[0] == instances[1] == instances[2]  # one bullet list
    assert instances[3] == instances[4] != instances[5]  # separate numbered lists restart
    assert [p.style for p in doc.body if p.list_item] == ["List Paragraph"] * 6
    assert {"Bullets", "Numbers"} <= {n.name for n in doc.numbering}


def test_mixed_nesting_starts_a_new_list_one_level_deeper() -> None:
    doc = compiled("1. a\n   - b")
    first, second = (p.list_item for p in doc.body)
    assert first is not None
    assert second is not None
    assert (second.numbering, second.level) == ("Bullets", 2)
    assert first.instance != second.instance


def test_item_continuation_paragraphs_are_indented() -> None:
    doc = compiled("1. first\n\n   more\n2. second")
    first, more, second = doc.body
    assert first.list_item is not None
    assert second.list_item is not None
    assert more.list_item is None
    assert more.props["indent-left"] == Length.twips(360)
    assert more.style == "List Paragraph"


def test_named_lists_continue_and_restart() -> None:
    doc = compiled(
        '\\styles{ numbering Legal { level 1 { text: "%1."; } } }\n\n'
        "\\list[Legal]{\n1. a\n}\n\n\\list[Legal, continue]{\n1. b\n}\n\n\\list[Legal, restart=5]{\n1. c\n}"
    )
    refs = [p.list_item for p in doc.body if p.list_item]
    assert [r.numbering for r in refs] == ["Legal"] * 3
    assert refs[0].instance == refs[1].instance != refs[2].instance
    assert refs[2].start == 5


def test_list_level_style_from_numbering() -> None:
    doc = compiled(
        '\\styles{ paragraph "Clause 1" { bold; }\n numbering MSA { level 1 { style: "Clause 1"; } } }\n\n'
        "\\list[MSA]{\n1. Term\n}"
    )
    assert doc.body[0].style == "Clause 1"


# --- macros, includes ------------------------------------------------------------------------


def test_let_and_define() -> None:
    doc = compiled(
        "\\let{product}{wordgenx}\n"
        '\\define{note}[title="Note", id]{\\b{#title:} #body}\n\n'
        "The \\product{}s are here. \\note[title=Tip]{Read *this*.} \\note{Defaults.}"
    )
    assert texts(doc) == ["The wordgenxs are here. Tip: Read this. Note: Defaults."]


def test_block_macro() -> None:
    doc = compiled("\\define{two}{First.\n\nSecond.}\n\n\\two")
    assert texts(doc) == ["First.", "Second."]


def test_macro_errors() -> None:
    result = diagnostics(
        "\\define{mac}[a]{#a #nope}\n\\define{b}{x}\n\\define{loop}{\\loop}\n\n\\mac[a=1, z=2] \\mac[positional] \\loop"
    )
    messages = [d.message for _, d in result.diagnostics if d.code is Code.MACRO]
    assert "unknown parameter `#nope`" in messages
    assert "`\\mac` has no parameter `z`" in messages
    assert "`\\mac` takes named options" in messages
    assert "`\\b` is a built-in command and cannot be redefined" in messages
    assert any("deeper than" in m for m in messages)


def test_include_and_macro_files(tmp_path: Path) -> None:
    (tmp_path / "chapter.wgx").write_text(
        "== Included\n\nChapter text with \\shout{it}.", encoding="utf-8"
    )
    (tmp_path / "macros.wgx").write_text(
        "\\define{shout}{\\b{#body}}\n\\use{more.wgx}", encoding="utf-8"
    )
    (tmp_path / "more.wgx").write_text("\\let{x}{X}", encoding="utf-8")
    (tmp_path / "self.wgx").write_text("\\include{self.wgx}", encoding="utf-8")
    doc = compiled("\\use{macros.wgx}\n\n= Main \\x\n\n\\include{chapter.wgx}", tmp_path)
    assert texts(doc) == ["Main X", "Included", "Chapter text with it."]
    assert doc.body[2].runs()[1].props == run_props(bold=True)
    loop = diagnostics("\\include{self.wgx}", tmp_path)
    assert any("includes itself" in d.message for _, d in loop.diagnostics)
    missing = diagnostics("\\include{nope.wgx}", tmp_path)
    assert [d.code for _, d in missing.diagnostics] == [Code.INCLUDE]


# --- diagnostics ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "code", "message"),
    [
        (r"\nosuch{x}", Code.UNKNOWN_COMMAND, "unknown command `\\nosuch`"),
        (r"\bb{x}", Code.UNKNOWN_COMMAND, "unknown command `\\bb`"),
        (r"Text \p{inside}", Code.MISPLACED, "a block cannot appear inside a paragraph"),
        (r"\b{\p{x}}", Code.MISPLACED, "`\\p` is a block command and cannot appear inside text"),
        (
            r"\p[spacebefore=1pt]{x}",
            Code.INVALID_ARGUMENT,
            "unknown option `spacebefore` for `\\p`",
        ),
        (r"\p[space-before=big]{x}", Code.INVALID_ARGUMENT, "space-before: not a length"),
        (r"\p[style=Nope]{x}", Code.UNDEFINED_STYLE, "paragraph style `Nope` is not defined"),
        (r'\style["Heading 1"]{x}', Code.UNDEFINED_STYLE, "`Heading 1` is a paragraph style"),
        (r"\list[Nope]{1. x}", Code.UNDEFINED_STYLE, "numbering `Nope` is not defined"),
        (r"\br{x}", Code.INVALID_ARGUMENT, "`\\br` takes no body"),
        (r"\section[size=B7]", Code.INVALID_ARGUMENT, "unknown page size"),
    ],
)
def test_errors(text: str, code: Code, message: str) -> None:
    result = diagnostics(text)
    assert result.document is None
    assert any(d.code is code and message in d.message for _, d in result.diagnostics), (
        result.render_diagnostics()
    )


def test_suggestions() -> None:
    result = diagnostics(r"\p[keep-with-nxt]{x}")
    ((_, d),) = result.diagnostics
    assert d.label == "did you mean `keep-with-next`?"


def test_planned_features_warn_but_build() -> None:
    result = diagnostics(
        r"Text\footnote{note} and @fig:x and \comment[author=A]{kept}" + "\n\n\\toc"
    )
    assert result.document is not None
    assert {d.severity for _, d in result.diagnostics} == {Severity.WARNING}
    assert texts(result.document) == ["Text and fig:x and kept"]


# --- build ------------------------------------------------------------------------------------


def test_build_produces_valid_docx() -> None:
    from tests.support.xsd import validate_package

    data, result = build("= Title\n\n- item *one*\n- [link](https://x.org)\n\n1. a\n   b. b")
    assert data is not None, result.render_diagnostics()
    assert validate_package(data) == {}


def test_cli_build(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    source = tmp_path / "doc.wgx"
    source.write_text("= Hello\n\nWorld.", encoding="utf-8")
    assert main(["build", str(source)]) == 0
    assert (tmp_path / "doc.docx").exists()
    assert "wrote" in capsys.readouterr().err

    bad = tmp_path / "bad.wgx"
    bad.write_text("\\nosuch", encoding="utf-8")
    assert main(["build", str(bad), "-o", str(tmp_path / "bad.docx")]) == 1
    assert not (tmp_path / "bad.docx").exists()
    assert "build failed: 1 error" in capsys.readouterr().err
