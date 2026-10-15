# 6. Roadmap

There are no dates. Each milestone has **exit criteria**. A milestone is done when its gallery documents open in Word
with no repair prompt, pass both validators, and match their golden snapshots.

## M0: Foundations

- Repository setup: `uv`, `ruff`, `pyright --strict`, `pytest`, GitHub Actions (Windows, Linux, macOS), license, CONTRIBUTING.
- OPC package writer: content types, relationships, deterministic zip.
- Minimal part writers: document, styles (defaults only), settings, core and app props, fontTable, theme (embedded default).
- Validation pipeline: lxml + ECMA-376 XSDs; `tools/ooxml-validate` (.NET OpenXmlValidator) in CI. This needs the .NET 8+ SDK on dev machines (currently only the runtime is installed).
- Golden-test harness (canonical XML diff).
- First version of the property registry, with run and paragraph basics.
- **Spike:** hand-write 5 representative documents in the proposed syntax (spec, report, contract, thesis chapter,
  form) to stress-test the language before freezing the grammar.
- **Exit:** the Python API produces a valid "hello world" with styled paragraphs. CI is green on all three OSes.

## M1: Core language (roughly "Pandoc parity")

- Lexer and parser (all modes), CST to AST, source spans, diagnostics renderer.
- Inline sugar, commands, options type-checking, units and colors.
- Style sheets: defaults, paragraph and character styles, theme references.
- Headings, paragraphs, lists with real multilevel numbering (restart and continue).
- Single-section page setup, breaks, hyperlinks, inline images, simple tables.
- `\include`, `\use`, `\let`, `\define` (macros with `#param`), `\settings`.
- Command registry with argument kinds (markup, string, raw, math); the comment, whitespace and flanking rules from the spike.
- Standard macro library skeleton (`std:callout`, `std:signature`).
- CLI: `wordgenx build`, `wordgenx check`.
- **Exit:** a real 20-page document can be authored entirely in wordgenx. Language grammar v0.1 is frozen.

## M2: Word structure (where wordgenx goes beyond Pandoc)

- Multiple sections; headers and footers (first, even, linked); page-number formats.
- Footnotes and endnotes; bookmarks; cross-references with pre-computed text; captions; TOC; list of figures and tables.
- Full tables: merges, borders, shading, repeat headers, table styles with conditional formatting.
- Templates: compile into `.dotx` or `.docx`, merge styles and numbering.
- Heading numbering, legal numbering, tab stops, paragraph borders.
- Document properties; general `\field`.
- **Exit:** wordgenx's own design docs are rebuilt in wordgenx with a corporate-style template and look professionally typeset in Word.

## M3: Technical writing

- Math (LaTeX subset to OMML), equation numbering.
- Code highlighting (Pygments).
- Citations and bibliography (CSL), index.
- SVG images; charts as images through the API; altChunk.
- `wordgenx fmt` formatter; `--emit-ir`.
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
