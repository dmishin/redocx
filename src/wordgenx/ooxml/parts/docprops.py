"""Document property parts: core (docProps/core.xml) and extended (docProps/app.xml)."""

from __future__ import annotations

from datetime import UTC, datetime

from wordgenx.ir import Meta
from wordgenx.ooxml.xml import PREFIXES, E, Element, nsmap


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


def app_xml() -> Element:
    # The extended-properties namespace is the default namespace in this part.
    return E(
        "ep:Properties",
        E("ep:Application", text="wordgenx"),
        E("ep:DocSecurity", text="0"),
        nsmap={None: PREFIXES["ep"], "vt": PREFIXES["vt"]},
    )
