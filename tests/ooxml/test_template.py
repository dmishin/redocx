"""Compiling into templates (.dotx / .docx): architecture §4.6."""

from __future__ import annotations

import io
import subprocess
import zipfile
from pathlib import Path

import pytest
from lxml import etree

from tests.support.xsd import validate_package
from wordgenx import ir
from wordgenx.cli import main
from wordgenx.compiler import build, compile_source
from wordgenx.ooxml.constants import CT
from wordgenx.ooxml.parts.theme import Theme, theme_from_xml, theme_xml
from wordgenx.ooxml.template import load_template
from wordgenx.values import Length

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
VALIDATOR = (
    Path(__file__).resolve().parents[2]
    / "tools/ooxml-validate/bin/Release/net10.0/ooxml-validate.exe"
)

TEMPLATE_SOURCE = r"""\styles{
  paragraph "Heading 1" : Normal { color: #C00000; size: 18pt; outline-level: 1; }
  paragraph Fancy : Normal { italic; color: #00B050; }
  character Strong { underline: single; }
  numbering Steps { level 1 { format: decimal; text: "Step %1."; } }
  paragraph Step : Normal { numbering: Steps level 1; }
}
\section[size=A5, orientation=landscape]
\header{TEMPLATE HEADER}
\footer[first]{TEMPLATE FIRST FOOTER}
"""


def make_template(tmp_path: Path, source: str = TEMPLATE_SOURCE, name: str = "t.dotx") -> Path:
    data, result = build(source, tmp_path, as_template=True)
    assert data is not None, result.render_diagnostics()
    path = tmp_path / name
    path.write_bytes(data)
    return path


def build_with(template: Path, text: str, *, validate: bool = True) -> zipfile.ZipFile:
    data, result = build(text, template.parent, template)
    assert data is not None, result.render_diagnostics()
    if validate:
        assert validate_package(data) == {}
    return zipfile.ZipFile(io.BytesIO(data))


def xml(package: zipfile.ZipFile, name: str) -> etree._Element:  # pyright: ignore[reportPrivateUsage]
    return etree.fromstring(package.read(name))


def style(package: zipfile.ZipFile, sid: str) -> etree._Element:  # pyright: ignore[reportPrivateUsage]
    (found,) = [
        s
        for s in xml(package, "word/styles.xml").findall(f"{W}style")
        if s.get(f"{W}styleId") == sid
    ]
    return found


def content_type(package: zipfile.ZipFile, part: str) -> str | None:
    types = xml(package, "[Content_Types].xml")
    for override in types:
        if override.get("PartName") == part:
            return override.get("ContentType")
    return None


def texts(package: zipfile.ZipFile, part: str = "word/document.xml") -> list[str]:
    root = xml(package, part)
    return ["".join(t.text or "" for t in p.iter(f"{W}t")) for p in root.iter(f"{W}p")]


# --- writing templates -------------------------------------------------------------------------


def test_dotx_output_has_the_template_content_type(tmp_path: Path) -> None:
    template = make_template(tmp_path)
    package = zipfile.ZipFile(template)
    assert content_type(package, "/word/document.xml") == CT.WML_TEMPLATE_MAIN
    loaded = load_template(template)
    assert loaded.is_template
    assert loaded.style("Fancy") is not None
    assert loaded.style("heading 1") == loaded.style("Heading 1")


def test_documents_built_from_a_template_are_documents(tmp_path: Path) -> None:
    package = build_with(make_template(tmp_path), "Text.")
    assert content_type(package, "/word/document.xml") == CT.WML_DOCUMENT_MAIN


# --- styles --------------------------------------------------------------------------------------


def test_template_styles_can_be_used_without_definitions(tmp_path: Path) -> None:
    package = build_with(make_template(tmp_path), "\\p[style=Fancy]{Styled.}")
    fancy = [
        s
        for s in xml(package, "word/styles.xml").findall(f"{W}style")
        if s.get(f"{W}styleId") == "Fancy"
    ]
    assert len(fancy) == 1
    assert fancy[0].find(f"{W}rPr/{W}i") is not None


