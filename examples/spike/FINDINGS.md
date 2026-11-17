# M0 Syntax Spike: Findings

> **Status: all recommendations accepted (2026-10-15)** and incorporated into the
> [language design v0.2](../../docs/03-language-design.md). The samples below are kept as a record and are still in the
> **pre-spike draft syntax**. They do not follow v0.2.

Five realistic documents were written in the **current draft syntax**
([language design](../../docs/03-language-design.md)). Nothing compiles them yet. The goal was to find where the
language is awkward, ambiguous, or missing something before the grammar is frozen.

| Sample | Kind | Main stress points |
|--------|------|--------------------|
| [01-design-spec.rdx](01-design-spec.rdx) | Software design spec | Template, front matter with roman page numbers, TOC, requirement IDs, landscape section |
| [02-quarterly-report.rdx](02-quarterly-report.rdx) | Business report | Currency and percentages, numeric tables, generated includes, two-column section |
| [03-services-agreement.rdx](03-services-agreement.rdx) | Contract | Legal numbering, clause cross-references, defined terms, tracked changes, comments |
| [04-thesis-chapter.rdx](04-thesis-chapter.rdx) | Thesis chapter | Math, equation numbering, citations, theorems, subfigures, listings |
| [05-onboarding-form.rdx](05-onboarding-form.rdx) | Fillable form | Content controls, layout tables, protection, repeating sections |

Where a sample needed something the design does not define yet, it uses an invented construct and refers to a
finding below.

## Summary: decisions needed

