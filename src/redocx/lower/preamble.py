"""The preamble and other files: \\document, \\meta, \\settings, \\use, \\include and
\\styles (design §3.3, §3.5, §3.18)."""

from __future__ import annotations

import difflib
from dataclasses import replace
from importlib import resources
from pathlib import Path
from typing import TYPE_CHECKING

from redocx import ir
from redocx.commands.bind import Args
from redocx.commands.types import Fields
from redocx.lower.common import BlockHandler, is_blank, shown_path, verbatim
from redocx.ooxml.template import Template, TemplateError, load_template
from redocx.stylesheet.collect import STYLESHEET_SUFFIX
from redocx.stylesheet.syntax import parse_stylesheet
from redocx.syntax import ParseResult, ast, parse
from redocx.syntax.diagnostics import Code
from redocx.syntax.source import Source, Span
from redocx.values import Color, Length

if TYPE_CHECKING:
    from redocx.lower.lowering import Lowering

META_FIELDS = {
    "title": "title",
    "subject": "subject",
    "author": "author",
    "description": "description",
    "last-modified-by": "last_modified_by",
}
"""\\meta option -> ir.Meta field."""


def document(lw: Lowering, command: ast.Command, args: Args) -> list[ir.Block]:
    if (language := args.text("lang")) is not None:
        lw.meta = replace(lw.meta, language=language)
    if "smart-quotes" in args:
        lw.smart_quotes = args.flag("smart-quotes")
    if (color := args.get("page-color", Color)) is not None:
        lw.settings = replace(
            lw.settings, page_color=None if color.rgb is None and color.theme is None else color
        )
    if (template := args.text("template")) is not None:
        if lw.has_content:
            lw.report(
                Code.TEMPLATE,
                "the template must be set before any content",
                args.span_of("template"),
                help="put \\document[template=...] at the top of the document",
            )
        elif not lw.template_fixed:
            use_template(lw, lw.base_dir / template, args.span_of("template"))
    return []


def use_template(lw: Lowering, path: Path, span: Span) -> None:
    """\\document[template=...]: compile into a .dotx or .docx."""
    try:
        template = load_template(path)
    except TemplateError as e:
        lw.report(Code.TEMPLATE, str(e), span, help=lw.paths_help())
        return
    apply_template(lw, template)
    if template.body_issue is not None:
        lw.warn(
            Code.TEMPLATE,
            f"the body content of `{template.name}` is not carried over: {template.body_issue}",
            span,
            help="only its styles, theme, page setup, headers and footers are used",
        )


def apply_template(lw: Lowering, template: Template, *, fixed: bool = False) -> None:
    """Compile into `template`: its page setup is the first section's starting point.
    A `fixed` one (from the command line) wins over \\document[template=...]."""
    lw.template = template
    lw.template_fixed = fixed
    lw.section = replace(template.section, headers=(), footers=())


def meta(lw: Lowering, command: ast.Command, args: Args) -> list[ir.Block]:
    for key, attribute in META_FIELDS.items():
        if (value := args.text(key)) is not None:
            lw.meta = replace(lw.meta, **{attribute: value})
    if "keywords" in args:
        lw.meta = replace(lw.meta, keywords=args.items("keywords", str))
    for key, (value, _) in args.patterns.items():  # custom.NAME
        assert isinstance(value, str | bool | int | float)
        name = key.removeprefix("custom.")
        custom = tuple((k, v) for k, v in lw.meta.custom if k != name)
        lw.meta = replace(lw.meta, custom=(*custom, (name, value)))
    return []


def settings(lw: Lowering, command: ast.Command, args: Args) -> list[ir.Block]:
    s = lw.settings
    for key, attribute in (
        ("even-odd-headers", "even_and_odd_headers"),
        ("mirror-margins", "mirror_margins"),
        ("update-fields-on-open", "update_fields_on_open"),
        ("track-changes", "track_changes"),
    ):
        if key in args:
            s = replace(s, **{attribute: args.flag(key)})
    if (hyphenation := args.text("hyphenation")) is not None:
        s = replace(s, auto_hyphenation=hyphenation == "auto")
    if (tab_stop := args.get("default-tab-stop", Length)) is not None:
        s = replace(s, default_tab_stop=tab_stop)
    for kind in ir.NOTE_KINDS:
        if (notes := args.record(f"{kind}s")) is not None:
            s = replace(s, **{f"{kind}s": replace(s.notes(kind), **dict(notes.values))})
    if (protection := args.record("protection")) is not None:
        mode = protection.text("mode")
        s = replace(s, protection=mode, protection_enforced=protection.flag("enforce"))
    lw.settings = s
    if (theme := args.text("code-theme")) is not None:
        lw.code_theme = theme
    if (captions := args.record("captions")) is not None:
        _captions(lw, captions)
    return []