def test_style_sheets_modify_template_styles(tmp_path: Path) -> None:
    package = build_with(
        make_template(tmp_path),
        '\\styles{ paragraph "Heading 1" { italic; size: 20pt; } }\n\n= Title',
    )
    heading = style(package, "Heading1")
    rpr = heading.find(f"{W}rPr")
    assert rpr is not None
    assert rpr.find(f"{W}color").get(f"{W}val") == "C00000"  # the template's  # pyright: ignore[reportOptionalMemberAccess]
    assert rpr.find(f"{W}i") is not None  # ours
    assert rpr.find(f"{W}sz").get(f"{W}val") == "40"  # ours wins  # pyright: ignore[reportOptionalMemberAccess]


def test_builtins_the_template_defines_are_the_templates(tmp_path: Path) -> None:
    package = build_with(make_template(tmp_path), "Some *strong* text.")
    strong = style(package, "Strong")
    rpr = strong.find(f"{W}rPr")
    assert rpr is not None
    assert rpr.find(f"{W}u") is not None
    assert rpr.find(f"{W}b") is None  # wordgenx's own Strong (bold) is not merged in


def test_template_defaults_replace_wordgenx_defaults(tmp_path: Path) -> None:
    template = make_template(tmp_path, "\\styles{ defaults { size: 9pt; } }")
    package = build_with(template, "Text.")
    size = xml(package, "word/styles.xml").find(f"{W}docDefaults/{W}rPrDefault/{W}rPr/{W}sz")
    assert size is not None
    assert size.get(f"{W}val") == "18"


# --- numbering -----------------------------------------------------------------------------------


def test_numbering_ids_follow_the_templates(tmp_path: Path) -> None:
    package = build_with(make_template(tmp_path), "\\p[style=Step]{a}\n\n1. b\n2. c")
    numbering = xml(package, "word/numbering.xml")
    num_ids = [n.get(f"{W}numId") for n in numbering.findall(f"{W}num")]
    abstract_ids = [a.get(f"{W}abstractNumId") for a in numbering.findall(f"{W}abstractNum")]
    assert len(num_ids) == len(set(num_ids))
    assert len(abstract_ids) == len(set(abstract_ids))
    used = {n.get(f"{W}val") for n in xml(package, "word/document.xml").iter(f"{W}numId")}
    step = style(package, "Step").find(f"{W}pPr/{W}numPr/{W}numId")
    assert step is not None
    assert used | {step.get(f"{W}val")} <= set(num_ids)
    assert step.get(f"{W}val") not in used  # the template's list and ours are distinct


def test_lists_can_use_template_list_definitions(tmp_path: Path) -> None:
    template = make_template(tmp_path)
    template_numbering = xml(zipfile.ZipFile(template), "word/numbering.xml")
    (steps_abstract,) = [
        a.get(f"{W}abstractNumId")
        for a in template_numbering.findall(f"{W}abstractNum")
        if a.find(f"{W}name").get(f"{W}val") == "Steps"  # pyright: ignore[reportOptionalMemberAccess]
    ]
    template_nums = {n.get(f"{W}numId") for n in template_numbering.findall(f"{W}num")}

    data, result = build(
        "\\list[Steps]{\n1. a\n2. b\n}\n\n\\list[Steps, restart=5]{\n1. c\n}", tmp_path, template
    )
    assert data is not None, result.render_diagnostics()
    assert validate_package(data) == {}
    package = zipfile.ZipFile(io.BytesIO(data))
    numbering = xml(package, "word/numbering.xml")
    names = [
        a.find(f"{W}name").get(f"{W}val")  # pyright: ignore[reportOptionalMemberAccess]
        for a in numbering.findall(f"{W}abstractNum")
        if a.find(f"{W}name") is not None
    ]
    assert names.count("Steps") == 1  # the template's, not copied
    nums = {n.get(f"{W}numId"): n for n in numbering.findall(f"{W}num")}
    assert template_nums <= set(nums)

    used = [n.get(f"{W}val") for n in xml(package, "word/document.xml").iter(f"{W}numId")]
    assert len(used) == 3
    assert used[0] == used[1] != used[2]  # one list, then a separately counted one
    starts: list[str | None] = []
    for num_id in (used[0], used[2]):
        assert num_id not in template_nums
        assert int(num_id) > max(int(n) for n in template_nums)  # pyright: ignore[reportArgumentType]
        num = nums[num_id]
        assert num.find(f"{W}abstractNumId").get(f"{W}val") == steps_abstract  # pyright: ignore[reportOptionalMemberAccess]
        starts.append(num.find(f"{W}lvlOverride/{W}startOverride").get(f"{W}val"))  # pyright: ignore[reportOptionalMemberAccess]
    assert starts == ["1", "5"]
    styles = [s.get(f"{W}val") for s in xml(package, "word/document.xml").iter(f"{W}pStyle")]
    assert styles == ["Step"] * 3  # the template level's linked style

    if VALIDATOR.exists():  # the Open XML SDK too, where it has been built
        out = tmp_path / "out.docx"
        out.write_bytes(data)
        run = subprocess.run([VALIDATOR, out], capture_output=True, text=True, check=False)
        assert run.returncode == 0, run.stdout + run.stderr


