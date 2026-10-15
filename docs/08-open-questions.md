# 8. Decisions & Open Questions

## 8.1 Decided (2026-10-15)

| # | Question | Decision | Notes |
|---|----------|----------|-------|
| Q1 | Implementation language | **Python 3.12+** with our own lxml-based OOXML writer | [ADR-0001](02-adr-implementation-language.md). .NET SDK is used only for the CI validator tool. |
| Q2 | Project name | **wordgenx** | Python package and CLI: `wordgenx`. Source files: `.wgx`; style sheets: `.wgxs`; diagnostic codes: `WGX####`. The name was free on PyPI and as a GitHub org on 2026-10-15. |
| Q3 | License | **Apache-2.0** | The patent grant matters in an Office-format space |
| Q4 | Command sigil | **`\cmd`** | Familiar from TeX. `#` and `@` are frequent in prose. |
| Q5 | Comment syntax | **`%` to end of line, as in TeX**; literal `\%` | The compiler warns on `%` right after a digit (likely a forgotten `\%`). There are no block comments. |
| Q6 | Emphasis sugar | **`*strong*`, `_emph_`** (Typst-style) | |
| Q7 | Scripting | **None** | Programmability comes through the Python API. `#{…}` stays reserved in case this is reconsidered. |
| Q8 | Output style | **Idiomatic Word**: sugar maps to character styles (`Strong`, `Emphasis`), with real numbering, fields and captions | The language is designed for DOCX only. It is not a general-purpose markup language (see [vision §1.3](01-vision-and-scope.md)). |
| Q10 | OOXML flavour | **Transitional** | |
| Q11 | Minimum Word target | **Word 2016+** (`compatibilityMode=15`), with w14/w15 extensions under `mc:Ignorable` | |
| Q12 | Indentation significance | **Only for list nesting sugar** | Everything else uses braces |
| Q13 | Hosting & governance | **GitHub, DCO** | |

## 8.2 Still open

| # | Question | Options | Plan |
|---|----------|---------|------|
| Q9 | Equation numbering layout | 3-column borderless table / OMML `#` alignment / tab stops | Decide after the M0 reference-XML survey, preferring whatever Word itself produces (idiomatic-output principle) |
