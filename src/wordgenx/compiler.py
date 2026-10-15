"""The compiler pipeline: source -> parse -> lower -> .docx (design §4.1)."""

from __future__ import annotations

from dataclasses import dataclass
from os import PathLike
from pathlib import Path

from wordgenx import ir
from wordgenx.lower import Located, lower
from wordgenx.ooxml.parts.styles import StyleError
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


def compile_source(source: Source | str, base_dir: Path | None = None) -> CompileResult:
    """Parse and lower a document; the IR is returned only when there are no errors."""
    if isinstance(source, str):
        source = Source("<input>", source)
        base_dir = base_dir or Path.cwd()
    document, diagnostics = lower(parse(source), base_dir)
    ordered = tuple(
        sorted(
            diagnostics,
            key=lambda item: (item[0].name != source.name, item[0].name, item[1].span.start),
        )
    )
    has_errors = any(d.severity is Severity.ERROR for _, d in ordered)
    return CompileResult(None if has_errors else document, ordered)


def compile_file(path: str | PathLike[str]) -> CompileResult:
    return compile_source(Source.from_path(path))


def build(source: Source | str, base_dir: Path | None = None) -> tuple[bytes | None, CompileResult]:
    """Compile to .docx bytes (None when there are errors)."""
    result = compile_source(source, base_dir)
    if result.document is None:
        return None, result
    try:
        return to_docx_bytes(result.document), result
    except StyleError as e:  # a consistency problem the lowering did not catch
        raise RuntimeError(f"internal error while writing the document: {e}") from e
