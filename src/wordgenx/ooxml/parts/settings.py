"""The document settings part (word/settings.xml)."""

from __future__ import annotations

from wordgenx.ir import Document
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
    return E("w:settings", *ordered(children, SETTINGS), nsmap=nsmap("w"))
