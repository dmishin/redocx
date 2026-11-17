"""Styles and list definitions that markup relies on, created when a document uses them
and its style sheets do not define them.

They follow Word's own built-ins (names, kinds, roles) with neutral formatting, so a
document that uses ``= Heading`` or ``*strong*`` is idiomatic Word even without a
style sheet. Style sheets and templates override all of them.
"""

from __future__ import annotations

from collections.abc import Callable

from redocx.ir import Numbering, NumberingLevel, Style, StyleKind
from redocx.props import para_props, run_props, table_props

_HEADING_SIZES = {1: "16pt", 2: "13pt", 3: "12pt"}


def _heading(level: int) -> Style:
    run: dict[str, object] = {
        "font": "theme.major",
        "size": _HEADING_SIZES.get(level, "11pt"),
        "color": "theme.accent1",
    }
    if level >= 4:
        run["italic"] = True
    return Style(
        StyleKind.PARAGRAPH,
        f"Heading {level}",
        based_on="Normal",
        next="Normal",
        paragraph=para_props(
            keep_with_next=True,
            keep_lines=True,
            space_before="12pt" if level == 1 else "6pt",
            space_after="3pt",
            outline_level=level,
        ),
        run=run_props(**run),
    )


def _toc(level: int) -> Style:
    """Word's TOC n: each level indented 11pt more; the page-number tab stop is added
    for the page size when the style is used."""
    return Style(
        StyleKind.PARAGRAPH,
        f"TOC {level}",
        based_on="Normal",
        next="Normal",
        paragraph=para_props(space_after="5pt", indent_left=f"{11 * (level - 1)}pt"),
    )


def _index(level: int) -> Style:
    """Word's index n: a hanging indent of 12pt, each level 12pt further in."""
    return Style(
        StyleKind.PARAGRAPH,
        f"index {level}",
        based_on="Normal",
        next="Normal",
        paragraph=para_props(
            space_after="0pt",
            line_spacing=1,
            indent_left=f"{12 * level}pt",
            hanging_indent="12pt",
        ),
    )


def _character(name: str, **props: object) -> Callable[[], Style]:
    return lambda: Style(StyleKind.CHARACTER, name, run=run_props(**props))


