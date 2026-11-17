"""The command binder: options read against a command spec."""

from decimal import Decimal

from redocx.commands.bind import Args, Problem, bind
from redocx.commands.builtin import CODE_BLOCK, COMMANDS
from redocx.commands.spec import CommandSpec, OptionSpec, Placement
from redocx.commands.types import Bool, ColumnWidth, Int, Levels, Percent, Text
from redocx.props import Target
from redocx.syntax import ast, parse
from redocx.syntax.diagnostics import Code, Severity
from redocx.values import Length, TabStop


def command(text: str) -> ast.Command:
    result = parse(text)
    assert not result.diagnostics, result.diagnostics
    (paragraph,) = result.ast.blocks
    assert isinstance(paragraph, ast.Paragraph)
    (node,) = [c for c in paragraph.content if isinstance(c, ast.Command)]
    return node


def bound(text: str) -> tuple[Args, list[Problem]]:
    node = command(text)
    return bind(node, COMMANDS[node.name])


def ok(text: str) -> Args:
    args, problems = bound(text)
    assert not problems, [p.message for p in problems]
    return args


def messages(text: str) -> list[str]:
    return [p.message for p in bound(text)[1]]


def test_bool_options_take_the_flag_and_the_key_form() -> None:
    assert ok(r"\section[mirror]").flag("mirror")
    assert ok(r"\section[mirror=on]").flag("mirror")
    assert not ok(r"\section[mirror=off]").flag("mirror")
    assert ok(r"\settings[update-fields-on-open]").flag("update-fields-on-open")
    assert ok(r"\settings[even-odd-headers=yes]").flag("even-odd-headers")


def test_a_misspelt_bool_is_an_error() -> None:
    args, problems = bound(r"\section[link-headers=flase]")
    assert "link-headers" not in args
    assert [p.message for p in problems] == ["`link-headers`: expected on or off, got `flase`"]
    assert problems[0].label == "did you mean `false`?"


def test_unknown_options_are_errors_with_a_suggestion() -> None:
    _, problems = bound(r"\section[orientaton=landscape]")
    (problem,) = problems
    assert problem.code is Code.INVALID_ARGUMENT
    assert problem.severity is Severity.ERROR
    assert problem.message == "unknown option `orientaton` for `\\section`"
    assert problem.label == "did you mean `orientation`?"
    assert problem.help is not None
    assert problem.help.startswith("\\section[size=...")


def test_planned_options_are_warnings() -> None:
    spec = CommandSpec("x", Placement.BLOCK, "", body=None, planned_options={"later": "M9"})
    (problem,) = bind(command(r"\x[later]"), spec)[1]
    assert problem.code is Code.NOT_SUPPORTED_YET
    assert problem.severity is Severity.WARNING


def test_repeated_options_are_errors() -> None:
    assert messages(r"\image[width=2cm, width=3cm]{a.png}") == ["`width` is given twice"]


def test_positional_value_and_flags() -> None:
    args = ok(r"\list[Legal, continue, restart=5]{x}")
    assert args.text("numbering") == "Legal"
    assert args.flag("continue")
    assert args.number("restart") == 5
    assert ok(r"\list[numbering=Legal]{x}").text("numbering") == "Legal"
    assert messages(r"\list[A, B]{x}") == ["unknown option `B` for `\\list`"]


def test_required_options() -> None:
    assert messages(r"\list{x}") == ["`\\list` needs `numbering`"]
    assert messages(r"\link{x}") == ["`\\link` needs `to`"]


def test_variadic_positional() -> None:
    stops = ok(r"\tabs[(8cm, center), (16cm, right, leader=dot)]{x}").items("stops", TabStop)
    assert [stop.position for stop in stops] == [Length.cm(8), Length.cm(16)]


def test_records_with_a_shorthand() -> None:
    columns = ok(r"\section[columns=2]").record("columns")
    assert columns is not None
    assert columns.number("count") == 2
    columns = ok(r"\section[columns=(count=3, gap=1cm, separator)]").record("columns")
    assert columns is not None
    assert columns.number("count") == 3
    assert columns.get("gap", Length) == Length.cm(1)
    assert columns.flag("separator")
    margins = ok(r"\section[margins=2cm]").record("margins")
    assert margins is not None
    assert margins.get("all", Length) == Length.cm(2)
    assert messages(r"\section[columns=(cuont=2)]") == ["`columns`: unknown field `cuont`"]
    assert messages(r"\section[columns=46]") == [
        "`columns`: expected a whole number from 1 to 45, got 46"
    ]


