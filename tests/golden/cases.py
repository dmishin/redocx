"""Golden-test cases: name -> document builder.

Until the language exists, cases are written against the Python API. Each case
is snapshotted, schema-validated and run through the Open XML SDK validator.
"""

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from wordgenx import ir
from wordgenx.compiler import compile_file
from wordgenx.props import para_props, run_props
from wordgenx.stylesheet import compile_stylesheets, parse_stylesheet
from wordgenx.syntax import Source
from wordgenx.values import Length, LineSpacing


def minimal() -> ir.Document:
    return ir.Document(body=(ir.paragraph("Hello, wordgenx."),))


def styled() -> ir.Document:
    """Custom and built-in styles, document defaults, direct formatting."""
    styles = (
        ir.paragraph_style("Normal", paragraph=para_props(space_after="6pt", line_spacing=1.15)),
        ir.paragraph_style(
            "Heading 1",
            next="Body Text",
            paragraph=para_props(
                keep_with_next=True,
                keep_lines=True,
                space_before="18pt",
                space_after="6pt",
                outline_level=1,
            ),
            run=run_props(font="theme.major", size="16pt", color="theme.accent1", bold=True),
        ),
        ir.paragraph_style(
            "Body Text",
            paragraph=para_props(align="justify", first_line_indent="0.5cm", widow_control=True),
        ),
        ir.paragraph_style(
            "Code Block",
            paragraph=para_props(
                indent_left="0.5cm",
                line_spacing=LineSpacing.exact(Length.pt(12)),
                contextual_spacing=True,
            ),
            run=run_props(font="Consolas", size="9.5pt"),
        ),
        ir.character_style("Term", italic=True, color="theme.accent2"),
        ir.character_style("Strong", bold=True),
    )
    body = (
        ir.paragraph("Styled document", style="Heading 1"),
        ir.paragraph(
            "Body text with a ",
            ir.run("defined term", style="Term"),
            ", ",
            ir.run("strong words", style="Strong"),
            " and ",
            ir.run("direct formatting", underline="double", highlight="light-gray"),
            ".",
            style="Body Text",
        ),
        ir.paragraph(
            "H",
            ir.run("2", vertical_align="subscript"),
            "O and E = mc",
            ir.run("2", vertical_align="superscript"),
            ". ",
            ir.run("Small caps", small_caps=True),
            ", ",
            ir.run("struck", strike=True),
            ", ",
            ir.run("spaced", character_spacing="2pt"),
            ", ",
            ir.run("red", color="#C00000"),
            ", ",
            ir.run("auto", color="auto"),
            ".",
            style="Body Text",
        ),
        ir.paragraph("def hello():\n    print('hi')", style="Code Block"),
        ir.paragraph("Left\tright after a tab.", align="right", hanging_indent="1cm"),
        ir.paragraph(ir.run("Zweisprachig", lang="de-DE"), page_break_before=True),
    )
    return ir.Document(
        body=body,
        styles=styles,
        default_paragraph=para_props(space_after="8pt"),
        meta=ir.Meta(language="en-GB"),
    )


def metadata() -> ir.Document:
    """Core properties, settings and a landscape Letter page."""
    return ir.Document(
        body=(ir.paragraph('Metadata & settings: <escaped> "text".'),),
        meta=ir.Meta(
            title="Q3 Report & Outlook",
            subject="Operations",
            author="Finance Team",
            keywords=("report", "quarterly"),
            description="Golden test for document properties.",
            last_modified_by="wordgenx tests",
            created=datetime(2026, 10, 1, 9, 30, tzinfo=UTC),
            modified=datetime(2026, 10, 15, 17, 0, tzinfo=UTC),
            language="en-US",
        ),
        settings=ir.Settings(
            default_tab_stop=Length.cm(1), update_fields_on_open=True, even_and_odd_headers=True
        ),
        page=ir.PageSetup(
            width=Length.inch(11),
            height=Length.inch("8.5"),
            margin_top=Length.inch(1),
            margin_bottom=Length.inch(1),
            margin_left=Length.inch("0.75"),
            margin_right=Length.inch("0.75"),
        ),
    )


def stylesheet() -> ir.Document:
    """examples/styles/house.wgxs: defaults, styles, linked style, heading and list numbering."""
    path = Path(__file__).resolve().parents[2] / "examples" / "styles" / "house.wgxs"
    compiled = compile_stylesheets([parse_stylesheet(Source.from_path(path))])
    assert compiled.ok, compiled.render_diagnostics()
    body = (
        ir.paragraph("Introduction", style="Heading 1"),
        ir.paragraph(
            "Body text with a ",
            ir.run("term", style="Term"),
            " and ",
            ir.run("strong words", style="Strong"),
            ".",
            style="Body Text",
        ),
        ir.paragraph("Background", style="Heading 2"),
        ir.paragraph("A quotation in a linked style.", style="Quote"),
        ir.paragraph("Design", style="Heading 1"),
        ir.paragraph("print('hello')", style="Code Block"),
    )
    return compiled.apply(ir.Document(body=body, meta=ir.Meta(language="en-GB")))


def example_hello() -> ir.Document:
    """examples/hello.wgx through the full pipeline: parse, style sheets, macros, lowering."""
    result = compile_file(Path(__file__).resolve().parents[2] / "examples" / "hello.wgx")
    assert result.document is not None, result.render_diagnostics()
    return result.document


CASES: dict[str, Callable[[], ir.Document]] = {
    "example-hello": example_hello,
    "minimal": minimal,
    "styled": styled,
    "metadata": metadata,
    "stylesheet": stylesheet,
}
