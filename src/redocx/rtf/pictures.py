r"""Pictures in RTF (`{\pict ...}`): their data as an image file Word can show, and their size.

PNG, JPEG and EMF data is a file as it is. A `\wmetafile` holds a Windows metafile without
the placeable header that WMF files start with, so the header is added from the picture's
size. A `\dibitmap` is a device-independent bitmap, which becomes a BMP file once it has a
file header.
"""

from __future__ import annotations

import io
import struct
from dataclasses import dataclass, field
from decimal import Decimal

from PIL import Image as PILImage
from PIL import UnidentifiedImageError

from redocx import ir
from redocx.values import Length

_HEX_DIGITS = frozenset(b"0123456789abcdefABCDEF")
_TWIPS_PER_INCH = 1440
_HIMETRIC_PER_INCH = 2540
_PIXELS_PER_INCH = 96


@dataclass
class Picture:
    r"""A `\pict` group being read: its control words and data."""

    kind: str | None = None
    r"""png, jpeg, emf, wmf, dib, or None (a kind redocx does not read: `\macpict`, ...)."""
    width: int = 0
    r"""`\picw`: pixels for bitmaps, 0.01 mm for metafiles."""
    height: int = 0
    width_goal: int = 0
    r"""`\picwgoal`: the width to show, in twips."""
    height_goal: int = 0
    scale_x: int = 100
    scale_y: int = 100
    crop: dict[str, int] = field(default_factory=dict[str, int])
    r"""`\piccropl`, ...: twips cut off each side, measured at the goal size."""
    hex: bytearray = field(default_factory=bytearray)
    binary: bytearray = field(default_factory=bytearray)

    def word(self, name: str, param: int | None) -> bool:
        """Take a control word of the picture; False when it is not one."""
        value = param if param is not None else 0
        if name in ("pngblip", "jpegblip", "emfblip"):
            self.kind = name.removesuffix("blip")
        elif name == "wmetafile":
            self.kind = "wmf"
        elif name == "dibitmap":
            self.kind = "dib"
        elif name in ("wbitmap", "macpict", "pmmetafile"):  # device-dependent or other platforms
            self.kind = None
        elif name == "picw":
            self.width = value
        elif name == "pich":
            self.height = value
        elif name == "picwgoal":
            self.width_goal = value
        elif name == "pichgoal":
            self.height_goal = value
        elif name == "picscalex":
            self.scale_x = value
        elif name == "picscaley":
            self.scale_y = value
        elif name in _CROPS:
            self.crop[_CROPS[name]] = max(0, value)  # negative: space around it, not kept
        else:
            return False
        return True

    def text(self, data: bytes) -> None:
        self.hex.extend(b for b in data if b in _HEX_DIGITS)

    def data(self) -> bytes:
        if self.binary:
            return bytes(self.binary)
        digits = self.hex if len(self.hex) % 2 == 0 else self.hex[:-1]
        return bytes.fromhex(digits.decode("ascii"))

    def image(self) -> ir.Image | None:
        """The picture as an inline image; None when it is not one Word can show."""
        if self.kind is None:
            return None
        data = self.data()
        if self.kind == "wmf":
            data = _placeable_wmf(data, self.width, self.height)
        elif self.kind == "dib":
            data = _bmp_file(data)
            if data is None:
                return None
        image_format = _format(data)
        if image_format is None:
            return None
        natural = self._natural(data, image_format)
        if natural is None:
            return None
        width, height = natural
        left, right = self.crop.get("left", 0), self.crop.get("right", 0)
        top, bottom = self.crop.get("top", 0), self.crop.get("bottom", 0)
        crop: ir.Crop | None = None
        if left + right or top + bottom:
            try:
                crop = ir.Crop(
                    *(round(Decimal(c) / size * 100_000) for c, size in
                      ((left, width), (top, height), (right, width), (bottom, height)))
                )  # fmt: skip
            except ValueError:  # nothing left of the picture
                return None
        shown_x = (width - left - right) * _scale(self.scale_x)
        shown_y = (height - top - bottom) * _scale(self.scale_y)
        shown = Length.inch(shown_x / _TWIPS_PER_INCH), Length.inch(shown_y / _TWIPS_PER_INCH)
        if shown[0].emu <= 0 or shown[1].emu <= 0:
            return None
        return ir.Image(data, image_format, shown[0], shown[1], crop=crop)

    def _natural(self, data: bytes, image_format: str) -> tuple[Decimal, Decimal] | None:
        """The size before cropping and scaling, in twips."""
        if self.width_goal > 0 and self.height_goal > 0:
            return Decimal(self.width_goal), Decimal(self.height_goal)
        if image_format in ("wmf", "emf") and self.width > 0 and self.height > 0:
            per_unit = Decimal(_TWIPS_PER_INCH) / _HIMETRIC_PER_INCH
            return self.width * per_unit, self.height * per_unit
        pixels = _pixels(data)
        if pixels is None or pixels[0] <= 0 or pixels[1] <= 0:
            return None
        per_pixel = Decimal(_TWIPS_PER_INCH) / _PIXELS_PER_INCH
        return pixels[0] * per_pixel, pixels[1] * per_pixel


