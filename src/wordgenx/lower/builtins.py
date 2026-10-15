"""Styles and list definitions that markup relies on, created when a document uses them
and its style sheets do not define them.

They follow Word's own built-ins (names, kinds, roles) with neutral formatting, so a
document that uses ``= Heading`` or ``*strong*`` is idiomatic Word even without a
style sheet. Style sheets and templates override all of them.
"""

from __future__ import annotations

from collections.abc import Callable

from wordgenx.ir import Numbering, NumberingLevel, Style, StyleKind
from wordgenx.props import para_props, run_props

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
    "Table Grid": lambda: Style(StyleKind.TABLE, "Table Grid"),
    "Strong": _character("Strong", bold=True),
    "Emphasis": _character("Emphasis", italic=True),
    "Code": _character("Code", font="Consolas"),
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