def test_records_with_required_fields() -> None:
    assert messages(r"\settings[protection=(enforce)]") == ["`protection`: `mode` is missing"]
    protection = ok(r"\settings[protection=read-only]").record("protection")
    assert protection is not None
    assert protection.text("mode") == "read-only"


def test_toggle() -> None:
    assert ok(r"\section[line-numbers=off]").values["line-numbers"] is None
    on = ok(r"\section[line-numbers]").record("line-numbers")
    assert on is not None
    assert not on.values
    numbers = ok(r"\section[line-numbers=(count-by=5)]").record("line-numbers")
    assert numbers is not None
    assert numbers.number("count-by") == 5


def test_choices_map_to_their_values() -> None:
    notes = ok(r"\settings[footnotes=(format=symbols, restart=each-page)]").record("footnotes")
    assert notes is not None
    assert notes.values == {"format": "chicago", "restart": "eachPage"}
    assert messages(r"\settings[endnotes=(restart=each-page)]") == [
        "`endnotes`: unknown value `each-page`"
    ]
    assert ok(r"\cell[valign=middle]{x}").text("valign") == "center"


def test_formatting_properties_as_options() -> None:
    args = ok(r"\p[style=Note, center, keep-with-next, space-before=6pt]{x}")
    assert args.text("style") == "Note"
    assert args.props is not None
    assert dict(args.props) == {
        "keep-with-next": True,
        "space-before": Length.pt(6),
        "align": "center",
    }
    assert messages(r"\p[space-befor=6pt]{x}") == ["unknown option `space-befor` for `\\p`"]
    assert bound(r"\p[space-befor=6pt]{x}")[1][0].label == "did you mean `space-before`?"
    font = ok(r"\font[bold, size=14pt]{x}").props
    assert font is not None
    assert font.target is Target.RUN
    assert font.get("bold") is True


def test_pattern_options() -> None:
    args = ok(r'\meta[custom.count=3, custom.client="ACME", custom.ok=true]')
    assert {k: v for k, (v, _) in args.patterns.items()} == {
        "custom.count": 3,
        "custom.client": "ACME",
        "custom.ok": True,
    }


def test_value_types() -> None:
    image = ok(r"\image[width=50%, height=2cm]{a.png}")
    assert image.get("width", Percent) == Percent(Decimal(50))
    assert image.get("height", Length) == Length.cm(2)
    widths = ok(r"\table[widths=(4cm, 25%, 2fr, auto)]{| a |}").items("widths", ColumnWidth)
    assert [w.kind for w in widths] == ["length", "percent", "fraction", "fraction"]
    assert ok(r"\toc[levels=3-1]").values["levels"] == Levels(1, 3)
    assert ok(r"\seq[Req, restart=heading-2]").text("restart") == "\\s 2"
    assert ok(r"\table[look=none]{| a |}").values["look"] == frozenset()
    assert ok(r"\meta[keywords=(a, b)]").values["keywords"] == ("a", "b")
    assert ok(r'\meta[keywords="a, b"]').values["keywords"] == ("a", "b")


def test_bodies() -> None:
    assert messages(r"\section{x}") == ["`\\section` takes no body"]
    assert messages(r"\url{}") == ["`\\url` needs an address"]
    assert messages(r"\b") == ["`\\b` needs a body"]
    assert messages(r"\footnote{}") == []


def test_a_spec_of_its_own() -> None:
    spec = CommandSpec(
        "demo",
        Placement.INLINE,
        "",
        (OptionSpec("n", Int(1, 3)), OptionSpec("on", Bool()), OptionSpec("t", Text())),
        positional="t",
    )
    args, problems = bind(command(r"\demo[hello, on, n=2]{x}"), spec)
    assert not problems
    assert (args.text("t"), args.flag("on"), args.number("n")) == ("hello", True, 2)
    assert args.span_of("n") != args.span


def test_code_block_attributes() -> None:
    node = ast.Command("code block", None, None, span=command(r"\b{x}").span)
    assert bind(node, CODE_BLOCK)[1] == []
