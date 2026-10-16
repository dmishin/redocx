"""The document settings part (word/settings.xml)."""

from __future__ import annotations

from wordgenx.ir import NOTE_KINDS, PROTECTION_MODES, Document, Math, NoteSettings
from wordgenx.math.omml import math_properties
from wordgenx.ooxml.schema_order import SETTINGS, ordered
from wordgenx.ooxml.xml import E, Element, nsmap

WORD_URI = "http://schemas.microsoft.com/office/word"

# compatibilityMode 15 = Word 2013 and later layout. The other settings are the ones
# Word 2016+ writes into every new document.
_COMPAT_SETTINGS = (
    ("compatibilityMode", "15"),
    ("overrideTableStyleFontSizeAndJustification", "1"),
    ("enableOpenTypeFeatures", "1"),
    ("doNotFlipMirrorIndents", "1"),
    ("differentiateMultirowTableHeaders", "1"),
)

_COLOR_SCHEME_MAPPING = {
    "w:bg1": "light1",
    "w:t1": "dark1",
    "w:bg2": "light2",
    "w:t2": "dark2",
    **{f"w:accent{i}": f"accent{i}" for i in range(1, 7)},
    "w:hyperlink": "hyperlink",
    "w:followedHyperlink": "followedHyperlink",
}


def note_properties(notes: NoteSettings) -> list[Element]:
    """CT_FtnProps / CT_EdnProps content: position, number format, start, restart."""
    out: list[Element] = []
    if notes.position is not None:
        out.append(E("w:pos", {"w:val": notes.position}))
    if notes.format is not None:
        out.append(E("w:numFmt", {"w:val": notes.format}))
    if notes.start is not None:
        out.append(E("w:numStart", {"w:val": str(notes.start)}))
    if notes.restart is not None:
        out.append(E("w:numRestart", {"w:val": notes.restart}))
    return out


def settings_xml(document: Document) -> Element:
    settings = document.settings
    language = document.meta.language or "en-US"
    children: dict[str, Element] = {
        "zoom": E("w:zoom", {"w:percent": "100"}),
        "defaultTabStop": E("w:defaultTabStop", {"w:val": str(settings.default_tab_stop.in_twips)}),
        "characterSpacingControl": E("w:characterSpacingControl", {"w:val": "doNotCompress"}),
        "compat": E(
            "w:compat",
            *(
                E("w:compatSetting", {"w:name": name, "w:uri": WORD_URI, "w:val": value})
                for name, value in _COMPAT_SETTINGS
            ),
        ),
        "themeFontLang": E("w:themeFontLang", {"w:val": language}),
        "clrSchemeMapping": E("w:clrSchemeMapping", _COLOR_SCHEME_MAPPING),
    }
    if settings.even_and_odd_headers:
        children["evenAndOddHeaders"] = E("w:evenAndOddHeaders")
    if settings.update_fields_on_open:
        children["updateFields"] = E("w:updateFields", {"w:val": "true"})
    if settings.mirror_margins:
        children["mirrorMargins"] = E("w:mirrorMargins")
    if any(isinstance(i, Math) for p in document.all_paragraphs() for i in p.content):
        children["mathPr"] = math_properties()
    if settings.protection is not None:
        children["documentProtection"] = E(
            "w:documentProtection",
            {
                "w:edit": PROTECTION_MODES[settings.protection],
                "w:enforcement": "1" if settings.protection_enforced else "0",
            },
        )
    for kind in NOTE_KINDS:
        properties = note_properties(settings.notes(kind))
        if document.notes(kind):  # the separator lines, as Word writes them
            properties += [E(f"w:{kind}", {"w:id": str(i)}) for i in (-1, 0)]
        if properties:
            children[f"{kind}Pr"] = E(f"w:{kind}Pr", *properties)
    return E("w:settings", *ordered(children, SETTINGS), nsmap=nsmap("w"))
