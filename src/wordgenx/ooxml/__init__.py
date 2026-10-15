"""Office Open XML output: packaging (OPC) and, later, WordprocessingML part writers."""

from wordgenx.ooxml.constants import CT, NS, RT
from wordgenx.ooxml.opc import Package, Part, PartNameError, Relationship

__all__ = ["CT", "NS", "RT", "Package", "Part", "PartNameError", "Relationship"]
