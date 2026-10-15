# 4. Architecture

## 4.1 Pipeline

```
 .wex / .wexs sources
        │
        ▼
 ┌──────────────┐   tokens/modes: markup, command, math, code, raw, stylesheet
 │ 1. Parse     │   hand-written recursive descent, lossless CST (keeps trivia for formatter/LSP)
 └──────┬───────┘
        ▼  Syntax tree (CST → AST)
 ┌──────────────┐   includes, \let, \define, sugar → canonical commands
 │ 2. Expand    │
 └──────┬───────┘
        ▼  Canonical AST
 ┌──────────────┐   command registry: name → option schema → IR builder
 │ 3. Lower     │   option type-checking, unit/color parsing
 └──────┬───────┘
        ▼  Document IR (Word-shaped, unit-normalized)
 ┌──────────────┐   template merge, style resolution, numbering instances,
 │ 4. Resolve   │   labels → bookmarks, SEQ/ref cached text, ID allocation,
 └──────┬───────┘   section splitting, validation of semantic rules
        ▼  Resolved IR
 ┌──────────────┐   one writer per part: document, styles, numbering, settings,
 │ 5. Emit      │   footnotes, endnotes, comments, headers/footers, rels,
 └──────┬───────┘   content types, core/app/custom props, media, theme, fontTable
        ▼  OPC package (deterministic zip)
 ┌──────────────┐   XSD validation (always in tests; opt-in --validate on CLI)
 │ 6. Validate  │   optional: finalize (update fields via Word COM / LibreOffice)
 └──────────────┘
```

Each stage is a pure function from input to output plus diagnostics. Stages communicate only through data structures.
Each can be tested on its own and is accessible from the Python API.

## 4.2 Package layout (proposed)

```
src/wex/
  syntax/        lexer, parser, CST, AST, source map, pretty-printer (formatter)
  expand/        includes, macros, desugaring
  commands/      command registry; one module per family (text, para, table, section, refs, math, …)
  props/         PROPERTY REGISTRY: name, type, units, OOXML target, docs — single source of truth
  ir/            IR node dataclasses, JSON (de)serialization
  resolve/       styles, numbering, references, fields, sections, ids
  ooxml/         package (OPC) writer, namespaces, element builders, part writers
    parts/       document.py, styles.py, numbering.py, settings.py, notes.py, comments.py, …
  template/      read .dotx/.docx; extract & merge styles, numbering, theme, settings, headers
  math/          LaTeX-subset parser → OMML
  highlight/     Pygments → runs + generated code styles
  diagnostics/   error codes, rendering
  cli/           `wex build`, `wex watch`, `wex check`, `wex fmt`, `wex finalize`
  api/           public Python builder API over the IR
tests/
  golden/        .wex input → canonicalized XML snapshots
  features/      one gallery doc per feature, for manual Word review
tools/
  ooxml-validate/   tiny .NET console app wrapping OpenXmlValidator (CI only)
  word-probe/       scripts to create a feature in Word and diff its XML (reverse-engineering aid)
schemas/         ECMA-376 Transitional XSDs (for lxml validation)
docs/            design docs → later: user manual written in WEX
```

## 4.3 The IR

This is the most important design artifact. It **mirrors WordprocessingML concepts** but with normalized units,
resolved references and no XML details.

```
Document
 ├─ meta: CoreProps, AppProps, CustomProps
 ├─ settings: Settings (compat, updateFields, evenAndOddHeaders, footnotePr, …)
 ├─ theme: Theme | None (inherited from template)
 ├─ styles: StyleSheet (docDefaults, latentStyles, [ParagraphStyle|CharacterStyle|TableStyle|NumberingStyle])
 ├─ numbering: [AbstractNumbering], [NumberingInstance]
 ├─ sections: [Section]
 │    ├─ page: PageSetup (size, margins, columns, numbering fmt/start, borders, lineNumbers…)
 │    ├─ headers/footers: {default, first, even} → [Block]
 │    └─ body: [Block]
 ├─ footnotes / endnotes: {id → [Block]}
 ├─ comments: {id → Comment}
 └─ media: {id → MediaItem}

Block   = Paragraph | Table | ContentControl(Block) | RawBlock | AltChunk
Paragraph(style, props: ParaProps, content: [Inline])
Inline  = Run(text, props: RunProps) | Break | Tab | FieldNode(instr, result:[Inline], children)
        | Hyperlink | BookmarkStart/End | NoteRef | CommentRange | Drawing | Math(OMML tree)
        | ContentControl(Inline) | Revision(ins|del, author, date, [Inline]) | RawInline | SymbolChar
Table(style, props: TableProps, grid:[Length], rows:[Row(props, cells:[Cell(props, [Block])])])
```

