"""Citations in the document: placeholders while lowering, rendered once all are known."""

from __future__ import annotations

import re
from dataclasses import dataclass, replace

from wordgenx import ir
from wordgenx.bibliography import Bibliography, html_runs
from wordgenx.lower.references import References
from wordgenx.syntax.source import Source, Span

NO_SPAN = Span(0, 0)
LABEL_RE = re.compile(r"^(\[\d+\]|\d+\.)\s*")
"""A numeric style's entry label ("[3]", "3."), separated from the entry by a tab."""


@dataclass(frozen=True)
class PendingCitation(ir.Run):
    """A \\cite or \\textcite, rendered when the whole document has been read."""

    index: int = 0
    """Its position in Bibliography.cites."""
    first_key: str = ""


@dataclass(frozen=True)
class PendingBibliography(ir.Paragraph):
    """Where \\printbibliography puts the list of references."""

    title: str | None = "References"
    level: int = 1
    include_all: bool = False
    span: Span = NO_SPAN
    source: Source | None = None


def bookmark_for(references: References, key: str, names: dict[str, str]) -> str:
    if key not in names:
        names[key] = references.bookmark(f"bib-{key}")
    return names[key]


def expand(
    body: tuple[ir.Block, ...],
    section: ir.Section,
    bibliography: Bibliography | None,
    references: References,
) -> tuple[tuple[ir.Block, ...], ir.Section, set[str]]:
    """Render the citations and bibliographies. Returns the body, the section (its headers
    may cite too), and the styles used."""
    if bibliography is None:
        return body, section, set()
    printed = any(isinstance(b, PendingBibliography) for b in body)
    include_all = any(isinstance(b, PendingBibliography) and b.include_all for b in body)
    rendered = bibliography.render(include_all)
    names: dict[str, str] = {}
    used: set[str] = set()

    def citation(paragraph: ir.Paragraph) -> ir.Paragraph:
        if not any(isinstance(item, PendingCitation) for item in paragraph.content):
            return paragraph
        content: list[ir.Inline] = []
        for item in paragraph.content:
            if not isinstance(item, PendingCitation):
                content.append(item)
                continue
            runs = [
                r
                for r in html_runs(rendered[item.index], item.props, item.style, links=False)
                if isinstance(r, ir.Run)
            ]
            if printed:
                content.append(
                    ir.Hyperlink(
                        "", tuple(runs), anchor=bookmark_for(references, item.first_key, names)
                    )
                )
            else:
                content.extend(runs)
        return replace(paragraph, content=tuple(content))

    body = ir.map_paragraphs(body, citation)
    section = ir.map_section(section, citation)
    entries = bibliography.entries()
    out: list[ir.Block] = []
    for block in body:
        if not isinstance(block, PendingBibliography):
            out.append(block)
            continue
        if block.title:
            heading = f"Heading {block.level}"
            used.add(heading)
            out.append(ir.Paragraph((ir.Run(block.title),), heading, unnumbered=True))
        used.add("Bibliography")
        for key, html in entries:
            match = LABEL_RE.match(html)
            label: list[ir.Inline] = []
            if match:
                label = [ir.Run(match.group(1)), ir.Run("\t")]
                html = html[match.end() :]
            name = bookmark_for(references, key, names)
            content = (ir.BookmarkStart(name), *label, *html_runs(html), ir.BookmarkEnd(name))
            if any(isinstance(i, ir.Hyperlink) for i in content):
                used.add("Hyperlink")
            out.append(ir.Paragraph(content, "Bibliography"))
    return tuple(out), section, used


__all__ = ["PendingBibliography", "PendingCitation", "expand"]
