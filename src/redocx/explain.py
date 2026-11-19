"""`redocx explain`: what a command, formatting property or macro is, from the tables
that define the language (the same facts as docs/reference.md, which tools/make_reference.py
writes from this module's catalogue).

Macros are found without compiling: the file is parsed, `\\use` and `\\include` are
followed, and each `\\define` / `\\let` is shown with its parameters, the `%` comment above
it and its definition.
"""

from __future__ import annotations

import contextlib
import dataclasses
import difflib
import textwrap
from collections.abc import Iterator
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

from redocx.commands.builtin import CODE_BLOCK, COMMANDS
from redocx.commands.latex import GUIDE, LATEX, LATEX_ENVIRONMENTS, LatexHint
from redocx.commands.spec import POSTPONED, CommandSpec, Placement
from redocx.props.registry import REGISTRY, PropSpec, Target
from redocx.syntax import Source, ast, parse
from redocx.syntax.commands import ArgKind

# --- the catalogue (shared with tools/make_reference.py) -----------------------------------

CATEGORIES: dict[str, tuple[str, ...]] = {
    "Preamble: the document as a whole": (
        "document", "meta", "settings", "use", "include", "styles", "bibliography",
    ),
    "Paragraphs, lists and tab stops": ("p", "align", "list", "tabs"),
    "Sections, headers and footers": ("section", "header", "footer", "watermark"),
    "Tables": ("table", "row", "cell"),
    "Captions, contents and the index": (
        "caption", "figure", "toc", "listof", "index", "ix", "printbibliography",
    ),
    "Text formatting": (
        "b", "i", "u", "s", "sup", "sub", "hidden", "hl", "strong", "emph", "code", "font",
        "style", "lang",
    ),
    "Links, references and notes": (
        "link", "url", "ref", "pageref", "footnote", "endnote", "cite", "textcite", "comment",
    ),
    "Fields and counters": ("pagenum", "numpages", "sectionpages", "date", "field", "seq"),
    "Breaks": ("br", "tab", "pagebreak", "columnbreak"),
    "Pictures, text boxes and forms": ("image", "textbox", "control", "embed-docx"),
    "Math": ("math", "m"),
    "Escape hatches": ("raw",),
    "Not implemented": ("ins", "del"),
}  # fmt: skip
"""Every command, once: tests/test_reference.py checks that none is missing."""

PARTS = {"row": "`\\table`", "cell": "`\\row`"}
WHERE = {
    Placement.BLOCK: "a block of its own",
    Placement.INLINE: "inside text",
    Placement.BOTH: "inside text, or alone as a block",
}
BODIES = {
    ArgKind.MARKUP: "text and commands",
    ArgKind.STRING: "{doc}, read as is (only `\\}}` and `\\\\` are escapes)",
    ArgKind.RAW: "{doc}, read as is (braces must balance)",
    ArgKind.MATH: "a formula in LaTeX notation",
    ArgKind.STYLESHEET: "style-sheet rules (see the style-sheet guide)",
}
TARGETS = {
    Target.RUN: (
        "Character properties", "`\\font[...]`, character styles, `\\p` and paragraph styles"
    ),
    Target.PARAGRAPH: ("Paragraph properties", "`\\p[...]`, `\\figure[...]`, paragraph styles"),
    Target.TABLE: ("Table properties", "`\\table[...]`, table styles"),
    Target.CELL: ("Cell properties", "`\\cell[...]`, table styles"),
}  # fmt: skip
TEXT_VALUE = 'text: a word, a "string" or {plain text}'


def sentence(text: str) -> str:
    text = text.strip()
    if not text:
        return ""
    text = text[0].upper() + text[1:]
    return text if text.endswith((".", "?", "!", ")")) else text + "."


def where(spec: CommandSpec) -> str:
    if spec.placement is Placement.PART:
        return f"directly inside {PARTS[spec.name]}"
    return WHERE[spec.placement]


