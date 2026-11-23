"""Smart quotes by language and `\\document[quotes=]` (design §3.2)."""

import pytest

from redocx import ir
from redocx.compiler import compile_source


def texts(source: str) -> list[str]:
    result = compile_source(source)
    assert not result.diagnostics, result.render_diagnostics()
    assert result.document is not None
    return [p.text() for p in ir.iter_paragraphs(result.document.body)]


@pytest.mark.parametrize(
    ("lang", "expected"),
    [
        ("en-US", "“a ‘b’ c” don’t"),
        ("de-DE", "„a ‚b‘ c“ don’t"),
        ("fr-FR", "«a “b” c» don’t"),  # no space is added inside « »
        ("ru-RU", "«a „b“ c» don’t"),
        ("ja-JP", "“a ‘b’ c” don’t"),  # input methods type their own marks
        ("xx", "“a ‘b’ c” don’t"),
    ],
)
def test_quotes_follow_the_document_language(lang: str, expected: str) -> None:
    assert texts(f"\\document[lang={lang}]\n\n\"a 'b' c\" don't") == [expected]


def test_quotes_follow_the_language_of_the_text() -> None:
    assert texts('"a" \\lang[de-DE]{"b"} "c"') == ["“a” „b“ “c”"]


def test_quotes_option_sets_the_quotes_for_every_language() -> None:
    four = '\\document[quotes="«»„“"]\n\n"a \'b\'" \\lang[de-DE]{"c"}'
    assert texts(four) == ["«a „b“» «c»"]
    two = '\\document[lang=de-DE, quotes="»«"]\n\n"a \'b\'"'
    assert texts(two) == ["»a ‚b‘«"]


def test_quotes_option_needs_two_or_four_characters() -> None:
    result = compile_source('\\document[quotes="«»„"]\n\nx')
    assert [d.message for _, d in result.diagnostics] == [
        "`quotes` takes two or four characters, got 3"
    ]


def test_smart_quotes_off_keeps_straight_quotes_in_any_language() -> None:
    assert texts("\\document[lang=ru-RU, smart-quotes=off]\n\n\"a 'b'\"") == ["\"a 'b'\""]
