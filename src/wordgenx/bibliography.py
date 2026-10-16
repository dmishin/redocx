"""Citations and bibliographies rendered with CSL (design §3.19).

citeproc-py does the CSL work; this module loads the data (BibTeX or CSL-JSON) and the
style, renders every citation once all of them are known (so numbering and disambiguation
are right), and turns citeproc's HTML-ish output into Word runs.

citeproc-py gets four things wrong that the bundled styles rely on; they are worked around
here, for any style: the bibliography comes from its own engine (otherwise a citation's
locator replaces the entry's page range), year suffixes are added (Smith 2020a, 2020b),
punctuation moves inside closing quotes where the locale says so, and `initialize="false"`
on a name is honoured.
"""

# pyright: basic

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, ClassVar

import citeproc
from citeproc import (
    Citation,
    CitationItem,
    CitationStylesBibliography,
    CitationStylesStyle,
    Locator,
    formatter,
)
from citeproc.model import Name
from citeproc.source import BibliographySource
from citeproc.source.bibtex import BibTeX
from citeproc.source.json import CiteProcJSON

from wordgenx import ir
from wordgenx.commands.builtin import CITE_LOCATORS as LOCATORS
from wordgenx.props import EMPTY_RUN, PropertySet, Target

DATA = Path(os.path.dirname(citeproc.__file__)) / "data"
STYLES = Path(__file__).parent / "data" / "csl"
BUNDLED_STYLES = {
    "harvard": DATA / "styles" / "harvard-cite-them-right.csl",
    "harvard-cite-them-right": DATA / "styles" / "harvard-cite-them-right.csl",
    "apa": STYLES / "apa.csl",
    "ieee": STYLES / "ieee.csl",
    "chicago": STYLES / "chicago-author-date.csl",
    "chicago-author-date": STYLES / "chicago-author-date.csl",
}
"""Styles wordgenx ships (data/csl/README.md); any other is a path to a .csl file."""
DEFAULT_STYLE = "harvard"

URL_RE = re.compile(r"https?://[^\s<>\"]+[^\s<>\".,;:)]")
QUOTED_RE = re.compile(r"((?:</[a-z]+>)*[\u201d\u2019])([.,])")
"""A closing quote (after any closing tags) followed by a period or comma."""


