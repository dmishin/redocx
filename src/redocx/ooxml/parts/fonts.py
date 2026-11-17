"""The font table part (word/fontTable.xml).

Lists the fonts the document refers to. Word fills in panose and charset data
itself; names are enough for it to resolve and substitute fonts.
"""

from __future__ import annotations

from collections.abc import Iterable

from redocx.ooxml.xml import E, Element, nsmap


def font_table_xml(font_names: Iterable[str]) -> Element:
    return E(
        "w:fonts",
        *(E("w:font", {"w:name": name}) for name in sorted(set(font_names))),
        nsmap=nsmap("w"),
    )
