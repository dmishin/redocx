r"""Citations: \bibliography, \cite, \textcite and \printbibliography (design §3.19).

Citations are placeholders while lowering; they are rendered once all are known.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from redocx import ir
from redocx.bibliography import (
    DEFAULT_STYLE,
    Bibliography,
    BibliographyError,
    Cite,
    html_runs,
    load_sources,
    load_style,
)
from redocx.commands.bind import Args
from redocx.commands.builtin import CITE_LOCATORS
from redocx.lower.common import BlockHandler, Format, InlineHandler, Piece, verbatim
from redocx.lower.references import References
from redocx.syntax import ast
from redocx.syntax.diagnostics import Code
from redocx.syntax.source import Source, Span

if TYPE_CHECKING:
    from redocx.lower.lowering import Lowering

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


# --- commands ------------------------------------------------------------------------------


def bibliography(lw: Lowering, command: ast.Command, args: Args) -> list[ir.Block]:
    r"""\bibliography[file=refs.bib, style=harvard]: the data (BibTeX, CSL-JSON) and the
    CSL style (bundled, or a path to a .csl file)."""
    if lw.bibliography is not None:
        lw.report(Code.CITATION, "the bibliography is already set", command.span)
        return []
    files = [lw.base_dir / name for name in args.items("file", str)]
    if not files:
        return []
    try:
        source = load_sources(files)
        style = load_style(
            args.text("style", DEFAULT_STYLE) or DEFAULT_STYLE, lw.base_dir, lw.meta.language
        )
    except BibliographyError as e:
        lw.report(Code.CITATION, str(e), command.span, help=lw.paths_help())
        return []
    lw.bibliography = Bibliography(source, style)
    return []


def cite(lw: Lowering, command: ast.Command, args: Args, fmt: Format) -> list[Piece]:
    r"""\cite[page=214, prefix="see", suffix="Lemma 2"]{key1, key2} and \textcite{key}."""
    keys = tuple(k.strip() for k in verbatim(command).split(",") if k.strip())
    if not keys:
        return []
    locator = next(((name, text) for name in CITE_LOCATORS if (text := args.text(name))), None)
    if lw.bibliography is None:
        lw.report(
            Code.CITATION,
            "citations need a bibliography",
            command.span,
            help='load one in the preamble: \\bibliography[file="refs.bib"]',
        )
        return []
    citation = Cite(
        keys,
        command.name == "textcite",
        locator,
        args.text("prefix", "") or "",
        args.text("suffix", "") or "",
    )
    missing = lw.bibliography.missing(citation)
    if missing:
        known = [str(key) for key in lw.bibliography.source]  # pyright: ignore[reportUnknownVariableType, reportUnknownArgumentType]
        close = difflib.get_close_matches(missing[0].lower(), known, n=1)
        lw.report(
            Code.CITATION,
            f"no bibliography entry `{missing[0]}`",
            command.span,
            f"did you mean `{close[0]}`?" if close else "",
        )
        return []
    lw.bibliography.cites.append(citation)
    index = len(lw.bibliography.cites) - 1
    pending = PendingCitation("", fmt.props, fmt.style, index, keys[0].lower())
    return [Piece("", fmt, "field", citation=pending)]


def print_bibliography(lw: Lowering, command: ast.Command, args: Args) -> list[ir.Block]:
    r"""\printbibliography[title="References", level=1, all]: the cited entries, or with
    `all` every entry."""
    lw.has_content = True
    if not lw.in_body(command):
        return []
    if lw.bibliography is None:
        lw.report(
            Code.CITATION, "\\printbibliography needs \\bibliography in the preamble", command.span
        )
        return []
    title = args.text("title", "References")
    return [
        PendingBibliography(
            title=None if title == "none" else title,
            level=args.number("level", 1) or 1,
            include_all=args.flag("all"),
            span=command.span,
            source=lw.source_of(command.span),
        )
    ]


BLOCKS: dict[str, BlockHandler] = {
    "bibliography": bibliography,
    "printbibliography": print_bibliography,
}
INLINES: dict[str, InlineHandler] = {"cite": cite, "textcite": cite}
