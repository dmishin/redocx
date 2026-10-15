"""What the parser needs to know about commands: how each one's body is read.

Language design §3.2 "Argument kinds". Commands not listed here (including user
macros) have markup bodies. Option values are always parsed generically and
interpreted later, by the command's option schema.
"""

from enum import Enum


class ArgKind(Enum):
    MARKUP = "markup"
    """Full markup: paragraphs, lists, inline sugar, commands."""
    STRING = "string"
    """Literal text up to the closing brace; only \\} and \\\\ are escapes."""
    RAW = "raw"
    """Literal text with balanced braces; nothing is an escape."""
    MATH = "math"
    """LaTeX-style math: balanced braces; \\{ and \\} do not count."""
    STYLESHEET = "stylesheet"
    """Style-sheet syntax (design §3.5): balanced braces, parsed by wordgenx.stylesheet."""


BODY_KINDS: dict[str, ArgKind] = {
    "include": ArgKind.STRING,
    "use": ArgKind.STRING,
    "image": ArgKind.STRING,
    "url": ArgKind.STRING,
    "embed-docx": ArgKind.STRING,
    "field": ArgKind.RAW,
    "raw": ArgKind.RAW,
    "m": ArgKind.MATH,
    "math": ArgKind.MATH,
    "styles": ArgKind.STYLESHEET,
}

SPECIAL_FORMS = frozenset({"define", "let"})
"""Declarations with a name group before the body.

\\define{name}[params]{body} and \\let{name}{value} are the only commands with two
brace groups (an exception to the one-body rule, design §3.18).
"""

BREAK_COMMANDS = frozenset({"br", "tab", "pagebreak", "columnbreak"})
"""Commands that trim adjacent horizontal whitespace (design §3.2, F-1)."""


def body_kind(name: str) -> ArgKind:
    return BODY_KINDS.get(name, ArgKind.MARKUP)
