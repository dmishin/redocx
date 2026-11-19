# Changelog

Changes that users see, newest first. The language follows the compatibility rule in
[decision Q14](docs/08-open-questions.md): a source that compiles keeps its meaning in later 0.x versions, and
anything that would change it is listed here first as a deprecation warning.

## 0.1.0 (2026-11-20)

First versioned release. The language grammar is frozen under Q14.

- **Language:** paragraphs, headings, lists with Word numbering, tables (pipe and block forms, merges, table styles),
  pictures (PNG, JPEG, GIF, BMP, SVG, EMF, WMF; inline and floating), text boxes, watermarks, sections, headers and
  footers, fields, footnotes and endnotes, captions, cross-references, contents, index, math from LaTeX notation,
  highlighted code, citations from BibTeX or CSL-JSON, comments, content controls, document protection, macros and
  the standard library (`std:callout`, `std:signature`, `std:latex`).
- **Styles:** `.rdxs` style sheets and `\styles{}`, themes, compiling into a `.dotx` or `.docx` template, writing
  templates.
- **Import:** `redocx import` turns a `.docx` into source, a style sheet and its pictures, with a report of what was
  lost and `--check` to compare the result with the original.
- **Generation:** `redocx.escape` writes any data into source safely; standard input and output for `build` and
  `check`; `--warnings-as-errors`; `--diagnostics json`; `--root` to keep file references inside one folder.
- **Tools:** `redocx check`, `fmt`, `parse`, `inspect`, `explain`; `--emit-ir`; `build --validate`.
