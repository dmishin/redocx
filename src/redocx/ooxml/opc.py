"""Open Packaging Conventions writer (ECMA-376 Part 2).

Builds a package in memory (parts, relationships, content types) and writes it
as a deterministic ZIP: the same parts always produce byte-identical output.
"""

from __future__ import annotations

import posixpath
import re
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass
from io import BytesIO
from os import PathLike
from xml.sax.saxutils import quoteattr

from redocx.ooxml.constants import CT, NS

CONTENT_TYPES_NAME = "/[Content_Types].xml"
PACKAGE_RELS_NAME = "/_rels/.rels"

XML_DECLARATION = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\r\n'

# Extensions written as <Default> entries when some part with that extension has
# the listed content type. Any part whose type differs gets an <Override>.
_DEFAULT_CONTENT_TYPES: dict[str, str] = {
    "rels": CT.RELATIONSHIPS,
    "xml": CT.XML,
    "png": CT.PNG,
    "jpeg": CT.JPEG,
    "jpg": CT.JPEG,
    "gif": CT.GIF,
    "bmp": CT.BMP,
    "tif": CT.TIFF,
    "tiff": CT.TIFF,
    "svg": CT.SVG,
    "emf": CT.EMF,
    "wmf": CT.WMF,
    "rtf": CT.RTF,
}

# Fixed ZIP metadata so output does not depend on the clock or the host OS.
_ZIP_EPOCH = (1980, 1, 1, 0, 0, 0)
_ZIP_COMPRESS_LEVEL = 6

# A restrictive subset of the OPC part-name grammar: unreserved URI characters only.
_SEGMENT_RE = re.compile(r"[A-Za-z0-9._~\-\[\]]+")


class PartNameError(ValueError):
    """An invalid or duplicate part name."""


def validate_part_name(name: str) -> None:
    """Raise PartNameError unless `name` is a valid absolute OPC part name."""
    if not name.startswith("/") or name.endswith("/"):
        raise PartNameError(f"part name must start and not end with '/': {name!r}")
    for segment in name[1:].split("/"):
        if not segment:
            raise PartNameError(f"empty segment in part name: {name!r}")
        if segment.endswith("."):
            raise PartNameError(f"segment ends with '.': {name!r}")
        if not _SEGMENT_RE.fullmatch(segment):
            raise PartNameError(f"unsupported characters in part name: {name!r}")


def rels_part_name(source: str | None) -> str:
    """Name of the relationships part for `source` (None means the package itself)."""
    if source is None:
        return PACKAGE_RELS_NAME
    directory, filename = posixpath.split(source)
    return posixpath.join(directory, "_rels", f"{filename}.rels")


@dataclass(frozen=True)
class Relationship:
    id: str
    type: str
    target: str
    """Relative URI of an internal part, or the absolute URI of an external target."""
    external: bool = False


class _Relationships:
    """The ordered relationships of one source (a part or the package)."""

    def __init__(self, source: str | None) -> None:
        self._source = source
        self._items: list[Relationship] = []

    def __iter__(self) -> Iterator[Relationship]:
        return iter(self._items)

    def __len__(self) -> int:
        return len(self._items)

    def add_internal(self, target: Part, reltype: str, rel_id: str | None = None) -> str:
        base = "/" if self._source is None else posixpath.dirname(self._source)
        return self._add(reltype, posixpath.relpath(target.name, base), False, rel_id)

    def add_external(self, url: str, reltype: str, rel_id: str | None = None) -> str:
        return self._add(reltype, url, True, rel_id)

    def _add(self, reltype: str, target: str, external: bool, rel_id: str | None) -> str:
        taken = {rel.id for rel in self._items}
        if rel_id is None:
            number = len(self._items) + 1
            while f"rId{number}" in taken:
                number += 1
            rel_id = f"rId{number}"
        elif rel_id in taken:
            raise ValueError(f"duplicate relationship id {rel_id!r}")
        self._items.append(Relationship(rel_id, reltype, target, external))
        return rel_id

    def to_xml(self) -> bytes:
        lines = [XML_DECLARATION, f"<Relationships xmlns={quoteattr(NS.RELATIONSHIPS)}>"]
        for rel in self._items:
            mode = ' TargetMode="External"' if rel.external else ""
            lines.append(
                f"<Relationship Id={quoteattr(rel.id)} Type={quoteattr(rel.type)}"
                f" Target={quoteattr(rel.target)}{mode}/>"
            )
        lines.append("</Relationships>")
        return "".join(lines).encode("utf-8")


