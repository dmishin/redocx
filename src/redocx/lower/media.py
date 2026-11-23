r"""Images: \image, SVG included (design §3.16), \embed-docx and \embed-rtf."""

from __future__ import annotations

import io
import re
import zipfile
from dataclasses import replace
from decimal import Decimal
from typing import TYPE_CHECKING, Literal

from lxml import etree
from PIL import Image as PILImage
from PIL import UnidentifiedImageError

from redocx import ir
from redocx.commands.bind import Args
from redocx.commands.types import Fields, Percent
from redocx.lower.common import BlockHandler, Format, InlineHandler, Piece, markup, verbatim
from redocx.syntax import ast
from redocx.syntax.diagnostics import Code
from redocx.values import Border, Color, Length

if TYPE_CHECKING:
    from redocx.lower.lowering import Lowering

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
PLACEHOLDER_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010802000000907753de"
    "0000000c49444154789c63b879f32600051a028cb5b098a90000000049454e44ae426082"
)
"""A 1 by 1 pixel grey (#D9D9D9) PNG: the picture shown for an SVG without a fallback and in an
empty picture control. Word stretches it to the drawing's size. It is stored, not
encoded, because PNG compression differs between zlib builds, and the output must be the
same bytes on every platform."""


def resolve_size(lw: Lowering, size: Length | Percent) -> Length:
    """A length, or a percentage of the text width."""
    if isinstance(size, Percent):
        return Length(int(lw.text_width().emu * size.value / 100))
    return size