def body(spec: CommandSpec) -> str:
    if spec.body is None:
        return "none"
    text = BODIES[spec.body].format(doc=spec.body_doc)
    return text if spec.body_required else f"{text} (optional)"


def planned(spec: CommandSpec) -> str | None:
    if spec.planned is None:
        return None
    return "postponed" if spec.planned == POSTPONED else f"planned for {spec.planned}"


def value(text: str) -> str:
    """A value description, with "text" for the usual text value."""
    return text.replace(TEXT_VALUE, "text")


# --- commands and properties ---------------------------------------------------------------


def _wrapped(text: str, indent: int) -> str:
    return textwrap.fill(
        text, 100, initial_indent=" " * indent, subsequent_indent=" " * (indent + 2),
        break_on_hyphens=False,
    )  # fmt: skip


def explain_command(spec: CommandSpec) -> str:
    title = spec.title.capitalize() if spec.title else f"\\{spec.name}"
    lines = [f"{title}: {sentence(spec.doc)}", ""]
    if (when := planned(spec)) is not None:
        return "\n".join([*lines, f"Not implemented ({when}): ignored with a warning."]) + "\n"
    lines += [f"  Where:  {where(spec)}", f"  Body:   {body(spec)}"]
    if spec.positional:
        many = " (several values: each one)" if spec.variadic else ""
        lines.append(f"  Positional option: `{spec.positional}`{many}")
    if spec.props is not None:
        label = TARGETS[spec.props][0].lower()
        lines.append(f"  Also takes every one of the {label} as an option")
        lines.append(f"    (redocx explain --properties {spec.props.name.lower()})")
    for prefix, kind in spec.patterns.items():
        lines.append(f"  Also takes `{prefix}NAME=value`: {value(kind.describe())}")
    if spec.options:
        lines += ["", "  Options:"]
        width = max(len(o.name) for o in spec.options) + 2
        for option in spec.options:
            name = option.name.ljust(width)
            flags = " (required)" if option.required else ""
            if option.name in spec.planned_options:
                flags += f" (planned for {spec.planned_options[option.name]})"
            lines.append(f"    {name}{option.doc}{flags}")
            lines.append(_wrapped(f"value: {value(option.type.describe())}", 4 + width))
    anchor = "cmd-" + spec.name.replace(" ", "-")
    lines += ["", f"  More: docs/reference.md#{anchor}"]
    return "\n".join(lines) + "\n"


def explain_property(spec: PropSpec) -> str:
    label, used = TARGETS[spec.target]
    return (
        f"{spec.name} ({label.lower().removesuffix('properties')}property): "
        f"{sentence(spec.doc)}\n\n"
        f"  Values:  {spec.values}\n"
        f"  Used in: {used.replace('`', '')}; in style sheets as `{spec.name}: ...;`\n"
    )


def explain_properties(target: Target) -> str:
    label, used = TARGETS[target]
    specs = REGISTRY[target].values()
    width = max(len(s.name) for s in specs) + 2
    lines = [f"{label}, used in {used.replace('`', '')}:", ""]
    lines += [f"  {s.name.ljust(width)}{sentence(s.doc)}  [{s.values}]" for s in specs]
    return "\n".join(lines) + "\n"


def overview() -> str:
    lines = ["Commands (redocx explain NAME for one of them):", ""]
    for category, names in CATEGORIES.items():
        lines.append(f"  {category}:")
        lines.append("    " + "  ".join(f"\\{n}" for n in names))
    lines += [
        "",
        "Also: code blocks (explain code-block), formatting properties (explain --properties",
        "run|paragraph|table|cell), the standard library (explain std:callout), and the macros",
        "of a document (explain NAME --in FILE.rdx).",
    ]
    return "\n".join(lines) + "\n"


# --- macros --------------------------------------------------------------------------------


@dataclass(frozen=True)
class MacroDoc:
    name: str
    params: tuple[tuple[str, str | None], ...]
    """Parameter -> its default as written (None: no default)."""
    uses_body: bool
    constant: bool
    """A \\let: no parameters."""
    comment: str
    definition: str
    where: str
    """file:line"""