def test_unknown_list_names_suggest_template_lists(tmp_path: Path) -> None:
    data, result = build("\\list[Stpes]{1. a}", tmp_path, make_template(tmp_path))
    assert data is None
    assert "did you mean `Steps`?" in result.render_diagnostics()


# --- page setup, headers and footers -------------------------------------------------------------


def test_page_setup_comes_from_the_template(tmp_path: Path) -> None:
    template = make_template(tmp_path)
    result = compile_source("Text.", tmp_path, template)
    assert result.document is not None
    page = result.document.section.page
    assert (page.width.in_twips, page.landscape) == (Length.mm(210).in_twips, True)  # A5
    result = compile_source("\\section[size=A4]\n\nText.", tmp_path, template)
    assert result.document is not None
    assert result.document.section.page.height.in_twips == Length.mm(297).in_twips  # A4


def test_headers_and_footers_carry_over(tmp_path: Path) -> None:
    package = build_with(make_template(tmp_path), "Text.")
    (section,) = xml(package, "word/document.xml").iter(f"{W}sectPr")
    references = {
        (etree.QName(r).localname, r.get(f"{W}type"))
        for r in section
        if "Reference" in etree.QName(r).localname
    }
    assert references == {("headerReference", "default"), ("footerReference", "first")}
    headers = [n for n in package.namelist() if "header" in n and n.endswith(".xml")]
    assert any("TEMPLATE HEADER" in "".join(texts(package, h)) for h in headers)


def test_document_headers_replace_the_templates(tmp_path: Path) -> None:
    package = build_with(make_template(tmp_path), "\\header{Mine}\n\nText.")
    (section,) = xml(package, "word/document.xml").iter(f"{W}sectPr")
    kinds = sorted(
        (etree.QName(r).localname, r.get(f"{W}type") or "")
        for r in section
        if "Reference" in etree.QName(r).localname
    )
    assert kinds == [("footerReference", "first"), ("headerReference", "default")]
    headers = [n for n in package.namelist() if "header" in n and n.endswith(".xml")]
    assert ["".join(texts(package, h)) for h in headers] == ["Mine"]


# --- body content ---------------------------------------------------------------------------------


def test_template_body_content_comes_first(tmp_path: Path) -> None:
    template = make_template(tmp_path, "Boilerplate with a [link](https://example.org).")
    package = build_with(template, "Ours.")
    assert texts(package)[:2] == ["Boilerplate with a link.", "Ours."]
    rels = package.read("word/_rels/document.xml.rels").decode()
    assert "https://example.org" in rels


def test_template_body_with_notes_is_dropped_with_a_warning(tmp_path: Path) -> None:
    template = make_template(tmp_path, "Boilerplate.\\footnote{A note.}")
    result = compile_source("Ours.", tmp_path, template)
    assert result.document is not None
    assert [d.message for _, d in result.diagnostics] == [
        "the body content of `t.dotx` is not carried over: it contains footnotes"
    ]


# --- other parts, localized templates, themes --------------------------------------------------------


def _rewrite(path: Path, edit: dict[str, bytes], extra: dict[str, bytes] | None = None) -> Path:
    source = zipfile.ZipFile(path)
    out = path.with_name("edited-" + path.name)
    with zipfile.ZipFile(out, "w") as target:
        for name in source.namelist():
            target.writestr(name, edit.get(name, source.read(name)))
        for name, data in (extra or {}).items():
            target.writestr(name, data)
    return out


