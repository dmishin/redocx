# 8. Decisions & Open Questions

## 8.1 Decided (2026-10-15)

| # | Question | Decision | Notes |
|---|----------|----------|-------|
| Q1 | Implementation language | **Python 3.12+** with our own lxml-based OOXML writer | [ADR-0001](02-adr-implementation-language.md). .NET SDK is used only for the CI validator tool. |
| Q2 | Project name | **redocx** | Python package and CLI: `redocx`. Source files: `.rdx`; style sheets: `.rdxs`; diagnostic codes: `RDX####`. The name was free on PyPI and as a GitHub org on 2026-10-15. |
| Q3 | License | **Apache-2.0** | The patent grant matters in an Office-format space |
| Q4 | Command sigil | **`\cmd`** | Familiar from TeX. `#` and `@` are frequent in prose. |
| Q5 | Comment syntax | **`%` to end of line, as in TeX**; literal `\%` | Refined by spike F-2: `%` starts a comment only at line start or after whitespace, so `12%` is literal. There are no block comments. |
| Q6 | Emphasis sugar | **`*strong*`, `_emph_`** (Typst-style) | |
| Q7 | Scripting | **None** | Programmability comes through the Python API. `#{…}` stays reserved in case this is reconsidered. |
| Q8 | Output style | **Idiomatic Word**: sugar maps to character styles (`Strong`, `Emphasis`), with real numbering, fields and captions | The language is designed for DOCX only. It is not a general-purpose markup language (see [vision §1.3](01-vision-and-scope.md)). |
| Q10 | OOXML flavour | **Transitional** | |
| Q11 | Minimum Word target | **Word 2016+** (`compatibilityMode=15`), with w14/w15 extensions under `mc:Ignorable` | |
| Q12 | Indentation significance | **Only for list nesting sugar** | Everything else uses braces |
| Q13 | Hosting & governance | **GitHub, DCO** | |

## 8.2 Decided by the M0 syntax spike (2026-10-15)

All recommendations in the [spike findings](../examples/spike/FINDINGS.md) were accepted and are now in the
[language design](03-language-design.md) (draft v0.2):

| # | Decision |
|---|----------|
| F-1 | Whitespace after commands is kept. Break-like commands (`\br`, `\tab`, `\pagebreak`, `\columnbreak`) trim adjacent spaces. |
| F-2 | `%` starts a comment only at line start or after whitespace |
| F-3 | `$` is literal. Inline math is `\m{…}`; display math is `$$…$$` or `\math{…}`. |
| F-4 | Commands declare argument kinds: `markup`, `string`, `raw`, `math` |
| F-5 | A caption is a paragraph and owns the label. `\figure` is sugar. |
| F-6 | `\page` renamed to `\section` (Word's section) |
| F-7 | What `@label` prints depends on the target kind, following Word's cross-reference defaults |
| F-8 | Macros: named options plus at most one body; parameters referenced as `#name` |
| F-9 | List sugar drives style-linked numbering; list items can carry labels |
| F-10 | One body per command. Extra content goes in named options (`\comment[text=…]`). |
| F-11 | `\cite`, `\textcite`, `\bibliography`, `\printbibliography`, rendered with CSL |
| F-12 | `\seq[Name]` custom counters; `id` forwarding in macros |
| F-13 | Non-Word concepts go in a standard macro library (`std:…`), not the core |
| F-14 | `\settings[…]` for `settings.xml` |
| F-15 | `columns=(…)` table option; pipe splitting respects code, math and command bodies |
| F-16 | `\date[kind=…]` defaults to fixed text; tuple syntax for length+rule; repeating content controls move to M5 |

## 8.3 Still open

| # | Question | Options | Plan |
|---|----------|---------|------|
| Q9 | Equation numbering layout | 3-column borderless table / OMML `#` alignment / tab stops | **Decided (M3):** OMML `#` alignment, which is what Word itself writes for `#(1)` (an `m:eqArr` row ending in `#` and the number); see design §3.14 |
