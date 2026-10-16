r"""Images: \image (design §3.16)."""

from __future__ import annotations

import io
from decimal import Decimal
from typing import TYPE_CHECKING

from PIL import Image as PILImage
from PIL import UnidentifiedImageError

from wordgenx import ir
from wordgenx.commands.bind import Args
from wordgenx.commands.types import Percent
from wordgenx.lower.common import Format, InlineHandler, Piece, verbatim
from wordgenx.syntax import ast
from wordgenx.syntax.diagnostics import Code
from wordgenx.values import Length

if TYPE_CHECKING:
    from wordgenx.lower.lowering import Lowering

DEFAULT_DPI = 96


def resolve_size(lw: Lowering, size: Length | Percent) -> Length:
    """A length, or a percentage of the text width."""
    if isinstance(size, Percent):
        return Length(int(lw.text_width().emu * size.value / 100))
    return size


def image(lw: Lowering, command: ast.Command, args: Args, fmt: Format) -> list[Piece]:
    name = verbatim(command)
    if not name:
        return []
    path = lw.base_dir / name
    size = args.values.get("width")
    width = resolve_size(lw, size) if isinstance(size, Length | Percent) else None
    height = args.get("height", Length)
    try:
        data = path.read_bytes()
    except OSError as e:
        lw.report(
            Code.INVALID_ARGUMENT,
            f"cannot read image `{path.name}`: {e.strerror}",
            command.span,
            help=f"paths are relative to {lw.base_dir}",
        )
        return []
    try:
        with PILImage.open(io.BytesIO(data)) as picture:
            image_format = (picture.format or "").lower()
            pixels = picture.size
            dpi = picture.info.get("dpi")
    except (UnidentifiedImageError, OSError):
        lw.report(
            Code.INVALID_ARGUMENT, f"`{path.name}` is not an image Word can show", command.span
        )
        return []
    if image_format not in ir.IMAGE_FORMATS:
        lw.report(
            Code.INVALID_ARGUMENT,
            f"unsupported image format {image_format.upper() or '?'}",
            command.span,
            help="use PNG, JPEG, GIF or BMP",
        )
        return []
    if width is None and height is None and lw.figure is not None:
        width = lw.figure.width
    dpi_x, dpi_y = _dpi(dpi)
    natural = (Length.inch(Decimal(pixels[0]) / dpi_x), Length.inch(Decimal(pixels[1]) / dpi_y))
    if width is None and height is None and natural[0].emu > lw.text_width().emu:
        width = lw.text_width()  # shrink to fit, as Word does on insert
    if width is not None and height is None:
        height = Length(natural[1].emu * width.emu // natural[0].emu)
    elif height is not None and width is None:
        width = Length(natural[0].emu * height.emu // natural[1].emu)
    w, h = width or natural[0], height or natural[1]
    description = args.text("alt", "") or ""
    try:
        made = ir.Image(data, image_format, w, h, description, args.text("name") or path.name)
    except ValueError as e:
        lw.report(Code.INVALID_ARGUMENT, str(e), command.span)
        return []
    return [Piece("", fmt, "image", made)]


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
