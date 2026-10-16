r"""Images: \image, SVG included (design §3.16), and \embed-docx."""

from __future__ import annotations

import io
import re
import zipfile
from decimal import Decimal
from typing import TYPE_CHECKING

from lxml import etree
from PIL import Image as PILImage
from PIL import UnidentifiedImageError

from wordgenx import ir
from wordgenx.commands.bind import Args
from wordgenx.commands.types import Percent
from wordgenx.lower.common import BlockHandler, Format, InlineHandler, Piece, verbatim
from wordgenx.syntax import ast
from wordgenx.syntax.diagnostics import Code
from wordgenx.values import Length

if TYPE_CHECKING:
    from wordgenx.lower.lowering import Lowering

DEFAULT_DPI = 96
SVG_NS = "http://www.w3.org/2000/svg"
SVG_UNITS = {
    "": Decimal(1) / 96,
    "px": Decimal(1) / 96,
    "pt": Decimal(1) / 72,
    "pc": Decimal(1) / 6,
    "in": Decimal(1),
    "cm": Decimal(1) / Decimal("2.54"),
    "mm": Decimal(1) / Decimal("25.4"),
}
"""Inches per unit of an SVG length (CSS units: 96 px to the inch)."""
SVG_LENGTH_RE = re.compile(r"\s*([0-9]*\.?[0-9]+(?:[eE][+-]?[0-9]+)?)\s*([a-z]*)\s*")
SVG_DEFAULT_SIZE = (300, 150)
"""The size a browser gives an SVG that states none, in CSS pixels."""
PLACEHOLDER_GREY = (217, 217, 217)
PLACEHOLDER_PIXELS = 200


def resolve_size(lw: Lowering, size: Length | Percent) -> Length:
    """A length, or a percentage of the text width."""
    if isinstance(size, Percent):
        return Length(int(lw.text_width().emu * size.value / 100))
    return size


def read_file(lw: Lowering, command: ast.Command, name: str, what: str) -> bytes | None:
    path = lw.base_dir / name
    try:
        return path.read_bytes()
    except OSError as e:
        lw.report(
            Code.INVALID_ARGUMENT,
            f"cannot read {what} `{path.name}`: {e.strerror}",
            command.span,
            help=lw.paths_help(),
        )
        return None


def raster(
    lw: Lowering, command: ast.Command, name: str, data: bytes
) -> tuple[str, tuple[Length, Length]] | None:
    """The format and natural size of a PNG, JPEG, GIF or BMP."""
    try:
        with PILImage.open(io.BytesIO(data)) as picture:
            image_format = (picture.format or "").lower()
            pixels = picture.size
            dpi = picture.info.get("dpi")
    except (UnidentifiedImageError, OSError):
        lw.report(Code.INVALID_ARGUMENT, f"`{name}` is not an image Word can show", command.span)
        return None
    if image_format not in ir.IMAGE_FORMATS:
        lw.report(
            Code.INVALID_ARGUMENT,
            f"unsupported image format {image_format.upper() or '?'}",
            command.span,
            help="use PNG, JPEG, GIF, BMP or SVG",
        )
        return None
    dpi_x, dpi_y = _dpi(dpi)
    natural = (Length.inch(Decimal(pixels[0]) / dpi_x), Length.inch(Decimal(pixels[1]) / dpi_y))
    return image_format, natural


