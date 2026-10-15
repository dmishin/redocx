# Schemas

XML Schemas used by the test suite to validate every generated part. They are not needed at runtime.

| Folder | Source |
|--------|--------|
| `ecma-376-transitional/` | ECMA-376 5th edition, Part 4 (Transitional Migration Features), `OfficeOpenXML-XMLSchema-Transitional.zip`. The SpreadsheetML and PresentationML schemas (`sml.xsd`, `pml.xsd`) are omitted. |
| `opc/` | ECMA-376 5th edition, Part 2 (Open Packaging Conventions), `OpenPackagingConventions-XMLSchema.zip`: content types, relationships, core properties. |
| `w3c/xml.xsd` | <https://www.w3.org/2001/xml.xsd> (the `xml:` namespace) |
| `dublin-core/` | **Written for wordgenx.** Minimal stand-ins for the Dublin Core schemas referenced by OPC core properties, so validation never needs the network. |
| `wordgenx.xsd` | **Written for wordgenx.** Driver schema importing all of the above with local locations. |

The ECMA-376 schemas are © Ecma International, downloaded from
<https://ecma-international.org/publications-and-standards/standards/ecma-376/> and redistributed unmodified under Ecma's
copyright policy, which permits copying and distribution provided the copyright notice is retained. The W3C `xml.xsd` is
© W3C and is distributed under the W3C Document License.
