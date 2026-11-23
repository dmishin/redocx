"""The compiler pipeline: source -> parse -> lower -> .docx (design §4.1)."""

from __future__ import annotations

from collections.abc import Collection, Mapping
from dataclasses import dataclass, replace
from os import PathLike
from pathlib import Path

from redocx import ir
from redocx.lower import Located, lower
from redocx.lower.lowering import FileData
from redocx.ooxml.integrity import math_problems, package_problems
from redocx.ooxml.parts.styles import StyleError
from redocx.ooxml.template import Template, load_template
from redocx.ooxml.writer import build_package
from redocx.syntax import parse
from redocx.syntax.diagnostics import Code, Diagnostic, Severity, render
from redocx.syntax.source import Source, Span


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
    """A problem the compiler should have reported as a diagnostic: a bug in redocx."""


def compile_source(
    source: Source | str,
    base_dir: Path | None = None,
    template: TemplateArg = None,
    *,
    std: Collection[str] | None = None,
    root: str | PathLike[str] | None = None,
    files: Mapping[str, FileData] | None = None,
) -> CompileResult:
    """Parse and lower a document; the IR is returned only when there are no errors.
    `template` (a .dotx or .docx, or one `load_template` read) overrides the document's
    \\document[template=...]; a file that is not one raises `TemplateError`. `std` names
    the standard library modules loaded before the document (None: all of them). With
    `root`, every file the document names (`\\include`, `\\image`, ...) must be inside that
    folder: use it when the source holds data from outside. `files` gives pictures and
    documents in memory (`\\image`, `\\embed-docx`, `\\watermark[image=...]`), by the name the
    document uses: bytes, or a function that returns them, called once when needed."""
    if template is not None and not isinstance(template, Template):
        template = load_template(template)
    if isinstance(source, str):
        source = Source("<input>", source)
        base_dir = base_dir or Path.cwd()
    document, diagnostics = lower(
        parse(source),
        base_dir,
        template,
        std=std,
        root=None if root is None else Path(root),
        files=files,
    )
    # By file (the document first, then the others in the order they were reported), then
    # by position.
    order = {source.name: 0}
    for file, _ in diagnostics:
        order.setdefault(file.name, len(order))
    ordered = tuple(sorted(diagnostics, key=lambda item: (order[item[0].name], item[1].span.start)))
    has_errors = any(d.severity is Severity.ERROR for _, d in ordered)
    return CompileResult(None if has_errors else document, ordered)


def compile_file(
    path: str | PathLike[str],
    template: TemplateArg = None,
    *,
    std: Collection[str] | None = None,
    root: str | PathLike[str] | None = None,
) -> CompileResult:
    return compile_source(Source.from_path(path), template=template, std=std, root=root)


def build(
    source: Source | str,
    base_dir: Path | None = None,
    template: TemplateArg = None,
    *,
    as_template: bool = False,
    std: Collection[str] | None = None,
    root: str | PathLike[str] | None = None,
    files: Mapping[str, FileData] | None = None,
) -> tuple[bytes | None, CompileResult]:
    """Compile to .docx bytes, or .dotx with `as_template` (None when there are errors).
    `files` are pictures and documents in memory, as for `compile_source`.
    The written package is checked for consistency (`package_problems`); what is wrong is
    reported as warnings. Invalid math (`math_problems`) is an error, and nothing is written."""
    if isinstance(source, str):
        source = Source("<input>", source)
        base_dir = base_dir or Path.cwd()
    result = compile_source(source, base_dir, template, std=std, root=root, files=files)
    if result.document is None:
        return None, result
    try:
        package = build_package(result.document, as_template=as_template)
    except StyleError as e:  # a consistency problem the lowering did not catch
        raise InternalError(f"internal error while writing the document: {e}") from e
    invalid = math_problems(package)
    if invalid:
        errors = (*result.diagnostics, *_invalid_math(source, invalid))
        return None, replace(result, document=None, diagnostics=errors)
    problems = package_problems(package)
    if problems:
        result = replace(
            result, diagnostics=(*result.diagnostics, *_output_warnings(source, problems))
        )
    return package.to_bytes(), result


OUTPUT_HELP = (
    "Word may report the document as damaged, or a link may go nowhere. Raw XML (\\raw, "
    "ooxml.*), template content and links to #bookmark names are written as they are, so "
    "check them; otherwise this is a bug in redocx: please report it with the document"
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


def _invalid_math(source: Source, problems: list[str]) -> list[Located]:
    """Invalid math in the written package, as one error for the whole document. The writer
    is meant never to write it, and raw OMML is checked where it is written, so this is a
    bug in redocx."""
    count = f"{len(problems)} problem{'s' * (len(problems) != 1)}"
    error = Diagnostic(
        Severity.ERROR,
        Code.INVALID_MATH,
        f"the written document has invalid math ({count}; nothing was written)",
        Span(0, 0),
        help="this is a bug in redocx: please report it with the document",
        notes=tuple(problems),
        located=False,
    )
    return [(source, error)]
