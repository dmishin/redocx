"""RTF -> document IR: the reader behind `redocx import x.rtf` and `\\include-rtf`."""

from redocx.rtf.reader import NotAnRtfError, is_rtf, read_rtf

__all__ = ["NotAnRtfError", "is_rtf", "read_rtf"]
