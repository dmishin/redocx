# 6. Roadmap

There are no dates. Each milestone has **exit criteria**. A milestone is done when its gallery documents open in Word
with no repair prompt, pass both validators, and match their golden snapshots.

## M0: Foundations

- [x] Repository setup: `uv`, `ruff`, `pyright --strict`, `pytest`, GitHub Actions (Windows, Linux, macOS), license.
  CONTRIBUTING is still to do.
- [x] OPC package writer: content types, relationships, deterministic zip.
- [x] Minimal part writers: document, styles (docDefaults, the four default styles, user styles), settings, core and app
  props, fontTable, theme (own default theme).
- [x] Validation pipeline: lxml + ECMA-376 Transitional XSDs (`schemas/`); `tools/ooxml-validate` (.NET OpenXmlValidator).
- [x] Golden-test harness (canonical XML snapshots in `tests/golden/expected/`).
- [x] First version of the property registry: 15 run and 14 paragraph properties, with schema-order tables checked against `wml.xsd`.
- [x] **Spike:** 5 representative documents in the proposed syntax. See the [findings](../examples/spike/FINDINGS.md).
- **Exit:** the Python API produces a valid document with styled paragraphs. This is met locally: the golden cases pass both validators
  and open in Word 365 with the expected formatting. CI has not run yet because the repository has no GitHub remote.

## M1: Core language (roughly "Pandoc parity")

- [x] Parser for all modes, lossless CST, typed AST, source spans, diagnostics renderer (`wordgenx.syntax`; rules in
  design §3.22). Checked by property-based fuzzing (Hypothesis) and a syntax tour (`examples/syntax-tour.wgx`).
- [x] CLI `wordgenx check` and `wordgenx parse` (`--tree ast|cst`).
- [x] Lowering AST -> IR (`wordgenx.lower`, design §3.23): inline sugar, formatting commands, `\p`, `\align`, option
  type-checking, units and colours (incl. theme `lighter`/`darker`), smart punctuation.
- [x] Style sheets (`.wgxs` and `\styles{}`): defaults, paragraph, character and linked styles, theme references,
  modify/replace, multilevel numbering definitions with style-linked levels (`numbering.xml`). Table styles are parsed
  but deferred to M2.
- [x] Headings, paragraphs, lists with real multilevel numbering (separate lists restart; `\list[Name, continue]`,
  `restart=N`).
- [x] Single-section page setup, page and column breaks, hyperlinks.
- [x] Inline images (PNG, JPEG, GIF, BMP) and tables: pipe and block forms, widths, horizontal and vertical merges,
  shading, header rows, row height, nested tables (borders via `Table Grid`; custom table styles in M2).
- [x] `\include`, `\use` (style sheets and macro files), `\let`, `\define` (macros with `#param`), `\settings`,
  `\document`, `\meta`.
- [x] Command registry with argument kinds (markup, string, raw, math); the comment, whitespace and flanking rules from the spike.
- [x] Standard macro library (`std:callout`, `std:signature`), packaged with the compiler.
- [x] Trial: the spike's design specification ported to v0.2 (`examples/design-spec/`, findings in its `NOTES.md`).
- [x] CLI: `wordgenx build` (and `check` runs the full compile).
- **Exit:** a real 20-page document can be authored entirely in wordgenx. Language grammar v0.1 is frozen.
  *Status:* the 4-page specification trial builds cleanly, and its remaining gaps (headers and footers, TOC,
  cross-references, captions, footnotes, sections) are M2 items. The grammar has been stable through M1 (v0.2 plus the
  §3.22–3.23 rules); freezing it is the last step.

## M2: Word structure (where wordgenx goes beyond Pandoc)

- [x] Multiple sections (orientation, size, margins, columns, break types); headers and footers (first, even, linked
  or not); page-number formats and restarts; fields (`\pagenum`, `\numpages`, `\field`, `\date`); tab stops.
- [x] Bookmarks; cross-references with pre-computed text (`@label`, `\ref`, `\pageref`); captions (`\caption`,
  `\figure`, code-block captions); `\seq` counters; TOC and lists of figures/tables with pre-rendered entries.
- [x] Footnotes and endnotes (`\footnote`, `\endnote`, labels and `NOTEREF`, numbering format, start, restart and
  position settings).
- [x] Full tables: merges, borders, shading (theme colours), repeat headers, cell margins, look flags, table styles with
  conditional formatting (header row, last row, first/last column, banded rows/columns, corner cells).
