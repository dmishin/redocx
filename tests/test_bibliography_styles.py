"""The bundled CSL styles render a fixed set of entries as the style guides want them.

The entries include what citeproc-py gets wrong on its own (bibliography.py works around
it): two papers by one author in one year, a locator on a cited article, a title in quotes
followed by a comma, and full given names in Chicago.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.support.xsd import validate_package
from wordgenx.bibliography import BUNDLED_STYLES, Bibliography, Cite, load_sources, load_style
from wordgenx.compiler import build

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
@inproceedings{vaswani2017,
  author = {Vaswani, Ashish and Shazeer, Noam and Parmar, Niki},
  title = {Attention Is All You Need}, booktitle = {Advances in Neural Information Processing Systems},
  pages = {5998--6008}, year = {2017}, publisher = {Curran Associates}
}
@book{ortuzar2011,
  author = {Ort{\'u}zar, Juan de Dios and Willumsen, Luis G.}, title = {Modelling Transport},
  edition = {4}, publisher = {Wiley}, address = {Chichester}, year = {2011}
}
@article{smith2020b,
  author = {Smith, Jane}, title = {Second Paper}, journal = {Journal B}, volume = {2}, pages = {11--20}, year = {2020}
}
@article{smith2020a,
  author = {Smith, Jane}, title = {First Paper}, journal = {Journal A}, volume = {1}, pages = {1--10}, year = {2020}
}
"""
CITES = [
    Cite(("tibshirani1996",), locator=("page", "270")),
    Cite(("smith2020a",)),
    Cite(("smith2020b",)),
    Cite(("vaswani2017", "daganzo2005")),
    Cite(("ortuzar2011",), narrative=True),
]
DOI = "https://doi.org/10.1111/j.2517-6161.1996.tb02080.x"
JRSS = "<i>Journal of the Royal Statistical Society: Series B</i>"
NIPS = "<i>Advances in Neural Information Processing Systems</i>"
DAGANZO = "<i>Fundamentals of Transportation and Traffic Operations</i>"

EXPECTED: dict[str, tuple[list[str], list[str]]] = {
    "apa": (
        [
            "(Tibshirani, 1996, p. 270)",
            "(Smith, 2020a)",
            "(Smith, 2020b)",
            "(Daganzo, 2005; Vaswani et al., 2017)",
            "Ort\u00fazar and Willumsen (2011)",
        ],
        [
            f"Daganzo, C. F. (2005). {DAGANZO}. Pergamon.",
            "Ort\u00fazar, J. de D., &amp; Willumsen, L. G. (2011). <i>Modelling Transport</i>"
            " (4th ed.). Wiley.",
            "Smith, J. (2020a). First Paper. <i>Journal A</i>, <i>1</i>, 1\u201310.",
            "Smith, J. (2020b). Second Paper. <i>Journal B</i>, <i>2</i>, 11\u201320.",
            f"Tibshirani, R. (1996). Regression Shrinkage and Selection via the Lasso. {JRSS},"
            f" <i>58</i>(1), 267\u2013288. {DOI}",
            f"Vaswani, A., Shazeer, N., &amp; Parmar, N. (2017). Attention Is All You Need."
            f" {NIPS}, 5998\u20136008.",
        ],
    ),
    "ieee": (
        ["[1, p. 270]", "[2]", "[3]", "[4], [5]", "Ort\u00fazar and Willumsen [6]"],
        [
            f"[1]R. Tibshirani, \u201cRegression Shrinkage and Selection via the Lasso,\u201d"
            f" {JRSS}, vol. 58, no. 1, pp. 267\u2013288, 1996,"
            " doi: 10.1111/j.2517-6161.1996.tb02080.x.",
            "[2]J. Smith, \u201cFirst Paper,\u201d <i>Journal A</i>, vol. 1, pp. 1\u201310, 2020.",
            "[3]J. Smith, \u201cSecond Paper,\u201d <i>Journal B</i>, vol. 2, pp. 11\u201320,"
            " 2020.",
            f"[4]A. Vaswani, N. Shazeer, and N. Parmar, \u201cAttention Is All You Need,\u201d"
            f" in {NIPS}, Curran Associates, 2017, pp. 5998\u20136008.",
            f"[5]C. F. Daganzo, {DAGANZO}. Oxford: Pergamon, 2005.",
            "[6]J. de D. Ort\u00fazar and L. G. Willumsen, <i>Modelling Transport</i>, 4th ed."
            " Chichester: Wiley, 2011.",
        ],
    ),
    "chicago": (
        [
            "(Tibshirani 1996, 270)",
            "(Smith 2020a)",
            "(Smith 2020b)",
            "(Daganzo 2005; Vaswani et al. 2017)",
            "Ort\u00fazar and Willumsen (2011)",
        ],
        [
            f"Daganzo, Carlos F. 2005. {DAGANZO}. Pergamon.",
            "Ort\u00fazar, Juan de Dios, and Luis G. Willumsen. 2011. <i>Modelling Transport</i>."
            " 4th ed. Wiley.",
            "Smith, Jane. 2020a. \u201cFirst Paper.\u201d <i>Journal A</i> 1: 1\u201310.",
            "Smith, Jane. 2020b. \u201cSecond Paper.\u201d <i>Journal B</i> 2: 11\u201320.",
            f"Tibshirani, Robert. 1996. \u201cRegression Shrinkage and Selection via the"
            f" Lasso.\u201d {JRSS} 58 (1): 267\u201388. {DOI}.",
            f"Vaswani, Ashish, Noam Shazeer, and Niki Parmar. 2017. \u201cAttention Is All You"
            f" Need.\u201d {NIPS}, 5998\u20136008.",
        ],
    ),
}