BUILTIN_STYLES: dict[str, Callable[[], Style]] = {
    **{f"Heading {i}": (lambda i=i: _heading(i)) for i in range(1, 10)},
    "Title": lambda: Style(
        StyleKind.PARAGRAPH,
        "Title",
        based_on="Normal",
        next="Normal",
        paragraph=para_props(space_after="6pt", contextual_spacing=True),
        run=run_props(font="theme.major", size="28pt"),
    ),
    "Subtitle": lambda: Style(
        StyleKind.PARAGRAPH,
        "Subtitle",
        based_on="Normal",
        next="Normal",
        run=run_props(size="14pt", color="theme.text2"),
    ),
    "List Paragraph": lambda: Style(
        StyleKind.PARAGRAPH,
        "List Paragraph",
        based_on="Normal",
        paragraph=para_props(indent_left="1.27cm", contextual_spacing=True),
    ),
    "Code Block": lambda: Style(
        StyleKind.PARAGRAPH,
        "Code Block",
        based_on="Normal",
        paragraph=para_props(space_after="0pt", line_spacing=1, contextual_spacing=True),
        run=run_props(font="Consolas", size="9.5pt"),
    ),
    "Table Grid": lambda: Style(  # single thin borders around and between all cells
        StyleKind.TABLE,
        "Table Grid",
        paragraph=para_props(space_after="0pt", line_spacing=1),
        table=table_props(borders="(single, 0.5pt, auto)"),
        quick_format=False,
    ),
    "Header": lambda: Style(
        StyleKind.PARAGRAPH,
        "Header",
        based_on="Normal",
        paragraph=para_props(space_after="0pt", line_spacing=1),
    ),
    "Footer": lambda: Style(
        StyleKind.PARAGRAPH,
        "Footer",
        based_on="Normal",
        paragraph=para_props(space_after="0pt", line_spacing=1),
    ),
    "Caption": lambda: Style(
        StyleKind.PARAGRAPH,
        "Caption",
        based_on="Normal",
        next="Normal",
        paragraph=para_props(space_after="10pt", line_spacing=1),
        run=run_props(italic=True, size="9pt", color="theme.text2"),
    ),
    "TOC Heading": lambda: Style(
        StyleKind.PARAGRAPH,
        "TOC Heading",
        based_on="Normal",
        next="Normal",
        paragraph=para_props(keep_with_next=True, space_before="12pt", space_after="6pt"),
        run=run_props(font="theme.major", size="16pt", color="theme.accent1"),
    ),
    **{f"TOC {i}": (lambda i=i: _toc(i)) for i in range(1, 10)},
    **{f"index {i}": (lambda i=i: _index(i)) for i in range(1, 10)},
    "index heading": lambda: Style(
        StyleKind.PARAGRAPH,
        "index heading",
        based_on="Normal",
        next="index 1",
        paragraph=para_props(keep_with_next=True, space_before="6pt", space_after="0pt"),
        run=run_props(font="theme.major", bold=True),
    ),
    "table of figures": lambda: Style(
        StyleKind.PARAGRAPH,
        "table of figures",
        based_on="Normal",
        next="Normal",
        paragraph=para_props(space_after="0pt"),
    ),
    **{
        f"{kind} Text": (
            lambda kind=kind: Style(
                StyleKind.PARAGRAPH,
                f"{kind} Text",
                based_on="Normal",
                paragraph=para_props(space_after="0pt", line_spacing=1),
                run=run_props(size="10pt"),
            )
        )
        for kind in ("Footnote", "Endnote")
    },
    "Footnote Reference": _character("Footnote Reference", vertical_align="superscript"),
    "annotation text": lambda: Style(  # Word's Comment Text
        StyleKind.PARAGRAPH,
        "annotation text",
        based_on="Normal",
        paragraph=para_props(space_after="0pt", line_spacing=1),
        run=run_props(size="10pt"),
    ),
    "annotation reference": _character("annotation reference", size="8pt"),
    "Placeholder Text": _character("Placeholder Text", color="#808080"),
    "Endnote Reference": _character("Endnote Reference", vertical_align="superscript"),
    "Bibliography": lambda: Style(  # entries with a hanging indent
        StyleKind.PARAGRAPH,
        "Bibliography",
        based_on="Normal",
        paragraph=para_props(indent_left="1.27cm", hanging_indent="1.27cm", space_after="6pt"),
    ),
    "Strong": _character("Strong", bold=True),
    "Emphasis": _character("Emphasis", italic=True),
    "Code": _character("Code", font="Consolas"),
    "Code Line Number": lambda: Style(
        StyleKind.CHARACTER, "Code Line Number", run=run_props(color="#8A8F98"), quick_format=False
    ),
    "Hyperlink": _character("Hyperlink", color="theme.hyperlink", underline="single"),
}
"""Style name -> factory. Names are Word's built-in names (Code and Code Block are custom)."""


def _bullets() -> Numbering:
    return Numbering("Bullets", (NumberingLevel(1, "bullet", "•"),))


def _numbers() -> Numbering:
    return Numbering(
        "Numbers",
        (
            NumberingLevel(1, "decimal", "%1."),
            NumberingLevel(2, "lowerLetter", "%2."),
            NumberingLevel(3, "lowerRoman", "%3."),
        ),
    )


DEFAULT_NUMBERING: dict[str, Callable[[], Numbering]] = {
    "Bullets": _bullets,
    "Numbers": _numbers,
}
"""Definitions used by ``-`` and ``1.`` lists unless a style sheet defines these names."""