def _nodes(node: object) -> Iterator[object]:
    """`node` and every AST node under it."""
    yield node
    if dataclasses.is_dataclass(node) and not isinstance(node, type):
        for f in dataclasses.fields(node):
            yield from _nodes(getattr(node, f.name))
    elif isinstance(node, tuple):
        for item in node:  # pyright: ignore[reportUnknownVariableType]
            yield from _nodes(item)  # pyright: ignore[reportUnknownArgumentType]


def _comment_above(source: Source, offset: int) -> str:
    """The `%` lines right above `offset`, without the `%`."""
    *above, start_of_line = source.text[:offset].split("\n")
    if start_of_line.strip():  # the definition does not start its line
        return ""
    found: list[str] = []
    for line in reversed(above):
        stripped = line.strip()
        if not stripped.startswith("%"):
            break
        found.append(stripped[1:].removeprefix(" ").rstrip())
    return "\n".join(reversed(found)).strip("\n")


def _header_comment(source: Source) -> str:
    """The `%` lines a file starts with (a module's description)."""
    found: list[str] = []
    for line in source.text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("%"):
            break
        found.append(stripped[1:].removeprefix(" ").rstrip())
    return "\n".join(found).strip("\n")


def _std_source(module: str) -> Source | None:
    path = resources.files("redocx.std") / f"{module}.rdx"
    if not path.is_file():
        return None
    return Source(f"std:{module}", path.read_text("utf-8"))


def std_modules() -> list[str]:
    root = resources.files("redocx.std")
    return sorted(e.name.removesuffix(".rdx") for e in root.iterdir() if e.name.endswith(".rdx"))


def _macro(node: ast.Define | ast.Let, source: Source) -> MacroDoc:
    params: list[tuple[str, str | None]] = []
    if isinstance(node, ast.Define) and node.params is not None:
        for option in node.params.items:
            if option.key is None and isinstance(option.value, ast.BareValue):
                params.append((option.value.text, None))
            elif option.key is not None:
                written = source.text[option.value.span.start : option.value.span.end]
                params.append((option.key, written))
    definition = source.text[node.span.start : node.span.end]
    line = source.position(node.span.start).line
    return MacroDoc(
        node.name,
        tuple(params),
        "#body" in definition,
        isinstance(node, ast.Let),
        _comment_above(source, node.span.start),
        definition,
        f"{source.name}:{line}",
    )


def find_macros(source: Source, base_dir: Path) -> dict[str, MacroDoc]:
    """The macros `source` defines or loads (\\use, \\include, std: modules), by name; a
    later definition replaces an earlier one, as when the document is compiled."""
    found: dict[str, MacroDoc] = {}
    seen: set[str] = set()

    def visit(src: Source, folder: Path) -> None:
        if src.name in seen:
            return
        seen.add(src.name)
        for node in _nodes(parse(src).ast):
            if isinstance(node, ast.Define | ast.Let):
                found[node.name] = _macro(node, src)
            elif (
                isinstance(node, ast.Command)
                and node.name in ("use", "include")
                and isinstance(node.body, ast.VerbatimBody)
            ):
                target = node.body.text.strip()
                if target.startswith("std:"):
                    std = _std_source(target.removeprefix("std:"))
                    if std is not None:
                        visit(std, folder)
                elif target.endswith(".rdx"):
                    path = folder / target
                    with contextlib.suppress(OSError, UnicodeDecodeError):  # build reports it
                        visit(Source.from_path(path), path.resolve().parent)

    visit(source, base_dir)
    return found


def std_macros() -> dict[str, MacroDoc]:
    found: dict[str, MacroDoc] = {}
    for module in std_modules():
        source = _std_source(module)
        assert source is not None
        found |= find_macros(source, Path.cwd())
    return found