@pytest.fixture
def bib(tmp_path: Path) -> Path:
    path = tmp_path / "refs.bib"
    path.write_text(BIBTEX, encoding="utf-8")
    return path


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_style_renders_as_its_guide_says(name: str, bib: Path) -> None:
    bibliography = Bibliography(load_sources([bib]), load_style(name, bib.parent, "en-US"))
    bibliography.cites = list(CITES)
    citations, entries = EXPECTED[name]
    assert bibliography.render() == citations
    assert [html for _, html in bibliography.entries()] == entries


def test_aliases_name_the_same_file() -> None:
    assert BUNDLED_STYLES["chicago"] == BUNDLED_STYLES["chicago-author-date"]
    for path in BUNDLED_STYLES.values():
        assert path.is_file(), path


def test_year_suffix_only_when_needed(bib: Path) -> None:
    bibliography = Bibliography(load_sources([bib]), load_style("apa", bib.parent, "en-US"))
    bibliography.cites = [Cite(("smith2020a",), narrative=True)]
    assert bibliography.render() == ["Smith (2020)"]


def test_textcite_carries_the_suffix(bib: Path) -> None:
    bibliography = Bibliography(load_sources([bib]), load_style("apa", bib.parent, "en-US"))
    bibliography.cites = [Cite(("smith2020b",), narrative=True), Cite(("smith2020a",))]
    assert bibliography.render() == ["Smith (2020b)", "(Smith, 2020a)"]


@pytest.mark.parametrize("name", [*sorted(EXPECTED), "harvard"])
def test_style_builds_valid_docx(name: str, bib: Path) -> None:
    text = (
        f"\\document[lang=en-US]\n\\bibliography[file=refs.bib, style={name}]\n\n"
        "As \\textcite{tibshirani1996} and \\cite[page=3]{smith2020a, smith2020b} show.\n\n"
        "\\printbibliography[all]\n"
    )
    data, result = build(text, bib.parent)
    assert data is not None, result.render_diagnostics()
    assert not result.diagnostics, result.render_diagnostics()
    assert validate_package(data) == {}
