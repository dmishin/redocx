"""The theme part (word/theme/theme1.xml).

wordgenx ships its own small default theme instead of copying Office's. It is
complete enough for Word to resolve theme colors and fonts (scheme colors,
major/minor fonts and the three-entry format scheme lists Word requires).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from wordgenx.ooxml.xml import E, Element, nsmap

# Scheme slot order required by CT_ColorScheme.
SCHEME_SLOTS = (
    "dk1 lt1 dk2 lt2 accent1 accent2 accent3 accent4 accent5 accent6 hlink folHlink".split()
)

# ST_ThemeColor (as used in w:color/@w:themeColor) -> scheme slot, per the
# standard color-scheme mapping that settings.xml declares.
THEME_COLOR_SLOTS: dict[str, str] = {
    "dark1": "dk1",
    "light1": "lt1",
    "dark2": "dk2",
    "light2": "lt2",
    "text1": "dk1",
    "background1": "lt1",
    "text2": "dk2",
    "background2": "lt2",
    "hyperlink": "hlink",
    "followedHyperlink": "folHlink",
    **{f"accent{i}": f"accent{i}" for i in range(1, 7)},
}


@dataclass(frozen=True)
class Theme:
    name: str = "wordgenx"
    colors: dict[str, str] = field(
        default_factory=lambda: {
            "dk1": "000000",
            "lt1": "FFFFFF",
            "dk2": "1F2A44",
            "lt2": "E8ECF2",
            "accent1": "1F6FB2",
            "accent2": "E07A1F",
            "accent3": "3A9D5D",
            "accent4": "7E57C2",
            "accent5": "C0392B",
            "accent6": "16A2A8",
            "hlink": "0563C1",
            "folHlink": "954F72",
        }
    )
    major_font: str = "Calibri Light"
    minor_font: str = "Calibri"

    def __post_init__(self) -> None:
        missing = set(SCHEME_SLOTS) - set(self.colors)
        if missing:
            raise ValueError(f"theme is missing colors: {sorted(missing)}")

    def theme_color_rgb(self, name: str) -> str:
        return self.colors[THEME_COLOR_SLOTS[name]]


def _solid_placeholder() -> Element:
    return E("a:solidFill", E("a:schemeClr", {"val": "phClr"}))


def theme_xml(theme: Theme) -> Element:
    def font(tag: str, typeface: str) -> Element:
        return E(
            tag,
            E("a:latin", {"typeface": typeface}),
            E("a:ea", {"typeface": ""}),
            E("a:cs", {"typeface": ""}),
        )

    color_scheme = E(
        "a:clrScheme",
        {"name": theme.name},
        *(E(f"a:{slot}", E("a:srgbClr", {"val": theme.colors[slot]})) for slot in SCHEME_SLOTS),
    )
    font_scheme = E(
        "a:fontScheme",
        {"name": theme.name},
        font("a:majorFont", theme.major_font),
        font("a:minorFont", theme.minor_font),
    )
    format_scheme = E(
        "a:fmtScheme",
        {"name": theme.name},
        E("a:fillStyleLst", *(_solid_placeholder() for _ in range(3))),
        E(
            "a:lnStyleLst",
            *(E("a:ln", {"w": str(6350 * (i + 1))}, _solid_placeholder()) for i in range(3)),
        ),
        E("a:effectStyleLst", *(E("a:effectStyle", E("a:effectLst")) for _ in range(3))),
        E("a:bgFillStyleLst", *(_solid_placeholder() for _ in range(3))),
    )
    return E(
        "a:theme",
        {"name": theme.name},
        E("a:themeElements", color_scheme, font_scheme, format_scheme),
        E("a:objectDefaults"),
        E("a:extraClrSchemeLst"),
        nsmap=nsmap("a"),
    )


_A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"


def theme_from_xml(root: Element) -> Theme:
    """The colours and fonts of an existing theme part (a template's), for resolving
    theme colours; unknown or missing entries keep wordgenx's defaults."""
    default = Theme()
    colors = dict(default.colors)
    scheme = root.find(f"{_A}themeElements/{_A}clrScheme")
    if scheme is not None:
        for slot in SCHEME_SLOTS:
            entry = scheme.find(f"{_A}{slot}")
            if entry is None or not len(entry):
                continue
            color = entry[0]
            value = color.get("lastClr") if color.tag == f"{_A}sysClr" else color.get("val")
            if value and len(value) == 6:
                colors[slot] = value.upper()
    fonts = root.find(f"{_A}themeElements/{_A}fontScheme")

    def typeface(which: str, fallback: str) -> str:
        latin = fonts.find(f"{_A}{which}/{_A}latin") if fonts is not None else None
        return (latin.get("typeface") if latin is not None else None) or fallback

    return Theme(
        name=root.get("name") or default.name,
        colors=colors,
        major_font=typeface("majorFont", default.major_font),
        minor_font=typeface("minorFont", default.minor_font),
    )