- [x] Templates: compile into a `.dotx` or `.docx` (styles, numbering, theme, page setup, headers and footers, body
  content, other parts); write `.dotx` templates.
- [x] Heading numbering, legal numbering, tab stops, paragraph borders (sides, between, bar) and shading.
- [x] Document properties: core, custom (`\meta[custom.NAME = ...]`, merged with a template's) and `DOCPROPERTY`;
  general `\field`.
- [x] Sweep: document protection, mirror margins, line numbering, section vertical alignment, decimal table columns,
  chapter-numbered captions, references in headers and footers, per-cell margins and text direction, more run and
  paragraph properties (scale, kerning, emboss, imprint, outline, shadow, suppress line numbers and hyphenation),
  and the escape hatches (`\raw[ooxml]`, `ooxml.*` properties). Moved out of M2: list (numbering) styles (later),
  columns of different widths (M4).
- **Exit:** wordgenx's own design docs are rebuilt in wordgenx with a corporate-style template and look professionally typeset in Word.
  *Status: met.* `docs/book/design.wgx` builds the eight design documents into one 38-page book with the
  `docs/book/wordgenx.dotx` template (itself written in wordgenx): cover page, contents, numbered chapters with
  running heads, styled tables with repeated header rows, boxed code, notes, front matter with roman page numbers.
  It builds without diagnostics, passes both validators and opens cleanly in Word. The chapters are converted from
  the Markdown sources by `tools/md2wgx.py`, and the test suite keeps them in step.

## M3: Technical writing

Steps, in order:

1. [x] **Math** (LaTeX subset to OMML, `wordgenx.math`). Inline `\m{…}`, display `$$…$$` and `\math[…]{…}`. Fractions,
   roots, scripts, big operators with limits, delimiters (`\left…\right`), functions and `\lim`, accents and bars,
   matrices and `cases`, `aligned` rows with `&` alignment, text and font styles (`\mathbf`, `\mathbb`, …), Greek
   letters and the common symbol set. Unsupported macros are errors at their position. OMML is written the way Word
   writes it (the reference survey settled Q9). A numbered display equation is an `m:eqArr` with Word's own `#`
   number. Labelled equations are numbered, optionally per chapter, and `@eq:x` prints "(3.2)".
2. [x] **Code highlighting** (Pygments): token runs in generated, themeable character styles (`Code Keyword`, …),
   `line-numbers`, captions as now.
3. **Citations and bibliography**: BibTeX or CSL-JSON data, CSL styles through citeproc-py, `\cite`/`\textcite`
   linked to entries, `\printbibliography` in a `Bibliography` style.
4. **Index**: `\ix{term!sub}` as `XE` fields, `\index[columns=2]` as an `INDEX` field with pre-rendered entries.
5. **Images and embedding**: SVG (with a PNG fallback, as Word stores it), `\embed-docx` (altChunk).
6. **Tooling**: `wordgenx fmt` (CST-based formatter), `--emit-ir` (JSON).

- **Exit:** a thesis-style document with math, citations, code and figures.

## M4: Advanced layout

- Floating images and wrapping, text boxes, shapes, watermarks, page borders and backgrounds.
- Drop caps, frames, floating tables.
- RTL and complex scripts; custom themes.

## M5: Collaboration & forms

- Comments, tracked insertions and deletions, `wordgenx diff` redlines.
- Content controls; document protection.

## M6: Ecosystem

- Language server (diagnostics, completion from the property registry, go-to-label, hover docs).
- VS Code extension (TextMate grammar plus LSP client).
- `wordgenx watch` (rebuild on change; optionally refresh in Word).
- `wordgenx finalize` / `--pdf` (Word COM or LibreOffice).
- `docx → wordgenx` importer (best effort).
- Website plus playground.

## Later / candidates

Native charts, native Word bibliography, content-control data binding, embedded fonts,
building blocks, tracked formatting changes.

## Suggested immediate next steps

1. ~~Confirm the open questions.~~ Done: see the [decision log](08-open-questions.md).
2. ~~Write the 5 sample documents (M0 spike) and review them together.~~ Done: see the [findings](../examples/spike/FINDINGS.md), all accepted.
3. Collect reference XML: build each M1–M2 feature in Word, save, and archive the unzipped XML under `tests/reference/` with notes.
4. Start M0.