def _captions(lw: Lowering, captions: Fields) -> None:
    """captions=(chapter-numbers, chapter-style="Heading 1", separator=".")."""
    style = captions.text("chapter-style", "Heading 1")
    assert style is not None
    if "chapter-style" in captions:
        lw.use_style(style, ir.StyleKind.PARAGRAPH, captions.span_of("chapter-style"))
    separator = captions.text("separator", ".")
    assert separator is not None
    lw.caption_chapters = (style, separator) if captions.flag("chapter-numbers") else None


def use(lw: Lowering, command: ast.Command, args: Args) -> list[ir.Block]:
    target = verbatim(command)
    if not target:
        return []
    if target.startswith("std:"):
        _use_std(lw, target.removeprefix("std:"), command)
        return []
    path = lw.base_dir / target
    if target.endswith(STYLESHEET_SUFFIX):
        try:
            lw.stylesheets.add(parse_stylesheet(Source.from_path(path, shown_path(path))))
        except (OSError, UnicodeDecodeError) as e:
            reason = e.strerror if isinstance(e, OSError) else "not valid UTF-8"
            lw.report(
                Code.STYLESHEET_NOT_FOUND,
                f"cannot read style sheet `{target}`: {reason}",
                command.span,
                help=lw.paths_help(),
            )
        return []
    result = lw.load(path, command)
    if result is not None:
        _macro_file(lw, result, path.resolve().parent)
    return []


def std_modules() -> list[str]:
    """Names of the standard library modules (``\\use{std:name}``)."""
    root = resources.files("redocx.std")
    return sorted(
        entry.name.removesuffix(".rdx") for entry in root.iterdir() if entry.name.endswith(".rdx")
    )


def _use_std(lw: Lowering, module: str, command: ast.Command) -> None:
    """\\use{std:name}: a macro file from the standard library (redocx/std/name.rdx)."""
    if module in lw.std_loaded:
        return
    available = std_modules()
    if module not in available:
        close = difflib.get_close_matches(module, available, n=1)
        lw.report(
            Code.INCLUDE,
            f"unknown standard library module `std:{module}`",
            command.span,
            f"did you mean `std:{close[0]}`?" if close else "",
            help=f"available: {', '.join(f'std:{m}' for m in available)}",
        )
        return
    lw.std_loaded.add(module)
    text = (resources.files("redocx.std") / f"{module}.rdx").read_text("utf-8")
    source = Source(f"std:{module}", text)
    result = parse(source)
    lw.diagnostics.extend((source, d) for d in result.diagnostics)
    _macro_file(lw, result, lw.base_dir)


def _macro_file(lw: Lowering, result: ParseResult, base_dir: Path) -> None:
    """Register a macro file's definitions; other content is ignored with a warning."""
    source, previous_dir = lw.source, lw.base_dir
    lw.source, lw.base_dir = result.source, base_dir
    try:
        for block in result.ast.blocks:
            if not isinstance(block, ast.Paragraph):
                lw.warn(
                    Code.MISPLACED, "a macro file only defines macros; this is ignored", block.span
                )
                continue
            for item in lw.expand(block.content):
                if isinstance(item, ast.Command) and item.name in ("use", "styles"):
                    lw.block_command(item)
                elif not is_blank(item):
                    lw.warn(
                        Code.MISPLACED,
                        "a macro file only defines macros; this is ignored",
                        item.span,
                    )
    finally:
        lw.source, lw.base_dir = source, previous_dir


def include(lw: Lowering, command: ast.Command, args: Args) -> list[ir.Block]:
    name = verbatim(command)
    if not name:
        return []
    path = lw.base_dir / name
    resolved = path.resolve()
    if resolved in lw.include_stack:
        lw.report(Code.INCLUDE, f"`{path.name}` includes itself", command.span)
        return []
    result = lw.load(path, command)
    if result is None:
        return []
    source, base_dir = lw.source, lw.base_dir
    lw.source, lw.base_dir = result.source, resolved.parent
    lw.include_stack.append(resolved)
    try:
        return lw.blocks(result.ast.blocks)
    finally:
        lw.include_stack.pop()
        lw.source, lw.base_dir = source, base_dir


def styles(lw: Lowering, command: ast.Command, args: Args) -> list[ir.Block]:
    body = command.body
    if not isinstance(body, ast.VerbatimBody):
        return []
    source = lw.source_of(body.span)
    closed = source.text[body.span.end - 1 : body.span.end] == "}" and len(body.span) > 1
    end = body.span.end - 1 if closed else body.span.end
    lw.stylesheets.add(parse_stylesheet(source, body.span.start + 1, end))
    return []


BLOCKS: dict[str, BlockHandler] = {
    "document": document,
    "meta": meta,
    "settings": settings,
    "use": use,
    "include": include,
    "styles": styles,
}