Rules:

- **Property bags are typed** (`RunProps`, `ParaProps`, `TableProps`, `CellProps`, `SectionProps`). Each field is
  defined once in the **property registry**. The registry also generates emitters, documentation and LSP completion.
  Adding a Word property should mean adding one registry entry plus tests.
- Units are normalized at lowering: lengths in twips (or EMUs for drawings), font sizes in half-points.
- Merged table cells are represented logically (colspan, rowspan) and converted to `gridSpan` / `vMerge` at emit.
- IR is serializable to JSON. This enables `wex build --emit-ir`, debugging and alternate frontends.

## 4.4 Resolve phase responsibilities

| Concern | What happens |
|---------|--------------|
| **Template merge** | Load template's `styles.xml`, `numbering.xml`, `theme`, `settings`, headers and footers. User style definitions modify or extend these. Conflicts produce warnings. Style IDs and names are mapped correctly, including localized built-in names. |
| **Style closure** | Make sure every referenced style exists. Auto-create built-ins we rely on (`Strong`, `Caption`, `TOC 1..9`, `Hyperlink`, `Footnote Text`, …) from Word's defaults if the template lacks them. |
| **Numbering** | Assign `abstractNumId` and `numId`. Create a new `num` with `startOverride` for restarts. Link heading styles to numbering. |
| **Counters** | Simulate Word's numbering (headings, list items, SEQ captions, footnotes) to compute **cached text** for references and fields. |
| **References** | Turn labels into unique bookmark names (`_Ref…`, max 40 chars, valid chars). Check that every reference resolves. |
| **IDs** | Allocate deterministic IDs: relationship IDs, `w14:paraId`, docPr IDs, footnote, comment and bookmark IDs. |
| **Sections** | Each section's `sectPr` goes on its last paragraph, and the final one on the body. Inherit header and footer links. |
| **TOC** | Pre-render TOC entries (text plus `PAGEREF` placeholders) inside the TOC field result. Set `updateFields`. |

## 4.5 OOXML emit

- Built with `lxml.etree` element factories. A small internal DSL (`w.p(w.pPr(...), w.r(...))`) handles namespace
  declarations once per part, including `mc:Ignorable` for `w14` and `w15` extensions.
- **Element order matters** in OOXML (schema sequences). Property emitters write children in schema order. This is
  enforced by tables generated from the XSD, not by hand.
- **Determinism:** fixed zip entry order and timestamps, stable IDs, no wall-clock values unless `\date[fixed]` or
  metadata asks for them. `SOURCE_DATE_EPOCH` is respected.
- Default parts are produced from embedded minimal templates (a clean default theme, fontTable and settings). We do not
  depend on a Word-created blank document whose licensing is unclear.

## 4.6 Templates

`wex build doc.wex --template corp.dotx` (or `\document[template=…]`):

1. Read the template package and keep all parts we don't manage, such as theme, fonts, glossary and customXml.
2. Replace `document.xml` body content with ours. Keep the template's final `sectPr` as the base page setup unless overridden.
3. Merge styles and numbering (see resolve).
4. Write the result as `.docx` (`.dotx` content type changed to document).

## 4.7 Finalize step (optional)

Word computes page numbers, TOC pages and `PAGEREF`. `wex finalize out.docx` (or `wex build --finalize`) can:

- **Windows + Word installed:** COM automation: open, `Fields.Update`, update TOCs, save. Gives the most faithful result.
- **LibreOffice headless:** a UNO macro that updates indexes and fields. Portable, slightly less faithful.
- Neither available: leave `w:updateFields=true` so Word refreshes on first open.

The same mechanism gives `--pdf` export.

## 4.8 Public Python API (sketch)

```python
from wex import Document, para, table, styles as S

doc = Document(template="corp.dotx")
doc.heading(1, "Quarterly report", label="sec:q")
doc.add(para("Revenue grew ", S.strong("12%"), " year over year."))
doc.add(table(df, style="Data Grid", header_rows=1))     # pandas DataFrame accepted
doc.include("appendix.wex", vars={"quarter": "Q3"})       # mix source and API
doc.save("report.docx")
```

The API builds the **same IR** the language produces. The language is a frontend to the API, not a separate path.

## 4.9 Extensibility

- **Command plugins:** Python entry points register new commands with an option schema and an IR builder.
- **Post-IR transforms:** hooks run between Resolve and Emit (for example, auto-linking glossary terms).
- Plugins are trusted Python code. Untrusted `.wex` sources cannot run code: there is no scripting, and `\include` and `\image`
  are restricted to the project root by default.