def _suffix(i: int) -> str:
    """a, b, ..., z, aa, ab, ..."""
    return _suffix(i // 26 - 1) + chr(ord("a") + i % 26) if i >= 26 else chr(ord("a") + i)


def _get_option(self: Name, name: str, context: object = None, sort_options: object = None):
    """Name.get_option, with `initialize="false"` meaning: do not initialize. citeproc-py
    ignores the attribute and initializes as soon as an `initialize-with` is inherited."""
    if name == "initialize-with" and self.get("initialize", "true").lower() == "false":
        return None
    return _name_option(self, name, context, sort_options)


_name_option = Name.get_option
Name.get_option = _get_option


class BibliographyError(ValueError):
    """Bibliography data or a style that cannot be used."""


@dataclass(frozen=True)
class Cite:
    """One \\cite or \\textcite: the keys and how to show them."""

    keys: tuple[str, ...]
    narrative: bool = False
    locator: tuple[str, str] | None = None
    """(CSL label, value): ("page", "214")."""
    prefix: str = ""
    suffix: str = ""


def load_sources(paths: list[Path]) -> BibliographySource:
    """BibTeX (.bib) and CSL-JSON (.json) files, merged (later files win)."""
    merged: BibliographySource | None = None
    for path in paths:
        try:
            if path.suffix.lower() == ".json":
                source: BibliographySource = CiteProcJSON(
                    json.loads(path.read_text(encoding="utf-8"))
                )
            elif path.suffix.lower() in (".bib", ".bibtex"):
                source = BibTeX(str(path), encoding="utf-8")
            else:
                raise BibliographyError(
                    f"`{path.name}`: bibliography data is .bib (BibTeX) or .json (CSL-JSON)"
                )
        except OSError as e:
            raise BibliographyError(f"cannot read `{path.name}`: {e.strerror}") from None
        except (ValueError, KeyError, TypeError) as e:
            raise BibliographyError(f"`{path.name}` is not valid bibliography data: {e}") from None
        if merged is None:
            merged = source
        else:
            merged.update(source)
    if merged is None:
        raise BibliographyError("no bibliography files given")
    return merged


def load_style(name: str, base_dir: Path, language: str | None) -> CitationStylesStyle:
    if name in BUNDLED_STYLES:
        path = BUNDLED_STYLES[name]
    else:
        path = base_dir / name
        if path.suffix != ".csl":
            path = path.with_suffix(".csl")
        if not path.exists():
            bundled = ", ".join(sorted(BUNDLED_STYLES))
            raise BibliographyError(
                f"unknown citation style `{name}` (bundled: {bundled}; or a path to a .csl file)"
            )
    locale = (
        language if language and (DATA / "locales" / f"locales-{language}.xml").exists() else None
    )
    try:
        return CitationStylesStyle(str(path), locale=locale, validate=False)
    except Exception as e:
        raise BibliographyError(f"`{path.name}` is not a usable CSL style: {e}") from None


def style_format(style: CitationStylesStyle) -> str:
    """author-date, numeric, note, label or author (CSL's citation-format)."""
    for category in style.root.iter("{http://purl.org/net/xbiblio/csl}category"):
        value = category.get("citation-format")
        if value:
            return str(value)
    return "author-date"


@dataclass
class Bibliography:
    """Everything \\bibliography loaded, and the citations in document order."""

    source: BibliographySource
    style: CitationStylesStyle
    cites: list[Cite] = field(default_factory=lambda: [])
    _rendered: list[str] | None = None
    _engine: CitationStylesBibliography | None = None

    def missing(self, cite: Cite) -> list[str]:
        return [key for key in cite.keys if key.lower() not in self.source]

    def render(self, include_all: bool = False) -> list[str]:
        """The text of every citation (in the order they were added), as citeproc HTML."""
        if self._rendered is not None:
            return self._rendered
        keys = list(dict.fromkeys(key.lower() for cite in self.cites for key in cite.keys))
        if include_all:
            keys += sorted(key for key in self.source if key not in keys)
        self._add_year_suffixes(keys)
        citations = [self._citation(cite) for cite in self.cites]
        rendered: list[str] = []
        engine = CitationStylesBibliography(self.style, self.source, formatter.html)
        for citation in citations:
            engine.register(citation)
        for key in keys:  # the uncited ones too: a sorted numeric style numbers them all
            engine.register(Citation([CitationItem(key)]))
        engine.sort()
        for cite, citation in zip(self.cites, citations, strict=True):
            text = self._quotes(str(engine.cite(citation, lambda item: None)))
            rendered.append(self._narrative(cite, text) if cite.narrative else text)
        # The entries come from an engine that saw no locators: citeproc-py renders a
        # citation's locator in place of the entry's own page range.
        entries = CitationStylesBibliography(self.style, self.source, formatter.html)
        for key in keys:
            entries.register(Citation([CitationItem(key)]))
        entries.sort()
        self._rendered, self._engine = rendered, entries
        return rendered

    def entries(self) -> list[tuple[str, str]]:
        """(key, citeproc HTML) of the bibliography, in the style's order."""
        assert self._engine is not None, "render() first"
        keys = [str(k) for k in self._engine.keys]
        items = (self._quotes(str(item)) for item in self._engine.bibliography())
        return list(zip(keys, items, strict=False))

    def _add_year_suffixes(self, keys: list[str]) -> None:
        """Smith 2020a, 2020b: entries whose citations would read the same get a suffix,
        in title order, when the style asks for that (citeproc-py does not do it)."""
        root: Any = self.style.root  # citeproc's element classes, untyped
        option = root.citation.get("disambiguate-add-year-suffix", "false")
        if option.lower() != "true":
            return
        engine = CitationStylesBibliography(self.style, self.source, formatter.html)
        citations = {key: Citation([CitationItem(key)]) for key in keys}
        for citation in citations.values():
            engine.register(citation)
        same: dict[str, list[str]] = {}
        for key, citation in citations.items():
            same.setdefault(str(engine.cite(citation, lambda item: None)), []).append(key)
        for group in same.values():
            if len(group) < 2:
                continue
            group.sort(key=lambda key: str(self.source[key].get("title", key)).lower())
            for i, key in enumerate(group):
                self.source[key]["year_suffix"] = _suffix(i)

    def _quotes(self, html: str) -> str:
        """American punctuation: a period or comma after a closing quote moves inside it, as
        the locale's punctuation-in-quote says (citeproc-py leaves it outside)."""
        root: Any = self.style.root
        if root.get_locale_option("punctuation-in-quote").lower() != "true":
            return html
        return QUOTED_RE.sub(r"\2\1", html)

    def _citation(self, cite: Cite) -> Citation:
        items = []
        for i, key in enumerate(cite.keys):
            options: dict[str, object] = {}
            last = i == len(cite.keys) - 1
            if i == 0 and cite.prefix and not cite.narrative:
                options["prefix"] = cite.prefix.rstrip() + " "
            if last and cite.locator is not None:
                options["locator"] = Locator(*cite.locator)
            if last and cite.suffix and not cite.narrative:
                options["suffix"] = ", " + cite.suffix.lstrip(", ")
            items.append(CitationItem(key.lower(), **options))
        return Citation(items)

    def _narrative(self, cite: Cite, text: str) -> str:
        """ "Tibshirani (1996)": names in the text, the rest in parentheses (author-date) or
        the citation itself after the names (numeric styles)."""
        parts = []
        for key in cite.keys:
            reference = self.source[key.lower()]
            names = reference.get("author") or reference.get("editor") or []
            families = [str(n.get("family") or n.get("literal") or "") for n in names] or [
                str(reference.get("title", key))
            ]
            who = (
                families[0]
                if len(families) == 1
                else f"{families[0]} and {families[1]}"
                if len(families) == 2
                else f"{families[0]} et al."
            )
            if style_format(self.style) in ("numeric", "label"):
                parts.append(f"{who} {text}")
                break
            issued = reference.get("issued")
            year = str(getattr(issued, "year", "") or "n.d.") + str(
                reference.get("year_suffix", "")
            )
            extra = ""
            if cite.locator is not None and key == cite.keys[-1]:
                label, value = cite.locator
                extra = f", {LOCATORS.get(label, label)} {value}"
            parts.append(f"{who} ({year}{extra})")
        return ", ".join(parts)


# --- citeproc HTML -> runs ----------------------------------------------------------------------


class _Runs(HTMLParser):
    TAGS: ClassVar[dict[str, dict[str, object]]] = {
        "i": {"italic": True},
        "em": {"italic": True},
        "b": {"bold": True},
        "strong": {"bold": True},
        "sup": {"vertical-align": "superscript"},
        "sub": {"vertical-align": "subscript"},
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list[dict[str, object]] = []
        self.out: list[tuple[str, dict[str, object]]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        props = dict(self.TAGS.get(tag, {}))
        style = dict(attrs).get("style") or ""
        if "small-caps" in style:
            props["small-caps"] = True
        if "italic" in style:
            props["italic"] = True
        if "bold" in style:
            props["bold"] = True
        self.stack.append(props)

    def handle_endtag(self, tag: str) -> None:
        if self.stack:
            self.stack.pop()

    def handle_data(self, data: str) -> None:
        merged: dict[str, object] = {}
        for props in self.stack:
            merged.update(props)
        if self.out and self.out[-1][1] == merged:
            self.out[-1] = (self.out[-1][0] + data, merged)
        else:
            self.out.append((data, merged))


def html_runs(
    html: str, props: PropertySet = EMPTY_RUN, style: str | None = None, links: bool = True
) -> list[ir.Inline]:
    """Runs for citeproc HTML; URLs (and DOI links) become hyperlinks."""
    parser = _Runs()
    parser.feed(html)
    parser.close()
    out: list[ir.Inline] = []
    for text, values in parser.out:
        run_props = props.merged(PropertySet(Target.RUN, values)) if values else props
        position = 0
        for match in URL_RE.finditer(text) if links else ():
            if match.start() > position:
                out.append(ir.Run(text[position : match.start()], run_props, style))
            out.append(
                ir.Hyperlink(match.group(0), (ir.Run(match.group(0), run_props, "Hyperlink"),))
            )
            position = match.end()
        if position < len(text):
            out.append(ir.Run(text[position:], run_props, style))
    return out


__all__ = [
    "DEFAULT_STYLE",
    "Bibliography",
    "BibliographyError",
    "Cite",
    "html_runs",
    "load_sources",
    "load_style",
    "style_format",
]
