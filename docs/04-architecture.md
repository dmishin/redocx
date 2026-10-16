# 4. Architecture

## 4.1 Pipeline

```
 .wgx / .wgxs sources
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

## 4.2 Package layout (partly proposed)

`syntax/`, `commands/`, `lower/`, `props/`, `std/`, `stylesheet/`, `ooxml/` and `math/` exist; the others are
the plan.

**Commands.** Each command is declared once, in `commands/builtin.py`: where it may appear (block, inline, either,
or only inside another command), its options with their value types, and how its body is read. The parser takes
body kinds from it. Lowering binds each command against its spec before it gives the command meaning (`bind`),
so every command reads options the same way and reports the same errors. A handler receives typed, checked
arguments and does only the semantic part. The schema does not depend on lowering, so documents can later be
checked without compiling them, and documentation and editor completion can be generated from it.

```
src/wordgenx/
  syntax/        lexer, parser, CST, AST, source map, pretty-printer (formatter)
  commands/      COMMAND SCHEMA: every command's placement, options (typed) and body kind (builtin.py);
                 value types (types.py); binding a parsed command to typed arguments (bind.py)
  lower/         AST → IR: the core pass (lowering.py: blocks, macros, dispatch, run assembly) and one
                 module per command family (text, code, preamble, sections, tables, fields, xrefs,
                 media, equations, citations, index); resolve passes (references, citations, index)
  std/           standard macro library (.wgx files: theorem, callout, subfigure, signature)
  props/         PROPERTY REGISTRY: name, type, units, OOXML target, docs — single source of truth
  ir/            IR node dataclasses, JSON (de)serialization
  resolve/       styles, numbering, references, fields, sections, ids
  ooxml/         package (OPC) writer, namespaces, element builders, part writers
    parts/       document.py, styles.py, numbering.py, settings.py, notes.py, comments.py, …
  template/      read .dotx/.docx; extract & merge styles, numbering, theme, settings, headers
  math/          LaTeX-subset parser → OMML
  highlight/     Pygments → runs + generated code styles
  diagnostics/   error codes, rendering
  cli/           `wordgenx build`, `wordgenx watch`, `wordgenx check`, `wordgenx fmt`, `wordgenx finalize`
  api/           public Python builder API over the IR
tests/
  golden/        .wgx input → canonicalized XML snapshots
  features/      one gallery doc per feature, for manual Word review
tools/
  ooxml-validate/   tiny .NET console app wrapping OpenXmlValidator (CI only)
  word-probe/       scripts to create a feature in Word and diff its XML (reverse-engineering aid)
schemas/         ECMA-376 Transitional XSDs (for lxml validation)
docs/            design docs → later: user manual written in wordgenx
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
- IR is serializable to JSON: `wordgenx build doc.wgx --emit-ir doc.json` (`-` for standard output) writes each
  object as `{"type": "Paragraph", …}` with the fields that differ from their defaults, lengths and colours as in
  the language (`12pt`, `#1F4E79`), and pictures and embedded documents as their size and SHA-256. It is one-way,
  for debugging and other tools; reading it back (for alternate frontends) is not done.

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

`wordgenx build doc.wgx --template corp.dotx` (or `\document[template=…]` in the preamble; the command line wins).
Implemented in M2 (`ooxml/template.py`, `ooxml/merge.py`):

1. **Read the template** (`load_template`). Its styles, numbering, settings, font table and theme are kept as XML. For
   lookups, its styles and numbering are also read into the IR (best effort), with the final section's page setup.
2. **Page setup:** the template's final section (size, margins, columns, title page) is the starting point of the
   document's first section. `\section` changes it as usual.
3. **Styles:** the template's `styles.xml` is the base.
   - Template styles can be used without being defined.
   - A style-sheet style the template already has is merged into it: our property elements replace the template's
     (`w:spacing` and `w:ind` merge attribute by attribute), while its name, base, links and gallery settings stay.
     `replace` and a new `: BASE` therefore act like `modify` for template styles.
   - Built-ins the markup relies on (Strong, Caption, TOC 1–9, …) are added only when the template lacks them.
   - Template styles are matched by name, so localized templates work (Word's German "heading 1" has the ID
     `berschrift1`): our style references are rewritten to the template's IDs.
4. **Numbering:** the template's lists keep their IDs, so its styles' and body's numbering stays valid. The document's
   definitions are appended after them. `\list[Name]{...}` can use a list definition only the template has (named by
   its `w:abstractNum`'s `w:name`; a style sheet's definition of the same name wins, the template's wins over the
   `-` / `1.` defaults). The definition is not copied: each list gets a new `w:num` (with its `restart=` start)
   after the template's own, pointing at the template's `w:abstractNum`.
5. **Settings:** the template's, with what the document sets (field updates, even/odd headers, note settings, default
   tab stop, language). The theme is the template's, and its colours resolve `theme.*` colours.
6. **Headers and footers:** each kind (default, first, even) the first section does not define comes from the
   template. Later sections inherit them, as in Word.
7. **Body content** (a cover page, boilerplate) comes before the document's, as in a new document Word creates from
   the template. Relationships, drawing IDs and bookmark IDs are renumbered. A body that refers to footnotes,
   comments or tracked changes is not carried over (warning `WGX0310`).
8. **Other parts** of the main document (glossary/building blocks, customXml, web settings) and custom document
   properties are copied with everything they relate to.
9. **Output:** `.docx`, or a template (`-o out.dotx`: the main part gets the template content type).

Not yet: a template's own footnote separators.

## 4.7 Finalize step (optional)

Word computes page numbers, TOC pages and `PAGEREF`. `wordgenx finalize out.docx` (or `wordgenx build --finalize`) can:

- **Windows + Word installed:** COM automation: open, `Fields.Update`, update TOCs, save. Gives the most faithful result.
- **LibreOffice headless:** a UNO macro that updates indexes and fields. Portable, slightly less faithful.
- Neither available: leave `w:updateFields=true` so Word refreshes on first open.

The same mechanism gives `--pdf` export.

## 4.8 Public Python API (sketch)

```python
from wordgenx import Document, para, table, styles as S

doc = Document(template="corp.dotx")
doc.heading(1, "Quarterly report", label="sec:q")
doc.add(para("Revenue grew ", S.strong("12%"), " year over year."))
doc.add(table(df, style="Data Grid", header_rows=1))     # pandas DataFrame accepted
doc.include("appendix.wgx", vars={"quarter": "Q3"})       # mix source and API
doc.save("report.docx")
```

The API builds the **same IR** the language produces. The language is a frontend to the API, not a separate path.

## 4.9 Extensibility

- **Command plugins:** Python entry points register new commands with an option schema and an IR builder.
- **Post-IR transforms:** hooks run between Resolve and Emit (for example, auto-linking glossary terms).
- Plugins are trusted Python code. Untrusted `.wgx` sources cannot run code: there is no scripting, and `\include` and `\image`
  are restricted to the project root by default.
