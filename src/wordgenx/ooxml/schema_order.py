"""Child-element order of WordprocessingML types, as declared in ECMA-376 Transitional.

Word expects children in schema order, even where the Transitional schema allows any
order (run properties). Writers sort children with `ordered`. The lists are copied
from wml.xsd, and tests/ooxml/test_schema_order.py checks them against the schema.
"""

from collections.abc import Iterable, Mapping, Sequence

from wordgenx.ooxml.xml import Element

RPR: Sequence[str] = (
    "rStyle rFonts b bCs i iCs caps smallCaps strike dstrike outline shadow emboss imprint noProof "
    "snapToGrid vanish webHidden color spacing w kern position sz szCs highlight u effect bdr shd "
    "fitText vertAlign rtl cs em lang eastAsianLayout specVanish oMath"
).split()
"""EG_RPrBase (followed by rPrChange in CT_RPr)."""

PPR: Sequence[str] = (
    "pStyle keepNext keepLines pageBreakBefore framePr widowControl numPr suppressLineNumbers pBdr "
    "shd tabs suppressAutoHyphens kinsoku wordWrap overflowPunct topLinePunct autoSpaceDE "
    "autoSpaceDN bidi adjustRightInd snapToGrid spacing ind contextualSpacing mirrorIndents "
    "suppressOverlap jc textDirection textAlignment textboxTightWrap outlineLvl divId cnfStyle"
).split()
"""CT_PPrBase (followed by rPr, sectPr, pPrChange in CT_PPr)."""

STYLE: Sequence[str] = (
    "name aliases basedOn next link autoRedefine hidden uiPriority semiHidden unhideWhenUsed "
    "qFormat locked personal personalCompose personalReply rsid pPr rPr tblPr trPr tcPr tblStylePr"
).split()
"""CT_Style."""

SECT_PR: Sequence[str] = (
    "footnotePr endnotePr type pgSz pgMar paperSrc pgBorders lnNumType pgNumType cols formProt "
    "vAlign noEndnote titlePg textDirection bidi rtlGutter docGrid printerSettings"
).split()
"""EG_SectPrContents (after the header/footer references, before sectPrChange)."""

SETTINGS: Sequence[str] = (
    "writeProtection view zoom removePersonalInformation removeDateAndTime "
    "doNotDisplayPageBoundaries displayBackgroundShape printPostScriptOverText "
    "printFractionalCharacterWidth printFormsData embedTrueTypeFonts embedSystemFonts "
    "saveSubsetFonts saveFormsData mirrorMargins alignBordersAndEdges bordersDoNotSurroundHeader "
    "bordersDoNotSurroundFooter gutterAtTop hideSpellingErrors hideGrammaticalErrors "
    "activeWritingStyle proofState formsDesign attachedTemplate linkStyles stylePaneFormatFilter "
    "stylePaneSortMethod documentType mailMerge revisionView trackRevisions doNotTrackMoves "
    "doNotTrackFormatting documentProtection autoFormatOverride styleLockTheme styleLockQFSet "
    "defaultTabStop autoHyphenation consecutiveHyphenLimit hyphenationZone doNotHyphenateCaps "
    "showEnvelope summaryLength clickAndTypeStyle defaultTableStyle evenAndOddHeaders "
    "bookFoldRevPrinting bookFoldPrinting bookFoldPrintingSheets drawingGridHorizontalSpacing "
    "drawingGridVerticalSpacing displayHorizontalDrawingGridEvery "
    "displayVerticalDrawingGridEvery doNotUseMarginsForDrawingGridOrigin "
    "drawingGridHorizontalOrigin drawingGridVerticalOrigin doNotShadeFormData "
    "noPunctuationKerning characterSpacingControl printTwoOnOne strictFirstAndLastChars "
    "noLineBreaksAfter noLineBreaksBefore savePreviewPicture doNotValidateAgainstSchema "
    "saveInvalidXml ignoreMixedContent alwaysShowPlaceholderText doNotDemarcateInvalidXml "
    "saveXmlDataOnly useXSLTWhenSaving saveThroughXslt showXMLTags alwaysMergeEmptyNamespace "
    "updateFields hdrShapeDefaults footnotePr endnotePr compat docVars rsids mathPr "
    "attachedSchema themeFontLang clrSchemeMapping doNotIncludeSubdocsInStats "
    "doNotAutoCompressPictures forceUpgrade captions readModeInkLockDown smartTagType "
    "schemaLibrary shapeDefaults doNotEmbedSmartTags decimalSymbol listSeparator"
).split()
"""CT_Settings."""


def ordered(children: Mapping[str, Element], order: Sequence[str]) -> Iterable[Element]:
    """Yield `children` (keyed by local name) in schema order; unknown names are an error."""
    unknown = set(children) - set(order)
    if unknown:
        raise KeyError(f"elements not in schema order list: {sorted(unknown)}")
    return (children[name] for name in order if name in children)
