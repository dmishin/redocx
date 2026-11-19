# 8. Decisions & Open Questions

## 8.1 Decided (2026-10-15)

| # | Question | Decision | Notes |
|---|----------|----------|-------|
| Q1 | Implementation language | **Python 3.12+** with our own lxml-based OOXML writer | [ADR-0001](02-adr-implementation-language.md). .NET SDK is used only for the CI validator tool. |
| Q2 | Project name | **redocx** (ReDocx) | Python package and CLI: `redocx`. Source files: `.rdx`; style sheets: `.rdxs`; diagnostic codes: `RDX####`. The project started as *wordgenx* (`.wgx`, `.wgxs`, `WGX####`) and was renamed on 2026-11-18. |
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
[language design](03-language-design.md):

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

## 8.3 Decided later

| # | Question | Options | Decision |
|---|----------|---------|----------|
| Q9 | Equation numbering layout | 3-column borderless table / OMML `#` alignment / tab stops | **Decided in M3:** OMML `#` alignment, which is what Word itself writes for `#(1)` (an `m:eqArr` row ending in `#` and the number); see design §3.14 |
| Q10 | LaTeX commands written from habit | generic "unknown command" with a near name / fuzzy match against LaTeX names / exact-name hint table | **Exact-name table** (`redocx.commands.latex`): the near-name guess pointed LaTeX users the wrong way (`\textbf` -> `\textbox`), and fuzzy LaTeX matching would turn typos of redocx commands into LaTeX advice. Each hint names a section of `docs/guide/tex.md`; a test keeps table and guide in sync. Same codes (`RDX0301`, `RDX0302`, `RDX0303`); see design §3.21 |
| Q11 | Standard library loading; LaTeX compatibility | opt-in `\use{std:…}` / loaded by default with flags to turn off; LaTeX commands as errors only / as a macro module | **Loaded by default**, with `--no-std` and `--no-latex`: the modules only define macros, so unused ones add nothing to the output, and a document macro of the same name wins. **`std:latex`** defines the common LaTeX commands as their redocx forms and is silent when used; what it cannot define (built-in names, two arguments, environments, string bodies) keeps the Q10 hints. See design §3.18 and §9 |
| Q14 | Grammar freeze and compatibility | freeze at 1.0 / freeze now with a rule / keep changing freely | **Frozen at 0.1.0 (2026-11-20), the v0.2 grammar of design §3.22–3.23.** A source that compiles keeps its meaning in later 0.x versions. Additions are fine: commands, options, properties, `std:` modules, and new sugar only where the text it uses was an error before. A change that would alter what a valid source means is first a deprecation warning for one minor version, listed in CHANGELOG.md. Generated sources are the main use, and their authors cannot follow grammar changes by hand. |

## 8.4 Still open

Nothing is open.
