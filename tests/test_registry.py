import pytest

from wordgenx.ooxml.parts.theme import Theme
from wordgenx.props import (
    REGISTRY,
    PropertySet,
    Target,
    UnknownPropertyError,
    para_props,
    run_props,
)
from wordgenx.values import InvalidValueError, Length


def test_unknown_property_suggests_nearest_name() -> None:
    with pytest.raises(UnknownPropertyError, match="did you mean `space-before`"):
        PropertySet(Target.PARAGRAPH, {"space-befor": "12pt"})


def test_run_property_is_not_a_paragraph_property() -> None:
    with pytest.raises(UnknownPropertyError, match="unknown paragraph property `bold`"):
        para_props(bold=True)


def test_values_are_coerced_and_validated() -> None:
    props = run_props(size="10.5pt", bold="true", underline="dotted-heavy")
    assert props["size"] == Length.pt("10.5")
    assert props["bold"] is True
    assert props["underline"] == "dottedHeavy"
    with pytest.raises(InvalidValueError, match="underline: expected one of"):
        run_props(underline="squiggly")
    with pytest.raises(InvalidValueError, match="outline level"):
        para_props(outline_level=10)


def test_first_line_and_hanging_indent_are_exclusive() -> None:
    with pytest.raises(InvalidValueError, match="mutually exclusive"):
        para_props(first_line_indent="1cm", hanging_indent="1cm")


def test_iteration_follows_registry_order() -> None:
    props = run_props(size="12pt", bold=True, font="Arial")
    assert list(props) == ["font", "bold", "size"]


def test_equality_and_merge() -> None:
    a = run_props(bold=True, size="12pt")
    assert a == run_props(size="12pt", bold=True)
    merged = a.merged(run_props(size="14pt", italic=True))
    assert merged == run_props(bold=True, size="14pt", italic=True)


def test_emit_merges_attributes_of_shared_elements() -> None:
    props = para_props(space_before="12pt", space_after="6pt", line_spacing=1.5)
    assert props.emit(Theme()) == {
        "spacing": {"before": "240", "after": "120", "line": "360", "lineRule": "auto"}
    }


def test_theme_color_emits_rgb_fallback() -> None:
    theme = Theme()
    emitted = run_props(color="theme.text1").emit(theme)
    assert emitted == {"color": {"val": theme.colors["dk1"], "themeColor": "text1"}}


def test_false_toggle_is_explicit() -> None:
    assert run_props(bold=False).emit(Theme()) == {"b": {"val": "0"}, "bCs": {"val": "0"}}


def test_every_property_is_documented() -> None:
    for target in Target:
        for spec in REGISTRY[target].values():
            assert spec.doc, spec.name
            assert spec.values, spec.name
            if not spec.name.startswith("ooxml."):  # ooxml.pPr names the element
                assert spec.name == spec.name.lower()
            assert " " not in spec.name