class Part:
    """A part of the package. `data` may be set or replaced until the package is written."""

    def __init__(self, name: str, content_type: str, data: bytes = b"") -> None:
        self.name = name
        self.content_type = content_type
        self.data = data
        self.rels = _Relationships(name)

    def relate(self, target: Part, reltype: str, rel_id: str | None = None) -> str:
        """Add a relationship from this part to `target`; return its id (rId1, rId2, ...,
        or `rel_id` when given, as when copying a part with its relationships)."""
        return self.rels.add_internal(target, reltype, rel_id)

    def relate_external(self, url: str, reltype: str, rel_id: str | None = None) -> str:
        """Add a relationship to an external URI (e.g. a hyperlink); return its id."""
        return self.rels.add_external(url, reltype, rel_id)

    def __repr__(self) -> str:
        return f"Part({self.name!r}, {self.content_type!r}, {len(self.data)} bytes)"


class Package:
    """An OPC package under construction."""

    def __init__(self) -> None:
        self._parts: dict[str, Part] = {}  # keyed by lower-cased name
        self.rels = _Relationships(None)

    def add_part(self, name: str, content_type: str, data: bytes = b"") -> Part:
        validate_part_name(name)
        key = name.lower()
        if key == CONTENT_TYPES_NAME.lower() or key.endswith(".rels"):
            raise PartNameError(f"{name!r} is generated by the package writer")
        if key in self._parts:
            raise PartNameError(f"duplicate part name (names are case-insensitive): {name!r}")
        part = Part(name, content_type, data)
        self._parts[key] = part
        return part

    def relate(self, target: Part, reltype: str) -> str:
        """Add a package-level relationship (e.g. officeDocument) to `target`."""
        return self.rels.add_internal(target, reltype)

    def part(self, name: str) -> Part:
        return self._parts[name.lower()]

    def has_part(self, name: str) -> bool:
        return name.lower() in self._parts

    @property
    def parts(self) -> list[Part]:
        return sorted(self._parts.values(), key=lambda p: p.name)

    def content_types_xml(self) -> bytes:
        defaults: dict[str, str] = {"rels": CT.RELATIONSHIPS, "xml": CT.XML}
        for part in self.parts:
            ext = _extension(part.name)
            if _DEFAULT_CONTENT_TYPES.get(ext) == part.content_type:
                defaults[ext] = part.content_type
        overrides = {
            part.name: part.content_type
            for part in self.parts
            if defaults.get(_extension(part.name)) != part.content_type
        }

        lines = [XML_DECLARATION, f"<Types xmlns={quoteattr(NS.CONTENT_TYPES)}>"]
        for ext in sorted(defaults):
            lines.append(
                f"<Default Extension={quoteattr(ext)} ContentType={quoteattr(defaults[ext])}/>"
            )
        for name in sorted(overrides):
            lines.append(
                f"<Override PartName={quoteattr(name)} ContentType={quoteattr(overrides[name])}/>"
            )
        lines.append("</Types>")
        return "".join(lines).encode("utf-8")

    def entries(self) -> list[tuple[str, bytes]]:
        """All ZIP entries as (part name, data), in write order."""
        files: dict[str, bytes] = {}
        if len(self.rels):
            files[PACKAGE_RELS_NAME] = self.rels.to_xml()
        for part in self.parts:
            files[part.name] = part.data
            if len(part.rels):
                files[rels_part_name(part.name)] = part.rels.to_xml()
        ordered = [(CONTENT_TYPES_NAME, self.content_types_xml())]
        ordered.extend(sorted(files.items()))
        return ordered

    def to_bytes(self) -> bytes:
        buffer = BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            for name, data in self.entries():
                info = zipfile.ZipInfo(name.lstrip("/"), date_time=_ZIP_EPOCH)
                info.compress_type = zipfile.ZIP_DEFLATED
                info.create_system = 0  # identical bytes on Windows and POSIX
                info.external_attr = 0
                archive.writestr(info, data, compresslevel=_ZIP_COMPRESS_LEVEL)
        return buffer.getvalue()

    def save(self, path: str | PathLike[str]) -> None:
        with open(path, "wb") as f:
            f.write(self.to_bytes())


def _extension(name: str) -> str:
    return posixpath.splitext(name)[1].lstrip(".").lower()
