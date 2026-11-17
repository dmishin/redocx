"""Find and compile the style sheets a document uses.

Style sheets come from ``\\styles{...}`` bodies and from ``\\use{file.rdxs}`` at the top
level of the document, compiled in document order (later rules modify or replace
earlier ones). Other ``\\use`` targets (macro files, ``std:`` modules) are not style
sheets and are left to the macro stage.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path

from redocx.stylesheet.compiler import CompiledStyles, StyleSheetCompiler
from redocx.stylesheet.syntax import StyleSheet, parse_stylesheet
from redocx.syntax import ParseResult, ast
from redocx.syntax.commands import ArgKind
from redocx.syntax.diagnostics import Code, Diagnostic, error
from redocx.syntax.source import Source

STYLESHEET_SUFFIX = ".rdxs"


def _top_level_commands(document: ast.Document) -> Iterator[ast.Command]:
    for block in document.blocks:
        if isinstance(block, ast.Paragraph):
            for inline in block.content:
                if isinstance(inline, ast.Command):
                    yield inline


def load_stylesheet(path: str | Path) -> StyleSheet:
    return parse_stylesheet(Source.from_path(path))


def collect_stylesheets(
    result: ParseResult, base_dir: Path | None = None
) -> tuple[list[StyleSheet], list[tuple[Source, Diagnostic]]]:
    """The style sheets of a parsed document, in order, plus loading errors."""
    if base_dir is None:
        base_dir = Path(result.source.name).resolve().parent
    sheets: list[StyleSheet] = []
    problems: list[tuple[Source, Diagnostic]] = []
    text = result.source.text
    for command in _top_level_commands(result.ast):
        body = command.body
        if command.name == "styles" and isinstance(body, ast.VerbatimBody):
            assert body.kind is ArgKind.STYLESHEET
            closed = text[body.span.end - 1 : body.span.end] == "}" and len(body.span) > 1
            end = body.span.end - 1 if closed else body.span.end
            sheets.append(parse_stylesheet(result.source, body.span.start + 1, end))
        elif command.name == "use" and isinstance(body, ast.VerbatimBody):
            target = body.text.strip()
            if not target.endswith(STYLESHEET_SUFFIX):
                continue
            path = base_dir / target
            try:
                sheets.append(load_stylesheet(path))
            except (OSError, UnicodeDecodeError) as e:
                reason = e.strerror if isinstance(e, OSError) else "not valid UTF-8"
                problems.append(
                    (
                        result.source,
                        error(
                            Code.STYLESHEET_NOT_FOUND,
                            f"cannot read style sheet `{target}`: {reason}",
                            command.span,
                            help=f"paths are relative to {base_dir}",
                        ),
                    )
                )
    return sheets, problems


def document_styles(result: ParseResult, base_dir: Path | None = None) -> CompiledStyles:
    """Compile all style sheets of a parsed document."""
    sheets, problems = collect_stylesheets(result, base_dir)
    compiler = StyleSheetCompiler()
    for sheet in sheets:
        compiler.add(sheet)
    compiled = compiler.result()
    if not problems:
        return compiled
    return replace(compiled, diagnostics=tuple(problems) + compiled.diagnostics)
