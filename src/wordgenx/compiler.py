"""The compiler pipeline: source -> parse -> lower -> .docx (design §4.1)."""

from __future__ import annotations

from dataclasses import dataclass, replace
from os import PathLike
from pathlib import Path

from wordgenx import ir
from wordgenx.lower import Located, lower
from wordgenx.ooxml.integrity import package_problems
from wordgenx.ooxml.parts.styles import StyleError
from wordgenx.ooxml.template import Template, load_template
from wordgenx.ooxml.writer import build_package
from wordgenx.syntax import parse
from wordgenx.syntax.diagnostics import Code, Diagnostic, Severity, render
from wordgenx.syntax.source import Source, Span


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
    """Compile to .docx bytes, or .dotx with `as_template` (None when there are errors).
    The written package is checked for consistency (`package_problems`); what is wrong is
    reported as warnings."""
    if isinstance(source, str):
        source = Source("<input>", source)
        base_dir = base_dir or Path.cwd()
    result = compile_source(source, base_dir, template)
    if result.document is None:
        return None, result
    try:
        package = build_package(result.document, as_template=as_template)
    except StyleError as e:  # a consistency problem the lowering did not catch
        raise InternalError(f"internal error while writing the document: {e}") from e
    problems = package_problems(package)
    if problems:
        result = replace(
            result, diagnostics=(*result.diagnostics, *_output_warnings(source, problems))
        )
    return package.to_bytes(), result


OUTPUT_HELP = (
    "Word may report the document as damaged. Raw XML (\\raw, ooxml.*) and template content "
    "are written as they are, so check them; otherwise this is a bug in wordgenx: please "
    "report it with the document"
)


def _output_warnings(source: Source, problems: list[str]) -> list[Located]:
    """The problems of the written package, as one warning for the whole document (they
    have no place in the source)."""
    count = f"{len(problems)} problem{'s' * (len(problems) != 1)}"
    warning = Diagnostic(
        Severity.WARNING,
        Code.INCONSISTENT_OUTPUT,
        f"the written document is inconsistent ({count})",
        Span(0, 0),
        help=OUTPUT_HELP,
        notes=tuple(problems),
        located=False,
    )
    return [(source, warning)]