| # | Finding | Severity | Recommendation |
|---|---------|----------|----------------|
| F-1 | Whitespace after commands | **High** | Keep spaces (unlike TeX). Breaks and tabs trim adjacent spaces. |
| F-2 | `%` comments vs percentages | **High** | `%` starts a comment only at line start or after whitespace |
| F-3 | `$` math vs currency | **High** | `$` is literal; inline math is `\m{…}`; display math stays `$$…$$` |
| F-4 | Arguments that must be verbatim | **High** | Each command declares argument kinds; paths, URLs and field codes are raw |
| F-5 | Captions and what a label points to | **High** | Caption is a paragraph and owns the label, as in Word |
| F-6 | `\page` starts a section | Medium | Rename to `\section` (Word's term) |
| F-7 | What `@label` prints | Medium | Depends on target kind, matching Word's cross-reference defaults |
| F-8 | Macro definition syntax | Medium | One optional body; parameters referenced as `#name` |
| F-9 | Clause-numbered documents are verbose | Medium | List sugar with style-linked levels and labels on items |
| F-10 | Two-body commands (`\comment`) | Low | Named option for the annotation text |
| F-11 | Citations not designed | Medium | `\cite`, `\textcite`, `\bibliography`, `\printbibliography` |
| F-12 | Custom counters and labels in macros | Medium | `\seq[Name]` (SEQ field) and an `id` pass-through |
| F-13 | Non-Word concepts (theorem, subfigure, callout) | Medium | A standard macro library ("prelude"), not core commands |
| F-14 | Document settings | Low | `\settings[…]` |
| F-15 | Table column options; pipe characters inside pipe-table cells | Low | `columns=(…)` option; pipe splitting respects code and math spans |
| F-16 | Small items | Low | See list |

---

## F-1 Whitespace after commands (High)

**Observed.** In `01-design-spec.rdx` the author wrote `\gateway{} payment` out of TeX habit. TeX swallows the space
after a control word, so `\gateway payment` would print "PayFlowpayment". In the other direction, `\control[…] Full-time`
in the form *needs* the space kept.

**Recommendation.** Whitespace after a command is **kept**, as in Typst. This is the predictable choice. The only
exception: the structural commands `\br`, `\tab`, `\pagebreak` and `\columnbreak` trim horizontal whitespace on both
sides, so `Signature:\tab \br` and `\tab Page` behave as people expect. `\name{}` remains a valid explicit terminator.

## F-2 `%` comments vs percentages (High)

**Observed.** The report needed `\%` **20 times** (`94.1\%`, `+9.4\%`, …). One missed escape silently deletes the
rest of the line. A URL like `…/a%20b` breaks the same way. The decision to use TeX-style comments stands, but the
pure TeX rule is costly for the business documents we are targeting.

**Options.**
- (a) Pure TeX: `%` always starts a comment. A lint warns on a digit followed by `%`.
- (b) **`%` starts a comment only at the start of a line or after whitespace.** `12%` and `%20` are literal. `\%` still works
  everywhere.

**Recommendation: (b).** Comments look exactly as in TeX in practice, since they almost always follow a line start or
whitespace, and the report needs no escapes. Only `% of` after a space (rare; style guides prefer `12%`) needs `\%`.

## F-3 `$` math vs currency (High)

**Observed.** The report needed `\$` for every amount (`\$48.2M`, `\$6.5M`). The thesis uses `$x$` heavily.

**Options.**
- (a) TeX: `$…$` is math, and currency is `\$`.
- (b) Pandoc heuristic: `$` opens math only if followed by a non-space, and closes only if preceded by a non-space and not
  followed by a digit. Works in most cases but is a heuristic, which conflicts with the "predictable parsing" goal.
- (c) **`$` is literal text. Inline math is `\m{…}`. Display math stays `$$…$$`** (rare in prose, so unambiguous).

**Recommendation: (c).** redocx is DOCX-first, and business documents use currency far more than inline math. The thesis
cost is `\m{x}` instead of `$x$`. A per-document `math-dollars=on` switch is possible but makes tools mode-dependent. Avoid unless
asked for.

## F-4 Arguments that must be verbatim (High)

**Observed.**
- `\field{STYLEREF "Heading 1" \n}`: `\n` is a field switch, but the parser would read it as a command.
- `\image{generated/charts/revenue_by_region.png}`: `_` would start emphasis. `%` (F-2) and `~` are also hazards in paths and URLs.
- `\link[https://…]{…}`, `\raw[ooxml]{…}`, `\include{…}` all take non-markup text.

**Recommendation.** Each command declares the **kind** of each argument in the command registry: `markup` (default),
`string` (no markup, escapes only for `}`), `raw` (balanced braces, nothing interpreted), or `math`. Field instructions
are `raw`. Nested fields use `\field` inside a `raw` argument through a single explicit exception, or a separate
`\fieldx{…}` with `{ }` field braces as in Word's Ctrl+F9 display. This needs a decision during grammar work.

## F-5 Captions and what a label points to (High)

**Observed.** Figures take the caption *inside* `\figure{…}`, but tables need a separate `\caption[@tbl:x, above]` after the
table. Generated tables (`\include`) can't carry their own caption, and the label sits on the table in one place and on the figure in another.

**How Word does it.** A caption is just a paragraph (style `Caption`) containing `Figure {SEQ Figure}`. A cross-reference to
"Figure 3" is a `REF` to a **bookmark around the caption**. Word has no figure container.

**Recommendation.** Do it the same way:
```rdx
\caption[Table, id=tbl:income]{Condensed income statement}   % placed above the table
\include{generated/q3-income-statement.rdx}

\figure[id=fig:rev]{ \image{…} \caption{Revenue by region} }  % sugar: image paragraph + caption, kept together; id moves to the caption
```
`\caption` is an ordinary block. Its kind (`Figure`, `Table`, `Listing`, `Equation`, or a custom kind) is explicit or inferred
from the adjacent object. The label always belongs to the caption.

## F-6 `\page` starts a section (Medium)

**Observed.** `\page[break=continuous, columns=2]` in the report doesn't start a page at all. Reading it as "page"
misleads.

**Recommendation.** Rename to **`\section[…]`**, Word's own term (Layout → Breaks → Section Breaks). Because the language is
DOCX-only, this follows Word's terms, even though LaTeX users expect `\section` to be a heading. The first `\section` in the preamble
configures section 1.

## F-7 What `@label` prints (Medium)

**Observed.** The samples write "Table @tbl:timing", "Theorem @thm:unique" and "clause \ref[form=number]{cl:invoice}", but
also "@fig:convergence shows…". The draft's default would print "Table Table 3.1".

**Recommendation.** The default form depends on the **kind of target**, mirroring Word's Insert Cross-reference defaults:

| Target | `@label` prints | Word field |
|--------|-----------------|------------|
| Caption (figure, table, listing) | "Figure 3.1" (label and number) | `REF _Ref… \h` on the label-and-number part of the caption |
| Equation | "(3.2)" | `REF` |
| Numbered heading / clause / list item | "4.2" (paragraph number, full context) | `REF \w \h` |
| Unnumbered heading | heading text | `REF \h` |
| Footnote | number | `NOTEREF \h` |

`\ref[form=…]{…}` overrides it. A lint warns when the word before `@label` repeats the label ("Table @tbl:…").

## F-8 Macro definition syntax (Medium)

**Observed.** `\define{field}[label, tag, type=text, placeholder="…"]{}{…}` needs an awkward empty `{}` because the macro
has no body. Parameters are referenced as `\label` and `\type`, which **collide with the command namespace**. `\label` and
`\type` could easily become built-ins. Substitution inside option values (`type=\type`) is not specified.

**Recommendation.**
```rdx
\define{field}[label, tag, type=text, placeholder="Click to enter"]{
  \p[style="Field Label"]{#label}
  \p{\control[type=#type, tag=#tag, placeholder=#placeholder]}
}
\define{callout}[title="Key takeaway"]{ … #title … #body … }   % #body = the invocation's body, if any
```
Macros take options plus **at most one body** (`#body`). `#name` is valid only inside `\define`, so it doesn't conflict with
the reserved `#{…}`. Substitution works in both text and option positions. Macro files are loaded with `\use{macros.rdx}`, as
style sheets are.

## F-9 Clause-numbered documents are verbose (Medium)

**Observed.** The contract repeats `\p[style="Clause 2"]{…}` for every clause, about 20 times in a short agreement.

**How Word does it.** The numbering definition links each level to a style ("Clause 1/2/3"), so applying the style
numbers the paragraph, and vice versa.

**Recommendation.** Let list sugar drive style-linked numbering, and allow labels on list items:
```rdx
\list[MSA]{
1. {#cl:term} Term
   1. This Agreement commences on the Effective Date …
2. {#cl:charges} Charges and Payment
   1. {#cl:invoice} The Supplier shall invoice the Customer monthly …
   2. If any sum is not paid when due, the Supplier may:
      a. charge interest …; and
      b. suspend the Services …
}
```
Because MSA's levels are linked to styles, each item gets "Clause n" automatically. The output is exactly what a Word
user builds by hand. The `1.` and `a.` markers in source are only nesting cues; the format comes from the definition.

## F-10 Two-body commands (Low)

**Observed.** `\comment[author=…]{commented text}{comment text}`: two bodies whose order is easy to confuse.

**Recommendation.** `\comment[author="R. Patel", text="Please confirm…"]{thirty (30) days}`. A long or rich comment can use
`text={…markup…}`. In general, prefer **one body per command**. Additional content goes in named options.

## F-11 Citations are not designed yet (Medium)

**Observed.** The thesis invented `\cite{key}`, `\cite[p.~214]{key}`, `\textcite{key}`, `\bibliography[style=apa, file=refs.bib]`
and `\printbibliography`. `[p.~214]` is also not valid option syntax.

**Recommendation.** Adopt those names (familiar from biblatex). Use `\cite[page=214]{key}` /
`\cite[prefix="see", suffix="Lemma 2"]{key}` and multiple keys `\cite{a, b}`. Render with CSL (citeproc-py), producing
plain text plus bookmarks and hyperlinks into the bibliography. This needs a section in the language design. It is milestone M3.

## F-12 Custom counters and labels in macros (Medium)

**Observed.** The spec's `\req` macro needs a running "REQ-n" number and a referenceable label from its `id` option.

**Recommendation.** `\seq[Req]` emits a `SEQ Req` field with a pre-computed result. Formats use options, for example
`\seq[Req, format=decimal, restart-at=heading-1]`. Any command or macro body can set `id=#id` to forward a label. Referencing a
`\seq` label prints its number.

## F-13 Non-Word concepts: theorem, proof, subfigure, callout (Medium)

**Observed.** The thesis needs `\theorem` and `\proof`, the report a callout box, and the thesis subfigures. Word has none of these as
native concepts.

**Recommendation.** Keep them **out of the core** (DOCX-only principle) and ship a **standard macro library** (prelude) written in
`.rdx` that builds them from Word primitives:
- theorem: a styled paragraph with `Theorem {SEQ Theorem}` and an optional name
- callout: a one-cell table with a table style
- subfigure: a borderless layout table with images and `SEQ` captions (letter format)

Users can read, copy and override these. This is also a good test that macros are powerful enough.

## F-14 Document settings (Low)

**Observed.** The form needs document protection, and the spec wants field-update-on-open off. There was no command for either.

**Recommendation.** `\settings[protection=(mode=forms, enforce), update-fields-on-open=off, even-odd-headers, …]`, which maps to
`settings.xml`. It belongs in the preamble only.

## F-15 Table column options; pipe characters in pipe-table cells (Low)

**Observed.** The report needs decimal-aligned number columns. The thesis avoided `|x|` in math inside a pipe table by using `\lVert`.

**Recommendation.** Add a `columns=(left, decimal, decimal, right)` table option. It emits per-cell paragraph alignment or a
decimal tab stop, as Word does. The pipe-table splitter must not split inside `` `code` `` or math spans. `\|` is a literal pipe.

## F-16 Small items

- `\date[format=…]` emits a `DATE` field, which changes every time the file is opened. Specs usually want `SAVEDATE`, `CREATEDATE` or fixed text. Make `\date` require
  `kind=(today|saved|created|fixed)`, defaulting to `fixed` at build time.
- Row height `height=1.5cm at-least` should use the common tuple form `height=(1.5cm, at-least)`.
- Repeating content-control sections (`w15:repeatingSection`) were "Later" in the feature matrix but are needed for realistic
  forms. Move them to M5.
- `\let` vs `\define`: keep both. `\let` is a zero-parameter constant and is easier to read.
- Multi-file documents need labels resolved across includes (`@ch:experiments` is in another chapter). The architecture already handles this; it is
  noted here for test coverage.
- Defined terms (contract): a later lint could warn when a capitalised defined term is used but never defined, or defined but
  never used. This is a strong feature for legal users.