_CROPS = {"piccropl": "left", "piccropr": "right", "piccropt": "top", "piccropb": "bottom"}


def _scale(percent: int) -> Decimal:
    return Decimal(percent if percent > 0 else 100) / 100


def _format(data: bytes) -> str | None:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith(b"\xff\xd8"):
        return "jpeg"
    if data.startswith(b"BM"):
        return "bmp"
    if data[40:44] == b" EMF":
        return "emf"
    if data.startswith(b"\xd7\xcd\xc6\x9a"):
        return "wmf"
    return None


def _pixels(data: bytes) -> tuple[int, int] | None:
    try:
        with PILImage.open(io.BytesIO(data)) as picture:
            return picture.size
    except (UnidentifiedImageError, OSError, ValueError):
        return None


def _placeable_wmf(data: bytes, width: int, height: int) -> bytes:
    """A WMF file: the Aldus placeable header (sized in 0.01 mm, the `\\picw` unit of
    metafiles), then the metafile."""
    if data.startswith(b"\xd7\xcd\xc6\x9a"):
        return data
    right, bottom = max(1, min(width, 32767)), max(1, min(height, 32767))
    words = struct.unpack("<10H", struct.pack("<IHhhhhHI", 0x9AC6CDD7, 0, 0, 0, right, bottom,
                                              _HIMETRIC_PER_INCH, 0))  # fmt: skip
    checksum = 0
    for word in words:
        checksum ^= word
    header = struct.pack("<IHhhhhHIH", 0x9AC6CDD7, 0, 0, 0, right, bottom, _HIMETRIC_PER_INCH, 0,
                         checksum)  # fmt: skip
    return header + data


def _bmp_file(dib: bytes) -> bytes | None:
    """A BMP file from a device-independent bitmap (BITMAPINFOHEADER, colour table, bits)."""
    if len(dib) < 16:
        return None
    (header_size,) = struct.unpack_from("<I", dib)
    if header_size == 12:  # BITMAPCOREHEADER
        bit_count = struct.unpack_from("<H", dib, 10)[0]
        colors = 1 << bit_count if bit_count <= 8 else 0
        table = colors * 3
    elif header_size >= 40 and len(dib) >= 40:
        bit_count, compression = struct.unpack_from("<HI", dib, 14)
        used = struct.unpack_from("<I", dib, 32)[0]
        colors = used or (1 << bit_count if bit_count <= 8 else 0)
        table = colors * 4
        if compression == 3 and header_size == 40:  # BI_BITFIELDS: three masks follow
            table += 12
    else:
        return None
    offset = 14 + header_size + table
    if offset - 14 > len(dib):
        return None
    return b"BM" + struct.pack("<IHHI", 14 + len(dib), 0, 0, offset) + dib
