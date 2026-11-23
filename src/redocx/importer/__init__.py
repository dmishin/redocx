"""docx (or RTF) -> redocx source (M6): the printer, and the import folder it writes.

`import_docx` reads a .docx or an RTF file, prints it as source (`printer`), writes the folder
(`doc.rdx`, `doc.rdxs`, `media/…`, `doc.import.txt`), compiles the printed source again
and compares its text with the original's.
"""

from __future__ import annotations

import io
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path

from redocx.compiler import compile_file
from redocx.importer.check import Comparison, compare, text_difference, visible_text
from redocx.importer.printer import KeptStyles, Printed, print_document
from redocx.ooxml.constants import NS
from redocx.ooxml.reader import ImportReport, NotADocxError, open_package, read_docx
from redocx.ooxml.template import load_template
from redocx.ooxml.xml import serialize
from redocx.rtf import is_rtf, read_rtf

__all__ = ["Imported", "import_docx", "styles_template", "text_difference", "visible_text"]


@dataclass(frozen=True)
class Imported:
    source: Path
    """The printed .rdx."""
    printed: Printed
    reader: ImportReport
    errors: int
    """Errors compiling the printed source (0: it compiles)."""
    comparison: Comparison | None
    """How the compiled source compares with the original (None: it does not compile)."""

    @property
    def difference(self) -> str | None:
        """Where the recompiled text first differs from the original's (None: the same)."""
        return self.comparison.text if self.comparison is not None else None

    def report(self) -> str:
        lines = [self.reader.render(), ""]
        if self.printed.lost:
            lines.append("Not printed as source:")
            lines += [f"  {n:5}  {what}" for what, n in sorted(self.printed.lost.items())]
        else:
            lines.append("Everything read was printed as source.")
        lines.append("")
        lines.append(
            "The printed source compiles."
            if not self.errors
            else f"The printed source has {self.errors} error{'s' * (self.errors != 1)}."
        )
        if self.comparison is not None:
            lines.append(self.comparison.render())
        return "\n".join(lines) + "\n"


def import_docx(
    docx: str | Path, out_dir: str | Path, *, keep_styles: bool = False, sugar: bool = True
) -> Imported:
    """Import `docx` (a .docx, or an RTF file) into the folder `out_dir` (created when
    missing). With `keep_styles`, the original without its body becomes
    `doc.template.docx`, which the source compiles into: its styles, theme, lists, defaults
    and settings are kept as they are. Without `sugar`, the source has commands only
    (headings as \\p[style=...], ...)."""
    docx, folder = Path(docx), Path(out_dir)
    data = docx.read_bytes()
    if is_rtf(data):
        if keep_styles:
            raise ValueError("`--keep-styles` needs a .docx: there is no template to keep")
        result = read_rtf(data)
    else:
        result = read_docx(data)
    stem = re.sub(r"[^\w.-]+", "-", docx.stem).strip("-") or "document"
    folder.mkdir(parents=True, exist_ok=True)
    kept = None
    if keep_styles:
        template = folder / f"{stem}.template.docx"
        template.write_bytes(styles_template(data))
        kept = KeptStyles(template.name, frozenset(load_template(template).lists))
    printed = print_document(result.document, f"{stem}.rdxs", kept, sugar=sugar)
    source = folder / f"{stem}.rdx"
    source.write_bytes(printed.source.encode("utf-8"))
    if printed.stylesheet:
        (folder / f"{stem}.rdxs").write_bytes(printed.stylesheet.encode("utf-8"))
    for name, data in printed.files.items():
        target = folder / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    compiled = compile_file(source)
    errors = sum(d.is_error for _, d in compiled.diagnostics)
    comparison = None
    if compiled.document is not None:  # compiled into the template: styled by the original's
        styled_by = result.document if keep_styles else None
        comparison = compare(result.document, compiled.document, styled_by)
    imported = Imported(source, printed, result.report, errors, comparison)
    (folder / f"{stem}.import.txt").write_bytes(imported.report().encode("utf-8"))
    if compiled.diagnostics:
        (folder / f"{stem}.diagnostics.txt").write_bytes(
            (compiled.render_diagnostics() + "\n").encode("utf-8")
        )
    return imported


def styles_template(data: bytes) -> bytes:
    """The package without its body content: what is left is a template with the original's
    styles, theme, lists, defaults, settings and page setup. The final section keeps its
    page setup but not its headers and footers, which the imported source writes itself."""
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        parts = {info.filename: archive.read(info) for info in archive.infolist()}
    reader, document_part = open_package(data)
    root = reader.parse(document_part)
    body = root.find(f"{{{NS.W}}}body") if root is not None else None
    if root is None or body is None:
        raise NotADocxError("the document part has no w:body")
    for child in list(body):
        if child.tag != f"{{{NS.W}}}sectPr":
            body.remove(child)
    for section in body.findall(f"{{{NS.W}}}sectPr"):
        for tag in ("headerReference", "footerReference"):
            for reference in section.findall(f"{{{NS.W}}}{tag}"):
                section.remove(reference)
    parts[document_part] = serialize(root)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in parts.items():
            archive.writestr(name, content)
    return buffer.getvalue()