def image(lw: Lowering, command: ast.Command, args: Args, fmt: Format) -> list[Piece]:
    name = verbatim(command)
    if not name:
        return []
    size = args.values.get("width")
    width = resolve_size(lw, size) if isinstance(size, Length | Percent) else None
    height = args.get("height", Length)
    data = read_file(lw, command, name, "image")
    if data is None:
        return []
    file_name = (lw.base_dir / name).name
    fallback = args.text("fallback")
    svg: bytes | None = None
    if _is_svg(name, data):
        svg = data
        natural = svg_size(data)
        if natural is None:
            lw.report(Code.INVALID_ARGUMENT, f"`{file_name}` is not an SVG image", command.span)
            return []
        image_format = "png"
        if fallback is None:
            data = placeholder(natural)
        else:
            data = read_file(lw, command, fallback, "fallback image")
            found = raster(lw, command, fallback, data) if data is not None else None
            if data is None or found is None:
                return []
            image_format = found[0]
    else:
        if fallback is not None:
            lw.report(
                Code.INVALID_ARGUMENT,
                "`fallback` is only for SVG images",
                command.span,
                help="every Word version shows PNG, JPEG, GIF and BMP",
            )
        found = raster(lw, command, file_name, data)
        if found is None:
            return []
        image_format, natural = found
    if width is None and height is None and lw.figure is not None:
        width = lw.figure.width
    if width is None and height is None and natural[0].emu > lw.text_width().emu:
        width = lw.text_width()  # shrink to fit, as Word does on insert
    if width is not None and height is None:
        height = Length(natural[1].emu * width.emu // natural[0].emu)
    elif height is not None and width is None:
        width = Length(natural[0].emu * height.emu // natural[1].emu)
    w, h = width or natural[0], height or natural[1]
    description = args.text("alt", "") or ""
    name_shown = args.text("name") or file_name
    try:
        made = ir.Image(data, image_format, w, h, description, name_shown, svg=svg)
    except ValueError as e:
        lw.report(Code.INVALID_ARGUMENT, str(e), command.span)
        return []
    return [Piece("", fmt, "image", made)]


def _is_svg(name: str, data: bytes) -> bool:
    if name.lower().endswith(".svg"):
        return True
    return data.lstrip()[:1] == b"<" and b"<svg" in data[:4096]


def svg_size(data: bytes) -> tuple[Length, Length] | None:
    """The size an SVG states: its width and height, else its viewBox in CSS pixels (a
    missing one follows the viewBox's proportions). None if `data` is not SVG."""
    parser = etree.XMLParser(resolve_entities=False, no_network=True)
    try:
        root = etree.fromstring(data, parser)
    except etree.XMLSyntaxError:
        return None
    if root.tag != f"{{{SVG_NS}}}svg":
        return None
    box = _view_box(root.get("viewBox"))
    ratio = box[1] / box[0] if box is not None else None
    width, height = _svg_length(root.get("width")), _svg_length(root.get("height"))
    if width is None and height is None and box is not None:
        width = box[0] * SVG_UNITS["px"]
    if width is not None and height is None:
        height = width * ratio if ratio is not None else width / 2
    elif height is not None and width is None:
        width = height / ratio if ratio is not None else height * 2
    if width is None or height is None:
        width, height = (n * SVG_UNITS["px"] for n in map(Decimal, SVG_DEFAULT_SIZE))
    return Length.inch(width), Length.inch(height)


def _view_box(value: str | None) -> tuple[Decimal, Decimal] | None:
    """The width and height of a viewBox="x y width height"."""
    parts = re.split(r"[\s,]+", (value or "").strip())
    if len(parts) != 4:
        return None
    try:
        width, height = Decimal(parts[2]), Decimal(parts[3])
    except ArithmeticError:
        return None
    return (width, height) if width > 0 and height > 0 else None


def _svg_length(value: str | None) -> Decimal | None:
    """An SVG width or height in inches; None for a missing, relative or zero value."""
    match = SVG_LENGTH_RE.fullmatch(value or "")
    if match is None or match[2] not in SVG_UNITS:
        return None
    inches = Decimal(match[1]) * SVG_UNITS[match[2]]
    return inches if inches > 0 else None


def placeholder(size: tuple[Length, Length]) -> bytes:
    """A plain grey PNG of the picture's proportions: what Word 2013 and older show in
    place of an SVG without a fallback picture."""
    width, height = size[0].emu, size[1].emu
    longest = max(width, height)
    pixels = (
        max(1, PLACEHOLDER_PIXELS * width // longest),
        max(1, PLACEHOLDER_PIXELS * height // longest),
    )
    out = io.BytesIO()
    PILImage.new("RGB", pixels, PLACEHOLDER_GREY).save(out, "PNG")
    return out.getvalue()


def embed_docx(lw: Lowering, command: ast.Command, args: Args) -> list[ir.Block]:
    r"""\embed-docx{file.docx}: Word merges the file's body in here when it opens the
    document (w:altChunk)."""
    name = verbatim(command)
    if not name or not lw.in_body(command):
        return []
    data = read_file(lw, command, name, "document")
    if data is None:
        return []
    file_name = (lw.base_dir / name).name
    if not _is_docx(data):
        lw.report(
            Code.INVALID_ARGUMENT,
            f"`{file_name}` is not a Word document (.docx)",
            command.span,
            help="save it as a .docx; macro-enabled documents and templates are not merged",
        )
        return []
    lw.has_content = True
    return [ir.EmbeddedDocx(data, file_name)]


def _is_docx(data: bytes) -> bool:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as package:
            types = package.read("[Content_Types].xml")
    except (zipfile.BadZipFile, KeyError, OSError):
        return False
    return b"wordprocessingml.document.main+xml" in types


def _dpi(info: object) -> tuple[Decimal, Decimal]:
    """Pillow's (x, y) resolution, falling back to 96 dpi for missing or implausible values."""
    if isinstance(info, tuple) and len(info) == 2:  # pyright: ignore[reportUnknownArgumentType]
        x, y = info  # pyright: ignore[reportUnknownVariableType]
        if isinstance(x, int | float) and isinstance(y, int | float) and x >= 10 and y >= 10:
            return _near_integer(x), _near_integer(y)
    return Decimal(DEFAULT_DPI), Decimal(DEFAULT_DPI)


def _near_integer(dpi: float) -> Decimal:
    """PNG stores resolution per metre, so 96 dpi reads back as 95.9866: snap such values."""
    return Decimal(round(dpi)) if abs(dpi - round(dpi)) < 0.05 else Decimal(str(dpi))


INLINES: dict[str, InlineHandler] = {"image": image}
BLOCKS: dict[str, BlockHandler] = {"embed-docx": embed_docx}
