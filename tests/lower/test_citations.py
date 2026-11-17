"""Citations and bibliographies (design §3.19)."""

from __future__ import annotations

import io
import shutil
import zipfile
from pathlib import Path

import pytest
from lxml import etree

from redocx import ir
from redocx.bibliography import DATA, html_runs
from redocx.compiler import build, compile_source
from tests.support.xsd import validate_package

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"

BIBTEX = r"""
@book{daganzo2005,
  author = {Daganzo, Carlos F.}, title = {Fundamentals of Transportation and Traffic Operations},
  publisher = {Pergamon}, address = {Oxford}, year = {2005}
}
@article{tibshirani1996,
  author = {Tibshirani, Robert}, title = {Regression Shrinkage and Selection via the Lasso},
  journal = {Journal of the Royal Statistical Society: Series B}, volume = {58}, number = {1},
  pages = {267--288}, year = {1996}, doi = {10.1111/j.2517-6161.1996.tb02080.x}
}
@book{ortuzar2011,
  author = {Ort{\'u}zar, Juan de Dios and Willumsen, Luis G.}, title = {Modelling Transport},
  publisher = {Wiley}, year = {2011}
}
"""
CSL_JSON = '[{"id": "knuth1984", "type": "book", "title": "The TeXbook", "author": [{"family": "Knuth", "given": "Donald E."}], "issued": {"date-parts": [[1984]]}, "publisher": "Addison-Wesley"}]'
PREAMBLE = "\\document[lang=en-GB]\n\\bibliography[file=(refs.bib, refs.json)]\n\n"


@pytest.fixture
def folder(tmp_path: Path) -> Path:
    (tmp_path / "refs.bib").write_text(BIBTEX, encoding="utf-8")
    (tmp_path / "refs.json").write_text(CSL_JSON, encoding="utf-8")
    return tmp_path


def compiled(text: str, folder: Path) -> ir.Document:
    result = compile_source(text, folder)
    assert result.document is not None, result.render_diagnostics()
    assert not result.diagnostics, result.render_diagnostics()
    return result.document


def errors(text: str, folder: Path) -> list[str]:
    return [d.message for _, d in compile_source(text, folder).diagnostics if d.is_error]


def texts(doc: ir.Document) -> list[tuple[str | None, str]]:
    return [(p.style, p.text()) for p in doc.all_paragraphs()]


def test_citations_render_with_the_style(folder: Path) -> None:
    doc = compiled(
        PREAMBLE + "A \\cite{daganzo2005}. B \\textcite{tibshirani1996}. "
        'C \\cite[page=214, prefix=see, suffix="Lemma 2"]{tibshirani1996}. '
        "D \\cite{ortuzar2011, daganzo2005}. E \\textcite[page=12]{knuth1984}.",
        folder,
    )
    ((_, text),) = texts(doc)
    assert text == (
        "A (Daganzo, 2005). B Tibshirani (1996). C (see Tibshirani, 1996, p. 214, Lemma 2). "
        "D (Daganzo, 2005; Ortúzar and Willumsen, 2011). E Knuth (1984, p. 12)."
    )


def test_bibliography_lists_cited_entries_in_order(folder: Path) -> None:
    doc = compiled(
        PREAMBLE + "\\cite{tibshirani1996} \\cite{daganzo2005}\n\n\\printbibliography", folder
    )
    body = texts(doc)
    assert body[1] == ("Heading 1", "References")
    assert [style for style, _ in body[2:]] == ["Bibliography", "Bibliography"]
    assert body[2][1].startswith("Daganzo, C.F. (2005) Fundamentals")
    assert "Available at: https://doi.org/10.1111/j.2517-6161.1996.tb02080.x." in body[3][1]
    heading = list(doc.all_paragraphs())[1]
    assert heading.unnumbered
    styles = {s.name for s in doc.styles}
    assert "Bibliography" in styles


def test_citations_link_to_their_entries(folder: Path) -> None:
    doc = compiled(PREAMBLE + "\\cite{daganzo2005}\n\n\\printbibliography", folder)
    citing, _, entry = doc.all_paragraphs()
    (link,) = citing.content
    assert isinstance(link, ir.Hyperlink)
    first = entry.content[0]
    assert isinstance(first, ir.BookmarkStart)
    assert link.anchor == first.name


def test_printbibliography_options(folder: Path) -> None:
    doc = compiled(
        PREAMBLE + '\\cite{knuth1984}\n\n\\printbibliography[title="Works cited", level=2, all]',
        folder,
    )
    body = texts(doc)
    assert body[1] == ("Heading 2", "Works cited")
    assert len(body) == 2 + 4  # every entry, cited or not
    untitled = compiled(PREAMBLE + "\\cite{knuth1984}\n\n\\printbibliography[title=none]", folder)
    assert [style for style, _ in texts(untitled)] == [None, "Bibliography"]