def read_file(lw: Lowering, command: ast.Command, name: str, what: str) -> bytes | None:
    """The bytes of a file the document names: one given in memory, else one on disk."""
    if (data := lw.memory_file(name)) is not None:
        return data
    path = lw.file(name, command.span)
    if path is None:
        return None
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
    """The format and natural size of a PNG, JPEG, GIF or BMP (or an EMF or WMF metafile,
    as an imported document's embedded objects show)."""
    try:
        with PILImage.open(io.BytesIO(data)) as picture:
            image_format = (picture.format or "").lower()
            pixels = picture.size
            dpi = picture.info.get("dpi")
    except (UnidentifiedImageError, OSError):
        lw.report(Code.INVALID_ARGUMENT, f"`{name}` is not an image Word can show", command.span)
        return None
    if image_format == "wmf" and data[40:44] == b" EMF":
        image_format = "emf"  # Pillow reads both metafile kinds as WMF
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
            data = PLACEHOLDER_PNG
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
    crop = _crop(lw, args.record("crop"))
    if crop is not None:  # the picture's own size is what is left of it
        natural = (
            Length(natural[0].emu * (100_000 - crop.left - crop.right) // 100_000),
            Length(natural[1].emu * (100_000 - crop.top - crop.bottom) // 100_000),
        )
    anchor = floating(args)
    if anchor is not None and lw.figure is not None:
        lw.report(
            Code.INVALID_ARGUMENT,
            "a picture in a \\figure cannot float: its caption would not move with it",
            command.span,
            help="float the whole figure in a text box, or leave the picture inline",
        )
        anchor = None
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
        made = ir.Image(
            data,
            image_format,
            w,
            h,
            description,
            name_shown,
            svg=svg,
            anchor=anchor,
            crop=crop,
            rotation=args.number("rotate", 0) or 0,
            border=args.get("border", Border),
        )
    except ValueError as e:
        lw.report(Code.INVALID_ARGUMENT, str(e), command.span)
        return []
    return [Piece("", fmt, "image", made)]


def textbox(lw: Lowering, command: ast.Command, args: Args, fmt: Format) -> list[Piece]:
    r"""\textbox[width=6cm, fill=..., border=..., wrap=square, x=right]{blocks}"""
    if lw.figure is not None:
        lw.report(Code.MISPLACED, "a text box cannot be in a \\figure", command.span)
        return []
    with lw.contained(part="text box"):
        blocks = tuple(lw.blocks(markup(command)))
    size = args.values.get("width")
    width = resolve_size(lw, size) if isinstance(size, Length | Percent) else lw.text_width()
    top, bottom, left, right = ir.TextBox(blocks, width).padding
    if (padding := args.record("padding")) is not None:
        sides = {"top": top, "bottom": bottom, "left": left, "right": right}
        for key, value in padding.values.items():  # all=..., then single sides
            assert isinstance(value, Length)
            for side in sides if key == "all" else (key,):
                sides[side] = value
        top, bottom, left, right = sides["top"], sides["bottom"], sides["left"], sides["right"]
    fill = args.get("shading", Color) or args.get("fill", Color)
    if "shading" in args and "fill" in args:
        lw.report(
            Code.INVALID_ARGUMENT,
            "`shading` is given twice",
            args.span_of("fill"),
            help="`fill` is the older name of `shading`; keep one",
        )
    if fill is not None and fill.rgb is None and fill.theme is None:
        fill = None  # auto: no fill
    try:
        box = ir.TextBox(
            blocks,
            width,
            args.get("height", Length),
            fill,
            args.get("border", Border),
            (top, bottom, left, right),
            args.text("valign", "top") or "top",
            args.number("rotate", 0) or 0,
            floating(args),
            args.text("name", "") or "",
        )
    except ValueError as e:
        lw.report(Code.INVALID_ARGUMENT, str(e), command.span)
        return []
    lw.has_content = True
    return [Piece("", fmt, "textbox", textbox=box)]


def floating(args: Args) -> ir.Anchor | None:
    """A floating picture's placement, when `float`, `wrap`, `x` or `y` asks for one."""
    if (
        not any(key in args for key in ("float", "wrap", "x", "y"))
        or args.values.get("float") is False
    ):
        return None
    anchor = ir.Anchor(
        wrap=args.text("wrap", "square") or "square",
        wrap_side=args.text("wrap-side", "both") or "both",
    )
    for axis in ("x", "y"):
        if (position := args.record(axis)) is not None:
            at = position.values.get("at", getattr(anchor, axis))
            assert isinstance(at, Length | str)
            where = position.text("from", getattr(anchor, f"{axis}_from"))
            anchor = replace(anchor, **{axis: at, f"{axis}_from": where})
    if (distance := args.record("distance")) is not None:
        top, bottom, left, right = anchor.distance
        sides = {"top": top, "bottom": bottom, "left": left, "right": right}
        for key, value in distance.values.items():  # all=..., then single sides
            assert isinstance(value, Length)
            for side in sides if key == "all" else (key,):
                sides[side] = value
        anchor = replace(
            anchor, distance=(sides["top"], sides["bottom"], sides["left"], sides["right"])
        )
    return anchor


def _crop(lw: Lowering, fields: Fields | None) -> ir.Crop | None:
    if fields is None:
        return None
    values = {
        side: int(value * 1000)
        for side, value in fields.values.items()
        if isinstance(value, Decimal)
    }
    try:
        return ir.Crop(**values)
    except ValueError as e:
        lw.report(Code.INVALID_ARGUMENT, str(e), fields.span)
        return None


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


def embed_docx(lw: Lowering, command: ast.Command, args: Args) -> list[ir.Block]:
    r"""\embed-docx{file.docx}: Word merges the file's body in here when it opens the
    document (w:altChunk)."""
    help = "save it as a .docx; macro-enabled documents and templates are not merged"
    return _alt_chunk(lw, command, "docx", "a Word document (.docx)", help)


def embed_rtf(lw: Lowering, command: ast.Command, args: Args) -> list[ir.Block]:
    r"""\embed-rtf{file.rtf}: Word converts the RTF and puts it in here when it opens the
    document (w:altChunk)."""
    help = r"an RTF file starts with `{\rtf`; save it as Rich Text Format"
    return _alt_chunk(lw, command, "rtf", "an RTF document", help)


def _alt_chunk(
    lw: Lowering, command: ast.Command, kind: Literal["docx", "rtf"], what: str, help: str
) -> list[ir.Block]:
    """The file the command names as a w:altChunk, after checking it is a `kind` file."""
    name = verbatim(command)
    if not name or not lw.in_body(command):
        return []
    data = read_file(lw, command, name, "document")
    if data is None:
        return []
    file_name = (lw.base_dir / name).name
    if not (_is_docx(data) if kind == "docx" else _is_rtf(data)):
        lw.report(Code.INVALID_ARGUMENT, f"`{file_name}` is not {what}", command.span, help=help)
        return []
    lw.has_content = True
    return [ir.AltChunk(data, file_name, kind)]


def _is_rtf(data: bytes) -> bool:
    return data.lstrip(b"\xef\xbb\xbf \t\r\n").startswith(b"{\\rtf")


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


INLINES: dict[str, InlineHandler] = {"image": image, "textbox": textbox}
BLOCKS: dict[str, BlockHandler] = {"embed-docx": embed_docx, "embed-rtf": embed_rtf}
