"""LaTeX commands and environments people type from habit, with the redocx form to use.

Unknown-command errors and `redocx explain` use this table to say that redocx is not
LaTeX and what to write instead. Each hint points to a section of docs/guide/tex.md, which
has the full mapping; tests/commands/test_latex.py keeps the two in sync.

Names match exactly: a fuzzy match would turn typos of redocx commands into LaTeX advice.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from redocx.commands.types import plain_text
from redocx.syntax import ast

GUIDE = "docs/guide/tex.md"


@dataclass(frozen=True)
class LatexHint:
    use: str
    """What to write in redocx, as help text: "write `*x*` (Strong style) or `\\b{x}`"."""
    topic: str
    """The section of the TeX guide with the details (a `##` heading of tex.md)."""


STRUCTURE = "Document structure"
TEXT = "Text"
LISTS = "Lists"
LABELS = "Labels and references"
CITATIONS = "Citations (biblatex, natbib)"
FLOATS = "Floats, figures and pictures"
TABLES = "Tables"
MATH = "Math"
PAGE = "Page layout"
CODE = "Code listings"
MACROS = "Macros"


def _hints(*entries: tuple[str, str, str]) -> Mapping[str, LatexHint]:
    table: dict[str, LatexHint] = {}
    for names, use, topic in entries:
        for name in names.split():
            table[name] = LatexHint(use, topic)
    return MappingProxyType(table)


LATEX: Mapping[str, LatexHint] = _hints(
    # Document structure.
    ("documentclass", 'leave it out, or use `\\document[template="report.dotx"]`', STRUCTURE),
    (
        "usepackage",
        "use `\\use{file.rdxs}` for style sheets and macros, or `\\use{std:...}`",
        STRUCTURE,
    ),
    ("title author", 'set document properties with `\\meta[title="...", author="..."]`', STRUCTURE),
    ("maketitle", "write `\\p[style=Title]{...}` and `\\p[style=Subtitle]{...}`", STRUCTURE),
    (
        "chapter subsection subsubsection",
        "write a heading line: `= Title`, `== Title`, `=== Title`",
        STRUCTURE,
    ),
    ("input", "write `\\include{file.rdx}`", STRUCTURE),
    ("appendix", "change the heading numbering or style in a style sheet", STRUCTURE),
    ("tableofcontents", 'write `\\toc[levels=1-3, title="Contents"]`', STRUCTURE),
    ("listoffigures listoftables", "write `\\listof[Figure]` or `\\listof[Table]`", STRUCTURE),
    ("newpage clearpage", "write `\\pagebreak`", STRUCTURE),
    ("cleardoublepage", "write `\\section[break=odd-page]`", STRUCTURE),
    # Collisions: redocx commands with another meaning, hinted only when given a body.
    (
        "section",
        "in redocx `\\section` sets the page layout; write a heading line: `= Title`",
        STRUCTURE,
    ),
    ("index", "in redocx `\\index` prints the index; mark an entry with `\\ix{term}`", LABELS),
    # Text.
    ("par", "leave a blank line between paragraphs", TEXT),
    ("newline", "write `\\br`", TEXT),
    ("textbf bfseries", "write `*x*` (Strong style) or `\\b{x}`", TEXT),
    ("textit itshape", "write `_x_` (Emphasis style) or `\\i{x}`", TEXT),
    ("underline", "write `\\u{x}`", TEXT),
    ("texttt ttfamily verb", "write `` `x` `` (Code style)", TEXT),
    ("textsc", "write `\\font[small-caps]{x}`", TEXT),
    ("textsuperscript", "write `\\sup{x}`", TEXT),
    ("textsubscript", "write `\\sub{x}`", TEXT),
    (
        "tiny small normalsize large Large huge fontsize",
        "write `\\font[size=14pt]{x}`, or better, use a character style",
        TEXT,
    ),
    ("textcolor color", "write `\\font[color=#C00000]{x}`", TEXT),
    ("colorbox", "write `\\hl[yellow]{x}` or `\\font[shading=#FFF2CC]{x}`", TEXT),
    ("foreignlanguage", "write `\\lang[de-DE]{x}`", TEXT),
    ("ldots dots", "type `...`; the source is UTF-8", TEXT),
    ("noindent", "write `\\p[first-line-indent=0]{...}`", TEXT),
    ("vspace", "write `\\p[space-before=12pt]{...}`, or set `space-after` in a style", TEXT),
    ("hspace hfill", "write `\\tab`, with tab stops from `\\tabs[(16cm, right)]{...}`", TEXT),
    ("centering", "write `\\align[center]{...}`; inside `\\figure` the style centres it", FLOATS),
    ("marginpar", "use a floating `\\textbox` or a `\\comment`", TEXT),
    ("href", 'write `[text](https://...)` or `\\link[to="https://..."]{text}`', TEXT),
    ("today", "write `\\date`", TEXT),
    # Labels and references.
    ("label", "add `{#name}` after a heading or `$$ ... $$`, or `id=name` on a command", LABELS),
    ("autoref cref Cref eqref", 'write `@name`; it prints "Figure 3", "2.1" or "(4)"', LABELS),
    ("nameref", "write `\\ref[form=text]{name}`", LABELS),
    ("hyperref", "write `[text](#name)`", LABELS),
    ("newcounter refstepcounter", "write `\\seq[Name, id=name]` (a Word SEQ field)", LABELS),
    ("printindex", "write `\\index[columns=2]`", LABELS),
    # Citations.
    ("addbibresource", 'write `\\bibliography[file="refs.bib", style=apa]`', CITATIONS),
    ("parencite citep", "write `\\cite{key}`", CITATIONS),
    ("citet", "write `\\textcite{key}`", CITATIONS),
    # Floats and pictures.
    ("includegraphics", "write `\\image[width=50%]{file.png}`", FLOATS),
    ("captionof", "write `\\caption[Figure]{...}`", FLOATS),
    # Tables.
    (
        "hline toprule midrule bottomrule",
        "use a table style, or `borders=...` on `\\table`",
        TABLES,
    ),
    ("multicolumn", "write `\\cell[colspan=3]{x}` followed by the empty cells it covers", TABLES),
    ("multirow", "write `\\cell[rowspan=2]{x}`", TABLES),
    ("rowcolor cellcolor", "use a table style, or `\\cell[shade=...]{x}`", TABLES),
    # Page layout.
    ("pagestyle fancyhead fancyfoot", "write `\\header{...}` and `\\footer{...}`", PAGE),
    ("thispagestyle", "use `\\section[title-page]` with an empty `\\header[first]{}`", PAGE),
    ("thepage", "write `\\pagenum`", PAGE),
    ("pagenumbering", "write `\\section[page-numbers=lower-roman]`", PAGE),
    ("setcounter", "use `restart=` on `\\list`, or `\\section[page-numbers=(start=1)]`", PAGE),
    ("twocolumn", "write `\\section[columns=2, break=continuous]`", PAGE),
    # Code listings.
    ("lstinline mintinline", "write `` `code` ``", CODE),
    # Macros.
    (
        "newcommand renewcommand def",
        "write `\\define{name}{...#body...}`, or `\\let{name}{text}` for plain text",
        MACROS,
    ),
    ("newenvironment", "write a `\\define` macro whose `#body` holds blocks", MACROS),
    ("newtheorem", "write a macro around a styled paragraph and a `\\seq` counter", MACROS),
    ("ifthenelse", "redocx has no conditionals; generate the source from a program", MACROS),
    # Environments: the hint depends on the name, see LATEX_ENVIRONMENTS.
    ("begin", "there are no environments; most have a command or a markup form", STRUCTURE),
    ("end", "remove it; redocx has no environments", STRUCTURE),
)
"""LaTeX command name -> what to write in redocx."""

LATEX_ENVIRONMENTS: Mapping[str, LatexHint] = _hints(
    ("document", "leave out `\\begin{document}`; the content starts after the preamble", STRUCTURE),
    ("itemize", "start each item on its own line with `- `", LISTS),
    ("enumerate", "start each item on its own line with `1. `", LISTS),
    (
        "description",
        "write `\\p[indent-left=1cm, hanging-indent=1cm]{*Term* \\tab text}` or use a style",
        LISTS,
    ),
    ("center", "write `\\align[center]{...}`", TEXT),
    ("flushright", "write `\\align[right]{...}`", TEXT),
    ("quote", "write `\\p[style=Quote]{...}`", TEXT),
    ("verbatim lstlisting minted", "write a code fence: ``` lines before and after the code", CODE),
    ("figure wrapfigure", "write `\\figure[id=fig:x]{ \\image{...} \\caption{...} }`", FLOATS),
    (
        "table tabular tabularx longtable",
        "write `\\caption{...}` and then `\\table{...}` or a `| a | b |` table",
        TABLES,
    ),
    ("equation equation*", "write `$$ ... $$ {#eq:x}`", MATH),
    ("align align*", "write `$$\\begin{aligned} ... \\end{aligned}$$ {#eq:x}`", MATH),
    ("multicols", "write `\\section[columns=2, break=continuous]`", PAGE),
    ("landscape", "write `\\section[orientation=landscape]`", PAGE),
)
"""LaTeX environment name (``\\begin{name}``) -> what to write in redocx."""


def environment(command: ast.Command) -> str | None:
    """The environment name of a ``\\begin{name}`` or ``\\end{name}``, if it is plain text."""
    if isinstance(command.body, ast.MarkupBody):
        return plain_text(command.body) or None
    return None


def latex_hint(command: ast.Command) -> LatexHint | None:
    """The hint for a LaTeX command used as if redocx were LaTeX, or None."""
    hint = LATEX.get(command.name)
    if command.name == "begin":
        name = environment(command)
        return LATEX_ENVIRONMENTS.get(name, hint) if name else hint
    if command.name == "end" and hint is not None:
        name = environment(command)
        if name:
            topic = LATEX_ENVIRONMENTS[name].topic if name in LATEX_ENVIRONMENTS else hint.topic
            return LatexHint(f"remove it; see the error on `\\begin{{{name}}}`", topic)
    return hint


def latex_note(hint: LatexHint, explain: str | None = None) -> str:
    """The note that points to the TeX guide (and to `redocx explain NAME`)."""
    where = f'"{hint.topic}" in {GUIDE}'
    return f"redocx is not LaTeX; see {where}" + (
        f", or run `redocx explain {explain}`" if explain else ""
    )
