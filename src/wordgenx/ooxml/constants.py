"""Namespace, content-type and relationship-type URIs (ECMA-376, Transitional)."""

from typing import Final


class NS:
    """XML namespace URIs."""

    CONTENT_TYPES: Final = "http://schemas.openxmlformats.org/package/2006/content-types"
    RELATIONSHIPS: Final = "http://schemas.openxmlformats.org/package/2006/relationships"
    W: Final = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    R: Final = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    CP: Final = "http://schemas.openxmlformats.org/package/2006/metadata/core-properties"
    DC: Final = "http://purl.org/dc/elements/1.1/"
    DCTERMS: Final = "http://purl.org/dc/terms/"
    XSI: Final = "http://www.w3.org/2001/XMLSchema-instance"


class CT:
    """Content types."""

    RELATIONSHIPS: Final = "application/vnd.openxmlformats-package.relationships+xml"
    XML: Final = "application/xml"
    CORE_PROPERTIES: Final = "application/vnd.openxmlformats-package.core-properties+xml"
    EXTENDED_PROPERTIES: Final = (
        "application/vnd.openxmlformats-officedocument.extended-properties+xml"
    )
    CUSTOM_PROPERTIES: Final = "application/vnd.openxmlformats-officedocument.custom-properties+xml"
    THEME: Final = "application/vnd.openxmlformats-officedocument.theme+xml"

    _WML: Final = "application/vnd.openxmlformats-officedocument.wordprocessingml"
    WML_DOCUMENT_MAIN: Final = f"{_WML}.document.main+xml"
    WML_TEMPLATE_MAIN: Final = f"{_WML}.template.main+xml"
    WML_STYLES: Final = f"{_WML}.styles+xml"
    WML_NUMBERING: Final = f"{_WML}.numbering+xml"
    WML_SETTINGS: Final = f"{_WML}.settings+xml"
    WML_WEB_SETTINGS: Final = f"{_WML}.webSettings+xml"
    WML_FONT_TABLE: Final = f"{_WML}.fontTable+xml"
    WML_FOOTNOTES: Final = f"{_WML}.footnotes+xml"
    WML_ENDNOTES: Final = f"{_WML}.endnotes+xml"
    WML_COMMENTS: Final = f"{_WML}.comments+xml"
    WML_HEADER: Final = f"{_WML}.header+xml"
    WML_FOOTER: Final = f"{_WML}.footer+xml"

    PNG: Final = "image/png"
    JPEG: Final = "image/jpeg"
    GIF: Final = "image/gif"
    BMP: Final = "image/bmp"
    TIFF: Final = "image/tiff"
    SVG: Final = "image/svg+xml"
    EMF: Final = "image/x-emf"
    WMF: Final = "image/x-wmf"


class RT:
    """Relationship types."""

    _OD: Final = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    OFFICE_DOCUMENT: Final = f"{_OD}/officeDocument"
    CORE_PROPERTIES: Final = (
        "http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties"
    )
    EXTENDED_PROPERTIES: Final = f"{_OD}/extended-properties"
    CUSTOM_PROPERTIES: Final = f"{_OD}/custom-properties"
    STYLES: Final = f"{_OD}/styles"
    NUMBERING: Final = f"{_OD}/numbering"
    SETTINGS: Final = f"{_OD}/settings"
    WEB_SETTINGS: Final = f"{_OD}/webSettings"
    FONT_TABLE: Final = f"{_OD}/fontTable"
    THEME: Final = f"{_OD}/theme"
    FOOTNOTES: Final = f"{_OD}/footnotes"
    ENDNOTES: Final = f"{_OD}/endnotes"
    COMMENTS: Final = f"{_OD}/comments"
    HEADER: Final = f"{_OD}/header"
    FOOTER: Final = f"{_OD}/footer"
    IMAGE: Final = f"{_OD}/image"
    HYPERLINK: Final = f"{_OD}/hyperlink"
