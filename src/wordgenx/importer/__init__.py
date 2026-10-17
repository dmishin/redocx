"""docx -> wordgenx source (M6): the printer, and the import folder it writes.

`import_docx` reads a .docx, prints it as source (`printer`), writes the folder
(`doc.wgx`, `doc.wgxs`, `media/…`, `doc.import.txt`), compiles the printed source again
and compares its text with the original's.
"""

from __future__ import annotations

import io
import re
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from wordgenx import ir
from wordgenx.compiler import compile_file
from wordgenx.importer.printer import KeptStyles, Printed, print_document
from wordgenx.ooxml.constants import NS
from wordgenx.ooxml.reader import ImportReport, NotADocxError, open_package, read_docx
from wordgenx.ooxml.template import load_template
from wordgenx.ooxml.xml import serialize

__all__ = ["Imported", "import_docx", "styles_template", "text_difference", "visible_text"]


@dataclass(frozen=True)
class Imported:
    source: Path
    """The printed .wgx."""
    printed: Printed
    reader: ImportReport
    errors: int
    """Errors compiling the printed source (0: it compiles)."""
    difference: str | None
    """Where the recompiled text first differs from the original's (None: the same)."""

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
        if not self.errors:
            lines.append(
                "Its text is the original's."
                if self.difference is None
                else f"Its text differs from the original's: {self.difference}"
            )
        return "\n".join(lines) + "\n"


def import_docx(docx: str | Path, out_dir: str | Path, *, keep_styles: bool = False) -> Imported:
    """Import `docx` into the folder `out_dir` (created when missing). With `keep_styles`,
    the original without its body becomes `doc.template.docx`, which the source compiles
    into: its styles, theme, lists, defaults and settings are kept as they are."""
    docx, folder = Path(docx), Path(out_dir)
    data = docx.read_bytes()
    result = read_docx(data)
    stem = re.sub(r"[^\w.-]+", "-", docx.stem).strip("-") or "document"
    folder.mkdir(parents=True, exist_ok=True)
    kept = None
    if keep_styles:
        template = folder / f"{stem}.template.docx"
        template.write_bytes(styles_template(data))
        kept = KeptStyles(template.name, frozenset(load_template(template).lists))
    printed = print_document(result.document, f"{stem}.wgxs", kept)
    source = folder / f"{stem}.wgx"
    source.write_bytes(printed.source.encode("utf-8"))
    if printed.stylesheet:
        (folder / f"{stem}.wgxs").write_bytes(printed.stylesheet.encode("utf-8"))
    for name, data in printed.files.items():
        target = folder / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    compiled = compile_file(source)
    errors = sum(d.is_error for _, d in compiled.diagnostics)
    difference = None
    if compiled.document is not None:
        difference = text_difference(result.document, compiled.document)
    imported = Imported(source, printed, result.report, errors, difference)
    (folder / f"{stem}.import.txt").write_bytes(imported.report().encode("utf-8"))
    if compiled.diagnostics:
        (folder / f"{stem}.diagnostics.txt").write_bytes(
            (compiled.render_diagnostics() + "\n").encode("utf-8")
        )
    return imported


def visible_text(document: ir.Document) -> str:
    """The text of every run, in document order (notes, comments and text boxes where they
    are anchored; headers and footers last), without whitespace. Field results and
    generated blocks (a table of contents) are left out: Word recomputes them."""
    return "".join(_texts_of_blocks(document.body)) + "".join(
        text
        for section in document.sections()
        for _, blocks in (*section.headers, *section.footers)
        for text in _texts_of_blocks(blocks)
    )


def text_difference(original: ir.Document, rebuilt: ir.Document) -> str | None:
    a, b = visible_text(original), visible_text(rebuilt)
    if a == b:
        return None
    at = next(
        (i for i, (x, y) in enumerate(zip(a, b, strict=False)) if x != y), min(len(a), len(b))
    )
    return f"after {at} of {len(a)} characters ({len(b)} in the import)"


def _texts_of_blocks(blocks: tuple[ir.Block, ...]) -> Iterator[str]:
    for block in blocks:
        if isinstance(block, ir.Paragraph):
            yield from _texts_of_inlines(block.content)
        elif isinstance(block, ir.Table):
            for row in block.rows:
                for cell in row.cells:
                    yield from _texts_of_blocks(cell.content)


def _texts_of_inlines(items: tuple[ir.Inline, ...]) -> Iterator[str]:
    for item in items:
        if isinstance(item, ir.Run):
            yield re.sub(r"\s+", "", item.text)
        elif isinstance(item, ir.Hyperlink):
            yield from _texts_of_inlines(tuple(r for r in item.content if isinstance(r, ir.Run)))
        elif isinstance(item, ir.ContentControl):
            yield from _texts_of_inlines(item.content)
        elif isinstance(item, ir.Note | ir.TextBox):
            yield from _texts_of_blocks(item.content)
        elif isinstance(item, ir.CommentStart):
            yield from _texts_of_blocks(item.comment.content)


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
