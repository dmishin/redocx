"""Document property parts: core (docProps/core.xml), extended (docProps/app.xml) and
custom (docProps/custom.xml)."""

from __future__ import annotations

import copy
from collections.abc import Sequence
from datetime import UTC, datetime

from lxml import etree

from wordgenx.ir import CustomValue, Meta
from wordgenx.ooxml.xml import PREFIXES, E, Element, nsmap

CUSTOM_NS = "http://schemas.openxmlformats.org/officeDocument/2006/custom-properties"
CUSTOM_FMTID = "{D5CDD505-2E9C-101B-9397-08002B2CF9AE}"
"""The format id Word gives every user-defined property."""


def _w3cdtf(moment: datetime) -> str:
    if moment.tzinfo is None:
        raise ValueError(f"document timestamps must be timezone-aware: {moment!r}")
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def core_xml(meta: Meta) -> Element:
    def text(tag: str, value: str | None) -> Element | None:
        return E(tag, text=value) if value else None

    def date(tag: str, value: datetime | None) -> Element | None:
        if value is None:
            return None
        return E(tag, {"xsi:type": "dcterms:W3CDTF"}, text=_w3cdtf(value))

    return E(
        "cp:coreProperties",
        text("dc:title", meta.title),
        text("dc:subject", meta.subject),
        text("dc:creator", meta.author),
        text("cp:keywords", ", ".join(meta.keywords) if meta.keywords else None),
        text("dc:description", meta.description),
        text("cp:lastModifiedBy", meta.last_modified_by),
        date("dcterms:created", meta.created),
        date("dcterms:modified", meta.modified),
        text("dc:language", meta.language),
        nsmap=nsmap("cp", "dc", "dcterms", "xsi"),
    )


def _vt_value(value: CustomValue) -> Element:
    if isinstance(value, bool):
        return E("vt:bool", text="true" if value else "false")
    if isinstance(value, int):
        return E("vt:i4", text=str(value))
    if isinstance(value, float):
        return E("vt:r8", text=repr(value))
    return E("vt:lpwstr", text=value)


def custom_xml(
    properties: Sequence[tuple[str, CustomValue]], base: Element | None = None
) -> Element:
    """The custom properties part; `base` is a template's, whose other properties are kept
    (ours replace those of the same name)."""
    ours = dict(properties)
    root = etree.Element(
        f"{{{CUSTOM_NS}}}Properties", nsmap={None: CUSTOM_NS, "vt": PREFIXES["vt"]}
    )
    entries: list[tuple[str, Element]] = []
    if base is not None:
        for prop in base:
            name = prop.get("name")
            if name is not None and name not in ours and len(prop):
                entries.append((name, prop[0]))
    entries += [(name, _vt_value(value)) for name, value in properties]
    for pid, (name, value) in enumerate(entries, start=2):  # pid 0 and 1 are reserved
        prop = etree.SubElement(
            root, f"{{{CUSTOM_NS}}}property", fmtid=CUSTOM_FMTID, pid=str(pid), name=name
        )
        prop.append(copy.deepcopy(value))
    return root


def app_xml() -> Element:
    # The extended-properties namespace is the default namespace in this part.
    return E(
        "ep:Properties",
        E("ep:Application", text="wordgenx"),
        E("ep:DocSecurity", text="0"),
        nsmap={None: PREFIXES["ep"], "vt": PREFIXES["vt"]},
    )