def test_without_printbibliography_citations_are_plain(folder: Path) -> None:
    doc = compiled(PREAMBLE + "\\cite{knuth1984}", folder)
    (paragraph,) = doc.all_paragraphs()
    assert all(isinstance(item, ir.Run) for item in paragraph.content)


def test_numeric_styles(folder: Path) -> None:
    style = (DATA / "styles" / "harvard-cite-them-right.csl").read_text(encoding="utf-8")
    assert 'citation-format="author-date"' in style
    (folder / "numeric.csl").write_text(NUMERIC_CSL, encoding="utf-8")
    doc = compiled(
        "\\bibliography[file=refs.bib, style=numeric]\n\n"
        "\\cite{tibshirani1996} and \\textcite{daganzo2005}\n\n\\printbibliography",
        folder,
    )
    body = texts(doc)
    assert body[0][1] == "[1] and Daganzo [2]"
    assert body[2][1].startswith("[1]\tR. Tibshirani")


def test_custom_style_files(folder: Path) -> None:
    shutil.copy(DATA / "styles" / "harvard-cite-them-right.csl", folder / "house.csl")
    doc = compiled(
        '\\bibliography[file=refs.bib, style="house.csl"]\n\n\\cite{daganzo2005}', folder
    )
    assert texts(doc) == [(None, "(Daganzo, 2005)")]


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("\\cite{x}", "citations need a bibliography"),
        ("\\printbibliography", "\\printbibliography needs \\bibliography in the preamble"),
        (PREAMBLE + "\\cite{daganzo2006}", "no bibliography entry `daganzo2006`"),
        ("\\bibliography[file=missing.bib]", "cannot read `missing.bib`"),
        ("\\bibliography[file=refs.txt]", "bibliography data is .bib (BibTeX) or .json (CSL-JSON)"),
        ("\\bibliography[file=refs.bib, style=apa9]", "unknown citation style `apa9`"),
        ("\\bibliography", "`\\bibliography` needs `file`"),
    ],
)
def test_errors(text: str, message: str, folder: Path) -> None:
    (folder / "refs.txt").write_text("x", encoding="utf-8")
    assert any(message in e for e in errors(text, folder)), errors(text, folder)


def test_unknown_keys_suggest_one(folder: Path) -> None:
    result = compile_source(PREAMBLE + "\\cite{daganzo2006}", folder)
    (diagnostic,) = [d for _, d in result.diagnostics]
    assert diagnostic.label == "did you mean `daganzo2005`?"


def test_html_runs() -> None:
    runs = html_runs(
        'A <i>book</i>, <span style="font-variant:small-caps;">caps</span> https://x.org/a.'
    )
    assert [(type(r).__name__, getattr(r, "text", None)) for r in runs[:4]] == [
        ("Run", "A "),
        ("Run", "book"),
        ("Run", ", "),
        ("Run", "caps"),
    ]
    assert runs[1].props.get("italic") is True  # type: ignore[union-attr]
    assert runs[3].props.get("small-caps") is True  # type: ignore[union-attr]
    link = runs[5]
    assert isinstance(link, ir.Hyperlink)
    assert link.url == "https://x.org/a"


def test_builds_valid_docx(folder: Path) -> None:
    data, result = build(PREAMBLE + "\\cite{tibshirani1996}\n\n\\printbibliography", folder)
    assert data is not None, result.render_diagnostics()
    assert validate_package(data) == {}
    document = etree.fromstring(zipfile.ZipFile(io.BytesIO(data)).read("word/document.xml"))
    assert document.find(f".//{W}hyperlink[@{W}anchor]") is not None


NUMERIC_CSL = """<?xml version="1.0" encoding="utf-8"?>
<style xmlns="http://purl.org/net/xbiblio/csl" class="in-text" version="1.0">
  <info><title>Minimal numeric</title><id>minimal-numeric</id>
    <category citation-format="numeric"/><updated>2026-10-16T00:00:00+00:00</updated></info>
  <citation><sort><key variable="citation-number"/></sort>
    <layout prefix="[" suffix="]" delimiter=", "><text variable="citation-number"/></layout></citation>
  <bibliography second-field-align="flush"><layout suffix=".">
    <text variable="citation-number" prefix="[" suffix="]"/>
    <names variable="author" suffix=", "><name initialize-with=". "/></names>
    <text variable="title" font-style="italic"/></layout></bibliography>
</style>
"""
