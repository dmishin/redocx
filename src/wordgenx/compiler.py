"""The compiler pipeline: source -> parse -> lower -> .docx (design §4.1)."""

from __future__ import annotations

from dataclasses import dataclass
from os import PathLike
from pathlib import Path

from wordgenx import ir
from wordgenx.lower import Located, lower
from wordgenx.ooxml.parts.styles import StyleError
from wordgenx.ooxml.template import Template, load_template
from wordgenx.ooxml.writer import to_docx_bytes
from wordgenx.syntax import parse
from wordgenx.syntax.diagnostics import Severity, render
from wordgenx.syntax.source import Source


@dataclass(frozen=True)
class CompileResult:
    document: ir.Document | None
    """None when there are errors."""
    diagnostics: tuple[Located, ...]

    @property
    def ok(self) -> bool:
        return self.document is not None

    @property
    def error_count(self) -> int:
        return sum(d.severity is Severity.ERROR for _, d in self.diagnostics)

    @property
    def warning_count(self) -> int:
        return sum(d.severity is Severity.WARNING for _, d in self.diagnostics)

    def render_diagnostics(self) -> str:
        return "\n".join(render(d, source) for source, d in self.diagnostics)


TemplateArg = Template | str | PathLike[str] | None
"""A template: loaded, or the path of a .dotx or .docx."""


class InternalError(RuntimeError):
    """A problem the compiler should have reported as a diagnostic: a bug in wordgenx."""


def compile_source(
    source: Source | str, base_dir: Path | None = None, template: TemplateArg = None
) -> CompileResult:
    """Parse and lower a document; the IR is returned only when there are no errors.
    `template` (a .dotx or .docx, or one `load_template` read) overrides the document's
    \\document[template=...]; a file that is not one raises `TemplateError`."""
    if template is not None and not isinstance(template, Template):
        template = load_template(template)
    if isinstance(source, str):
        source = Source("<input>", source)
        base_dir = base_dir or Path.cwd()
    document, diagnostics = lower(parse(source), base_dir, template)
    # By file (the document first, then the others in the order they were reported), then
    # by position.
    files = {source.name: 0}
    for file, _ in diagnostics:
        files.setdefault(file.name, len(files))
    ordered = tuple(sorted(diagnostics, key=lambda item: (files[item[0].name], item[1].span.start)))
    has_errors = any(d.severity is Severity.ERROR for _, d in ordered)
    return CompileResult(None if has_errors else document, ordered)


def compile_file(path: str | PathLike[str], template: TemplateArg = None) -> CompileResult:
    return compile_source(Source.from_path(path), template=template)


def build(
    source: Source | str,
    base_dir: Path | None = None,
    template: TemplateArg = None,
    *,
    as_template: bool = False,
) -> tuple[bytes | None, CompileResult]:
    """Compile to .docx bytes, or .dotx with `as_template` (None when there are errors)."""
    result = compile_source(source, base_dir, template)
    if result.document is None:
        return None, result
    try:
        return to_docx_bytes(result.document, as_template=as_template), result
    except StyleError as e:  # a consistency problem the lowering did not catch
        raise InternalError(f"internal error while writing the document: {e}") from e
