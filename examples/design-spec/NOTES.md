# M1 trial: porting the design specification

`design-spec.wgx` is the spike sample [01-design-spec.wgx](../spike/01-design-spec.wgx) rewritten in language v0.2
and built with the M1 compiler. It built with no errors and 7 warnings, all for features planned for M2 (after M2
step 2, one warning is left: the footnote). Word opens
the result with numbered headings, continuously numbered requirements (REQ-1 … REQ-5), tables, the diagram,
callouts and approval lines.

## What worked well

- **Style-linked numbering** carries the document. Headings are numbered through the `Headings` list, and the
  requirement macro is a single `\p[style=Requirement]`, because the `Requirement` style is linked to a `REQ-%1` list
  level. Word users get the same behaviour when they apply the style by hand.
- **Macros + std library.** `\req`, `\warning`, `\tip` and `\approval` kept the source short and readable.
- **Pipe tables** were enough for every table in the spec, including column alignment.
- **Spike decisions held up.** Nothing needed `\%` or `\$`, `@refs` read naturally, and `\section` made sense.

## Problems found and fixed while porting

| Problem | Fix |
|---------|-----|
| A long `alt="…"` broken across lines produced 28 cascading errors | The parser now skips the rest of an option list after an unclosed string (1 error). Text options also accept `{…}`, which may span lines: `alt={a long description …}`. |
| `#E8F0FA` inside a `\define` body was read as the parameter `#E8F0FA` | In option values, `#` plus six hex digits is always a colour |

## Gaps (planned for M2)

In order of how much the spec misses them:

1. ~~Headers and footers~~, ~~several sections~~. **Done in M2 step 1:** the spec now has a title page without a
   header, roman page numbers for the front matter, decimal numbers restarting at the body, a running header with
   the current chapter (`STYLEREF`), "Page n of m", and a landscape appendix.
2. ~~Table of contents~~, ~~cross-references~~, ~~captions~~. **Done in M2 step 2:** the spec has a contents page
   and lists of figures and tables with pre-rendered entries; `@sec:refunds` prints "3.2" and `@app:errors` prints
   the appendix title; the context diagram is a `\figure`, the definitions table and the webhook code have
   captions, and all of them are referenced. Word, after updating every field, shows the same text the compiler
   wrote.
3. **Footnotes.**

The spec is 4 pages. The M1 exit criterion ("a real 20-page document") is met in kind but not in length: nothing in
the port suggests a longer document would hit new problems, but it has not been tried.
