# 1. Vision & Scope

## 1.1 The problem

People who must deliver Word documents (specifications, contracts, reports, theses, regulatory submissions,
proposals) have two poor options:

1. **Author in Word.** It is WYSIWYG, not reproducible, hard to diff or review in Git, hard to generate from data,
   and formatting drifts over time.
2. **Author in Markdown, LaTeX or AsciiDoc and convert with Pandoc.** It is reproducible, but the output is a lowest common
   denominator. There are no section-specific headers, no table cell merges (from Markdown), no real field-based
   cross-references, no captions as `SEQ` fields, limited control of numbering, no content controls, no tracked changes, and
   styling is limited to a `reference.docx`.

Nothing treats **DOCX as a first-class compilation target** with full access to Word's feature set.

## 1.2 Vision

A plain-text language and compiler where:

- **Everything Word can represent can be expressed**, ergonomically for common cases and through escape hatches for
  the rest.
- **The output is idiomatic Word.** It uses real named styles, real numbering definitions, real fields (`TOC`, `REF`, `PAGEREF`,
  `SEQ`), real footnotes and real bookmarks, not direct-formatting soup. A colleague who opens the file in Word sees
  a cleanly built document and can continue editing it.
- **Corporate templates work.** Compile *into* an existing `.dotx` or `.docx` and inherit its styles, theme, headers and
  numbering.
- **It is programmable.** It has macros and includes in the language, plus a public Python API that builds the same
  document model, so reports can be generated from data.
- **Errors are good.** Messages carry file, line and column, explain what went wrong and suggest a fix.

## 1.3 Design principles

1. **Designed for DOCX, and only DOCX.** wordgenx is not a general-purpose or output-neutral markup language. Its
   concepts are named after Word (paragraph style, character style, section, field, tab stop), not HTML or CSS, and
   are shaped the way WordprocessingML models them. We add no abstractions to make sources portable to other formats.
   Users who know Word should recognise everything.
2. **Idiomatic Word output.** We generate what a skilled Word user would build: named styles rather than direct
   formatting, real multilevel numbering, real fields, captions and bookmarks. When choosing how to emit a feature,
   we prefer the representation Word itself writes.
3. **Semantic first, direct formatting allowed.** Styles are the main path. Direct formatting (`\font[size=14pt]{…}`)
   is available because real documents need it.
4. **Layered sugar.** Lightweight markup (`*bold*`, `= Heading`, lists, pipe tables) desugars to commands. Every command maps
   to a documented node of the intermediate representation (IR). Every IR property maps to OOXML. You can drop down a level at any point,
   down to raw OOXML.
5. **No silent loss.** If a construct cannot be represented, compilation fails or warns. It never quietly drops content.
6. **Valid by construction.** Output validates against the ECMA-376 schemas and opens in Word with no repair prompt. This
   is a release-blocking invariant.
7. **Deterministic output.** The same input gives a byte-identical `.docx` (stable IDs, fixed zip timestamps). This lets
   golden tests and reproducible builds work.

## 1.4 Non-goals (at least initially)

- **We do not lay out pages.** Word is the layout engine. We do not compute line breaks, page breaks or page numbers.
  This is the biggest conceptual difference from TeX. See §1.6.
- We are not a WYSIWYG editor.
- We do not imitate TeX syntax or semantics. There are no catcodes and no expansion-order puzzles.
- We do not convert *from* arbitrary DOCX with full fidelity. A `docx → wordgenx` importer is a later, best-effort tool.
- We do not support legacy `.doc`, ODT output or PDF typesetting. PDF comes from Word or LibreOffice converting our DOCX.
- We do not support SmartArt, OLE embedding, VBA or macros.

## 1.5 Prior art and positioning

| Tool | DOCX output | What it lacks for our goal |
|------|-------------|----------------------------|
| **Pandoc** (Markdown, LaTeX → DOCX) | Yes | Styling only through reference.docx; one section; weak tables; cross-references need filters; no fields, content controls or comments authoring |
| **Quarto** | Through Pandoc | Same limits as Pandoc |
| **Typst** | No (PDF/HTML/SVG) | Good inspiration for language ergonomics (markup plus scripting, good errors) |
| **LaTeX** | Only through converters (tex4ht, pandoc) | Poor DOCX fidelity |
| **AsciiDoc / DocBook** | Through DocBook XSL or Pandoc | Same lowest-common-denominator problem |
| **python-docx, docx4j, Open XML SDK, docx (JS)** | Yes | Libraries, not languages. They are building blocks. |
| **docxtemplater, python-docx-template** | Yes | Fill templates; they don't author documents |

**Positioning:** "Typst-like authoring ergonomics, with Word as the target and Word's full object model exposed."

## 1.6 What is possible and what isn't

The DOCX format (ECMA-376 / ISO/IEC 29500 WordprocessingML) is a **declarative description**. Word lays it out when
the file is opened. So:

**Fully expressible (we can generate exactly what Word would save):**
character and paragraph formatting, styles and inheritance, themes, multilevel numbering, sections and page setup,
columns, headers and footers (first, odd and even pages), footnotes and endnotes, tables (merges, borders, shading, conditional
table styles), images (inline and floating, with wrapping), text boxes and shapes, equations (OMML), hyperlinks, bookmarks,
fields of every kind, content controls, legacy form fields, comments, tracked revisions, document properties,
protection settings, compatibility settings and embedded fonts.

**Expressible, but the result is computed by Word when the file is opened:**
- **Page numbers, TOC page numbers, `PAGEREF`, `NUMPAGES`.** We emit the field with a placeholder cached result and set
  `w:updateFields` so Word refreshes on open (Word asks the user first). An optional **finalize** step can
  refresh fields by automating Word (Windows COM) or LibreOffice headless.
- **TOC entry text.** We can pre-fill this because we know the headings. Only the page numbers need Word.
- **Cross-reference text** ("Figure 3", "Section 2.1"). We can compute this ourselves because we know the numbering. Page
  references cannot be computed this way.

**Hard, deferred:**
- Native charts (DrawingML chart parts plus embedded workbook). This is a lot of work, so charts start as images.
- Bibliography through Word's own `sources.xml` and `CITATION` fields. We first render citations ourselves with CSL.
- Reading arbitrary DOCX back into wordgenx.

**Out of scope:** SmartArt, OLE objects, VBA and ActiveX.

## 1.7 Target users and first use cases

1. **Engineers writing specifications or design documents** that must be delivered as Word with a corporate template. This is the
   primary dogfooding case: wordgenx's own docs will be written in wordgenx.
2. **Automated report generation** from data through the Python API, with macros and includes.
3. **Academic and technical writing** that needs math, citations and cross-references, delivered as DOCX for journals or
   reviewers.
4. **Legal and contract drafting** with numbered clauses, defined terms, and tracked changes between versions (later).
