"""Word's built-in style names and style IDs.

Word stores some built-in style names in lower case ("heading 1", "caption") and
shows them localized and capitalized in the UI. Users write the display form
("Heading 1"); we store what Word stores, so the style is recognised as the
built-in one (and localized) rather than as a look-alike custom style.
"""

from __future__ import annotations

import re

# Built-in names Word stores in lower case.
_LOWERCASE_BUILTINS = {
    *(f"heading {i}" for i in range(1, 10)),
    *(f"toc {i}" for i in range(1, 10)),
    *(f"index {i}" for i in range(1, 10)),
    "caption",
    "header",
    "footer",
    "footnote text",
    "footnote reference",
    "endnote text",
    "endnote reference",
    "annotation text",
    "annotation reference",
    "table of figures",
    "index heading",
}

# Other built-in names, stored as written.
_OTHER_BUILTINS = {
    "Normal",
    "Title",
    "Subtitle",
    "Quote",
    "Intense Quote",
    "Strong",
    "Emphasis",
    "Subtle Emphasis",
    "Intense Emphasis",
    "Subtle Reference",
    "Intense Reference",
    "Book Title",
    "No Spacing",
    "List Paragraph",
    "TOC Heading",
    "Hyperlink",
    "FollowedHyperlink",
    "Default Paragraph Font",
    "Normal Table",
    "No List",
    "Table Grid",
    "Body Text",
    "Plain Text",
    "Block Text",
    "Bibliography",
}

# Style IDs that are not simply the name with spaces removed.
_SPECIAL_IDS = {
    "Normal Table": "TableNormal",
    "table of figures": "TableofFigures",
    "annotation text": "CommentText",
    "annotation reference": "CommentReference",
    **{f"toc {i}": f"TOC{i}" for i in range(1, 10)},
}

# Gallery priorities Word assigns to common built-ins.
UI_PRIORITY = {
    "Normal": 0,
    **{f"heading {i}": 9 for i in range(1, 10)},
    "Title": 10,
    "Subtitle": 11,
    "Subtle Emphasis": 19,
    "Emphasis": 20,
    "Intense Emphasis": 21,
    "Strong": 22,
    "Quote": 29,
    "Intense Quote": 30,
    "Subtle Reference": 31,
    "Intense Reference": 32,
    "Book Title": 33,
    "List Paragraph": 34,
    "caption": 35,
    "Bibliography": 37,
    "TOC Heading": 39,
    "Table Grid": 39,
    **{f"toc {i}": 39 for i in range(1, 10)},
    "No Spacing": 1,
    "Default Paragraph Font": 1,
    "Normal Table": 99,
    "No List": 99,
    "Hyperlink": 99,
}


def stored_name(name: str) -> str:
    """The name as Word stores it in styles.xml."""
    if name.lower() in _LOWERCASE_BUILTINS:
        return name.lower()
    for builtin in _OTHER_BUILTINS:
        if builtin.lower() == name.lower():
            return builtin
    return name


def is_builtin(name: str) -> bool:
    stored = stored_name(name)
    return stored in _LOWERCASE_BUILTINS or stored in _OTHER_BUILTINS


def style_id(name: str) -> str:
    """The w:styleId Word uses for a style name: ``Heading 1`` -> ``Heading1``."""
    stored = stored_name(name)
    if stored in _SPECIAL_IDS:
        return _SPECIAL_IDS[stored]
    words = re.split(r"\s+", stored.strip())
    joined = "".join(w[:1].upper() + w[1:] for w in words) if is_builtin(stored) else "".join(words)
    return "".join(ch for ch in joined if ch.isalnum())