def explain_macro(macro: MacroDoc) -> str:
    kind = "constant" if macro.constant else "macro"
    sections = [[f"\\{macro.name}: a {kind} defined at {macro.where}"]]
    if macro.comment:
        sections.append([f"  {line}".rstrip() for line in macro.comment.splitlines()])
    facts: list[str] = []
    if macro.params:
        facts.append("  Options:")
        for name, default in macro.params:
            facts.append(f"    {name}" + (f" (default {default})" if default else " (optional)"))
    if not macro.constant:
        facts.append("  Body: " + ("its #body" if macro.uses_body else "none (the body is unused)"))
    if macro.where.startswith("std:"):
        module = macro.where.split(":")[1]
        facts.append(
            f"  From std:{module}, loaded by default; `--no-std` turns it off"
            f" (redocx explain std:{module})"
        )
    if facts:
        sections.append(facts)
    definition = [f"    {line}".rstrip() for line in macro.definition.splitlines()]
    sections.append(["  Definition:", *definition])
    return "\n\n".join("\n".join(section) for section in sections) + "\n"


def explain_module(module: str) -> str | None:
    source = _std_source(module)
    if source is None:
        return None
    macros = find_macros(source, Path.cwd())
    lines = [f"std:{module}: a module of the standard library, loaded by default", ""]
    lines += [f"  {line}".rstrip() for line in _header_comment(source).splitlines()]
    lines += ["", "  Macros: " + ", ".join(f"\\{name}" for name in macros)]
    lines.append(f"  (redocx explain {next(iter(macros), 'NAME')} for one of them)")
    return "\n".join(lines) + "\n"


def explain_latex(name: str, hint: LatexHint) -> str:
    """What to write in redocx instead of the LaTeX command `name`."""
    what = "means something else in LaTeX" if name in COMMANDS else "is LaTeX, not redocx"
    text = f"\\{name} {what}. {sentence(hint.use)}\n"
    macro = std_macros().get(name)
    if macro is not None and macro.where.startswith("std:latex:"):
        text += "  std:latex makes it work as in LaTeX; `--no-latex` turns that off.\n"
    if name == "begin":
        text += "".join(
            f"  {{{env}}}: {sentence(h.use)}\n" for env, h in LATEX_ENVIRONMENTS.items()
        )
    return text + f'  See "{hint.topic}" in {GUIDE} (redocx for TeX users).\n'


# --- lookup --------------------------------------------------------------------------------


def explain(name: str, document: Path | None = None) -> tuple[str, bool]:
    """What `name` is (a command, property, macro or std: module), and whether it was
    found; otherwise suggestions. `document` adds the macros it defines or loads."""
    name = name.strip().removeprefix("\\")
    if name.startswith("std:"):
        text = explain_module(name.removeprefix("std:"))
        if text is not None:
            return text, True
        modules = ", ".join(f"std:{m}" for m in std_modules())
        return f"unknown standard library module `{name}`; available: {modules}\n", False
    macros = std_macros()
    if document is not None:
        macros |= find_macros(Source.from_path(document), document.resolve().parent)
    parts: list[str] = []
    if name in macros:
        parts.append(explain_macro(macros[name]))
    if name in COMMANDS:
        parts.append(explain_command(COMMANDS[name]))
    if name in ("code-block", "code block", "```"):
        parts.append(explain_command(CODE_BLOCK))
    parts += [explain_property(REGISTRY[t][name]) for t in Target if name in REGISTRY[t]]
    if name in LATEX:
        parts.append(explain_latex(name, LATEX[name]))
    if parts:
        return "\n".join(parts), True
    known = {*COMMANDS, *macros, *(n for t in Target for n in REGISTRY[t])}
    close = difflib.get_close_matches(name, known, n=3)
    hint = f"; did you mean {', '.join(f'`{c}`' for c in close)}?" if close else ""
    where = "" if document else " (for a document's own macros, add --in FILE.rdx)"
    return f"`{name}` is not a command, property or macro{hint}{where}\n", False
