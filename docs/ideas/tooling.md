# Ideas: tooling for later

> Status: not scheduled. The language and the compiler come first: these wait until the language has settled and
> the system has matured. Each entry says what it would be and how it would most likely be built.

## Language server and editor support

- **Language server (LSP):** diagnostics as you type (the compiler already reports spans and help), completion of
  commands, options and property names from the command schema and the property registry, go to a label's
  definition, hover documentation for commands and properties.
- **VS Code extension:** a TextMate grammar for `.wgx` and `.wgxs` (syntax colouring works without the server), plus
  the LSP client. Later: a preview pane.

Why wait: completion and hover are only as good as the schema, and the schema still changes with every milestone.

## `wordgenx watch`

Rebuild when a source, style sheet, include or picture changes. Optionally ask Word to reload the open document
(through COM on Windows), so the result shows next to the editor. Small, but only worth it once people write longer
documents with wordgenx.

## PDF export and "finalize"

A `.docx` is what wordgenx makes; a PDF is a rendering of it, and only a word processor can lay out a page. So PDF
export means asking one:

- **Word** (Windows, macOS): open the document, update its fields and contents, export with
  `ExportAsFixedFormat`, close without saving. This is exactly what the test suite does to check documents, and it
  is the faithful renderer: wordgenx targets Word. On Windows through COM, on macOS through AppleScript (the
  `docx2pdf` package does both).
- **LibreOffice** (anywhere, including servers and CI): `soffice --headless --convert-to pdf`. Reliable to run, but
  it lays out Word documents differently in places (fonts, spacing, floating objects, fields), so the PDF can differ
  from what Word shows.

`wordgenx finalize` would be the first half without the PDF: update every field, the contents and the index in Word
and save, so the document opens with no update prompt.

## Website and playground

Documentation generated from the command schema and the property registry, the design book, and a playground that
compiles a snippet in the browser (through Pyodide) and shows the resulting XML or offers the `.docx`.