def test_unmanaged_parts_are_copied(tmp_path: Path) -> None:
    template = make_template(tmp_path)
    package = zipfile.ZipFile(template)
    rels = (
        package.read("word/_rels/document.xml.rels")
        .decode()
        .replace(
            "</Relationships>",
            '<Relationship Id="rId99" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
            'relationships/customXml" Target="../customXml/item1.xml"/></Relationships>',
        )
    )
    edited = _rewrite(
        template,
        {"word/_rels/document.xml.rels": rels.encode()},
        {"customXml/item1.xml": b"<data>keep me</data>"},
    )
    out = build_with(edited, "Text.", validate=False)  # the custom XML is not OOXML
    copied = [n for n in out.namelist() if n.startswith("customXml/")]
    assert len(copied) == 1
    assert out.read(copied[0]) == b"<data>keep me</data>"
    assert "customXml" in out.read("word/_rels/document.xml.rels").decode()


def test_localized_style_ids_are_used(tmp_path: Path) -> None:
    template = make_template(tmp_path)
    package = zipfile.ZipFile(template)
    styles = package.read("word/styles.xml").replace(
        b'w:styleId="Heading1"', b'w:styleId="berschrift1"'
    )
    edited = _rewrite(template, {"word/styles.xml": styles})
    out = build_with(edited, "= Titel")
    (heading,) = [p for p in xml(out, "word/document.xml").iter(f"{W}pStyle")]
    assert heading.get(f"{W}val") == "berschrift1"
    ids = [s.get(f"{W}styleId") for s in xml(out, "word/styles.xml").findall(f"{W}style")]
    assert "Heading1" not in ids


def test_theme_colors_are_read() -> None:
    theme = Theme(colors={**Theme().colors, "accent1": "123456"}, major_font="Georgia")
    parsed = theme_from_xml(theme_xml(theme))
    assert (parsed.colors["accent1"], parsed.major_font) == ("123456", "Georgia")


# --- options and errors ---------------------------------------------------------------------------


def test_document_option_and_errors(tmp_path: Path) -> None:
    make_template(tmp_path)
    result = compile_source('\\document[template="t.dotx"]\n\nText.', tmp_path)
    assert result.document is not None
    assert result.document.template is not None
    missing = compile_source('\\document[template="nope.dotx"]\n\nText.', tmp_path)
    assert [d.code.value for _, d in missing.diagnostics] == ["WGX0310"]
    (tmp_path / "bad.dotx").write_bytes(b"not a zip")
    bad = compile_source('\\document[template="bad.dotx"]', tmp_path)
    assert "not a Word document or template" in bad.render_diagnostics()
    late = compile_source('Text.\n\n\\document[template="t.dotx"]', tmp_path)
    assert "before any content" in late.render_diagnostics()


def test_cli_template_and_dotx_output(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    source = tmp_path / "corp.wgx"
    source.write_text(TEMPLATE_SOURCE, encoding="utf-8")
    assert main(["build", str(source), "-o", str(tmp_path / "corp.dotx")]) == 0
    doc = tmp_path / "doc.wgx"
    doc.write_text("= Hello\n\nWorld.", encoding="utf-8")
    assert main(["build", str(doc), "--template", str(tmp_path / "corp.dotx")]) == 0
    out = zipfile.ZipFile(tmp_path / "doc.docx")
    assert content_type(out, "/word/document.xml") == CT.WML_DOCUMENT_MAIN
    assert any("TEMPLATE HEADER" in "".join(texts(out, n)) for n in out.namelist() if "header" in n)
    capsys.readouterr()


def test_word_templates_on_this_machine_load() -> None:
    """Office's own templates, when installed (a robustness check; not part of CI)."""
    folder = Path("C:/Program Files/Microsoft Office/root/Templates/1033")
    templates = sorted(folder.glob("*.dotx")) if folder.exists() else []
    if not templates:
        pytest.skip("no Office templates installed")
    for path in templates:
        template = load_template(path)
        data, result = build("= Heading\n\nText.", Path.cwd(), path)
        assert data is not None, (path.name, result.render_diagnostics())
        assert template.style("Normal") is not None


def test_template_is_kept_on_the_document(tmp_path: Path) -> None:
    template = make_template(tmp_path)
    result = compile_source("Text.", tmp_path, template)
    assert isinstance(result.document, ir.Document)
    assert result.document.template is not None
    assert result.document.template.name == "t.dotx"
