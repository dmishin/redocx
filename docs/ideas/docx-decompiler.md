# Musings: a `docx → redocx` decompiler

> **Update:** implemented as M6, `redocx import` (see the [roadmap](../06-roadmap.md) and the
> [import guide](../guide/import.md)). This note is kept as the background to that work.
>
> Status: thinking out loud. This is not a plan or a commitment. The roadmap lists a "best-effort importer" for
> later (`06-roadmap.md`); this note asks how far "best effort" could realistically go and what makes it hard.

## 1. The framing: it really is a decompiler

The analogy with binary decompilation holds up well:

| Binary world                       | redocx world                                                          |
| ---------------------------------- | ----------------------------------------------------------------------- |
| Source → compiler → machine code   | `.rdx` → expand → lower → resolve → emit → `.docx`                      |
| Compilation is many-to-one         | Macros, `\let`, sugar and includes all expand away                      |
| Disassembly (mechanical)           | OOXML → IR (mechanical, since the IR mirrors WordprocessingML)          |
| Decompilation to C (structuring)   | IR → canonical commands (`\p[style=…]{…}`)                              |
| Idiomatic C, named variables, types| Sugar, stylesheets with inheritance, macros, labels with real names    |
| Library signatures (IDA's FLIRT)   | Recognising `std:callout` and the user's own macro expansions           |
| Hand-written assembly, obfuscation | Documents authored by hand in Word: direct-formatting soup              |

Because compilation is many-to-one, the decompiler can never recover *the* source. It produces *a* source. The only
real question is how good that source is.

Two inputs make very different problems, and they should be kept apart:

- **A.** The `.docx` was produced by redocx (possibly edited in Word afterwards). This is tractable, and possibly the
  most valuable case (see §6).
- **B.** The `.docx` was authored in Word by people. This is the "real" decompiler problem. A faithful result is
  achievable; an idiomatic one is heuristic.

## 2. Why the architecture is in our favour

Two existing design decisions make this much easier than a generic converter like Pandoc's docx reader:

1. **The IR mirrors WordprocessingML** (`04-architecture.md` §4.3). Reading OOXML into IR is a parse, not an
   interpretation. Pandoc must squash Word into a format-neutral AST and loses styles, sections and fields on the way.
   We do not have to.
2. **Every sugar has a command equivalent** (`03-language-design.md`, principle 6, which already names the importer).
   So any IR can be printed as canonical commands, and the sugar layer becomes an optional pass on top.

The pipeline would roughly be the compiler run backwards:

```
.docx ──read──▶ raw IR ──normalize──▶ clean IR ──lift──▶ canonical AST ──resugar / abstract──▶ AST ──print + fmt──▶ .rdx
                (lossless)  (merge runs,      (styles, sections,   (headings, lists, pipe     (redocx fmt
                            drop rsid noise)   fields, labels)      tables, macros, \let)      for layout)
```

The reading side is partly needed anyway: compiling into a corporate `.dotx` requires reading its styles, numbering
and theme (`template/` in the architecture). So maybe a third of the infrastructure is shared.

**One design implication for today:** the property registry (`props/registry.py`) is emit-only. If each `PropSpec`
had an inverse (`parse`: OOXML elements → value), the decompiler would mostly fall out for formatting, and
`parse ∘ emit = id` would be a property-based test for every property. That is worth keeping in mind as properties
are added, even if nothing is built yet.

## 3. Levels of decompilation

It helps to think in levels, like assembly → C → idiomatic C:

| Level | Output                                                        | Feasibility                        |
| ----- | ------------------------------------------------------------- | ---------------------------------- |
| L0    | Everything wrapped in raw OOXML escape hatches                | Always possible. Useless.          |
| L1    | Canonical commands, all direct formatting kept as options     | Realistic for nearly all content.  |
| L2    | Sugar: `= Heading`, lists, pipe tables, `*bold*`, `{#label}`  | Realistic. Mostly pattern matching.|
| L3    | Abstraction: stylesheets, `\define` macros, `\let`, std lib   | Heuristic. The fun part.           |

A good decompiler always reaches L1 for whatever it understands, falls back to L0 locally for what it does not
(one `\raw{…}` for a SmartArt, not for the whole document), and climbs to L2/L3 only where it can prove it is safe.

## 4. The hard parts

### 4.1 Run soup
Word splits text into runs for reasons that mean nothing to the author: `rsid` revision IDs, spell-check
`proofErr` markers, IME input, past edits. The first normalisation pass computes the *effective* formatting of each
character (doc defaults → table style → paragraph style → numbering → character style → direct formatting) and
re-segments runs by real differences. Without this, the output is unreadable.

### 4.2 Direct formatting versus styles
The defining problem of category B. People make "headings" with bold 14 pt Normal paragraphs. Options:

- **Faithful:** emit `\font[size=14pt]{\b{…}}`. Correct, ugly, and against the spirit of the language.
- **Inferred:** cluster paragraphs by their effective property vectors, propose synthetic styles (`"Imported 1"`),
  and detect heading-like clusters (short, bold, larger, followed by body text, `keep-with-next`). Opt-in, as in
  `--infer-styles`.
- **User-guided:** borrow mammoth.js's *style map*: a small file that says "Word style `Überschrift 1` → `=`",
  "bold 14pt → Heading 2". This is a simple and proven idea.

### 4.3 Stylesheet bloat
`styles.xml` in a typical Word document holds hundreds of built-in and latent styles. The decompiler should print
only styles that are used (or are ancestors of used ones), and only properties that differ from Word's own built-in
definition. If the user supplies the original template (`--template corp.dotx`), it can *subtract* the template:
styles identical to it are inherited via `\use`, not reprinted. That is a big win for corporate documents.

### 4.4 Numbering
`abstractNum` / `num` / `lvlOverride` / `startOverride` is hard to read. Word-authored documents often create a new
`numId` for every list or restart. Mapping back requires grouping consecutive paragraphs that share a numbering
instance into lists, deciding whether a list is plain sugar (`-`, `1.`) or a named numbering definition, and
leaving manually typed "1." as text.

### 4.5 Fields, bookmarks and cross-references
- Complex fields (`fldChar begin/separate/end`) span runs, nest, and can cross paragraphs. Parsing them into a field
  tree is a necessary early pass.
- Known fields map to commands (`TOC`, `REF`, `PAGEREF`, `SEQ`, `PAGE`, `TITLE`); unknown ones stay
  `\field{…}`.
- The cached result of a `TOC` field is the whole table of contents. It should be dropped and replaced with `\toc`.
- "Figure " + `SEQ Figure` → a caption command.
- Word's hidden bookmarks (`_Ref123456789`, `_Toc…`) should become readable labels (`{#fig:architecture}`)
  derived from the target's text. Unreferenced ones are dropped.

### 4.6 Sections
WordprocessingML puts `sectPr` at the *end* of the last paragraph of a section; redocx puts `\section` at the
start. Headers and footers are inherited across sections ("link to previous" means "absent"). Inverting both is
straightforward but fiddly.

### 4.7 Units and colours
Twips, half-points and EMUs have to become units a human would write. Snap to "nice" values within a tolerance
(567 twips → `1cm`, 1440 → `1in`), and choose the unit family from the page size or locale (A4 → cm). Table widths
that are exact fractions of the text width could become `fr` units. Theme colours with tint/shade should be
inverted back to `theme.accent1.darker(25%)` (the inverse of `apply_tint_shade`).

### 4.8 Text escaping and typography
Characters that are significant in the markup (`* _ = | \ % #`) must be escaped. Typographic replacements are
subtler: if the lowering turns `--` into an en dash, the decompiler may print `--` only where recompiling gives the
exact same character. The rule is general: **re-sugar only when the recompile is equivalent.**

### 4.9 Things that will stay out of reach (or raw)
SmartArt, OLE objects, VBA, charts, DrawingML shapes and text boxes, complex floating anchors, legacy form fields.
These become local raw blobs or warnings. Math is a nice middle ground: OMML → a LaTeX subset is feasible for common
cases, as the inverse of `math/`.

Following the "no silent loss" principle (`01-vision-and-scope.md` §1.3), the decompiler must report what it dropped
or approximated, both as a diagnostics report and as comments in the source (`% import: dropped field cache`).

## 5. The oracle: the compiler checks the decompiler

This is the most interesting property of the whole idea. **We own the compiler, so every decompilation can be
verified**:

```
D (original .docx) ──decompile──▶ S (.rdx) ──compile──▶ D' (.docx)          check: D ≈ D'
```

Equivalence can be defined at several strengths:

1. Byte-identical (only possible for redocx-produced input).
2. Canonical XML-equivalent (ignoring `rsid`, run boundaries and ID numbering).
3. Effective-formatting-equivalent: same text, same structure, same resolved properties per character. This is
   probably the right default.
4. Visually equivalent: render both through LibreOffice or Word to PDF and diff the pixels. The loosest check, and
   the safety net.

Consequences:

- **Abstraction becomes a search with a checker.** Try a more idiomatic rewrite (use a heading sugar, extract a
  macro, use `fr` widths); keep it only if the recompile is still equivalent. Decompilers for binaries cannot do this
  cheaply, but we can.
- **A free test corpus for the compiler.** Every golden test can be run round-trip: `rdx → docx → rdx' → docx''`
  and `docx'' ≈ docx`. It is effectively a fuzzer for both directions.
- **LLMs become safe to use.** Naming styles, labels and macros, or deciding "is this bold paragraph a heading", are
  judgement calls an LLM is good at. Any rewrite it proposes is checked by the oracle, so it can make the source
  nicer but never make it wrong.

## 6. The killer use case might be round-trip editing, not import

The vision (`01-vision-and-scope.md` §1.2) promises that "a colleague who opens the file in Word … can continue editing
it". Then what? Today, those edits are stranded in the `.docx`.

If the compiler embedded provenance in its output (a custom XML part holding a source hash and a map from
`w14:paraId` to source spans), then for a `.docx` that started as redocx output:

```
S (source) ──compile──▶ D ──colleague edits in Word──▶ D'
                         └────── diff D → D' at IR level ──────▶ patch to S
```

This is a three-way problem with a known base, which is much easier than decompiling from nothing. Text edits inside
a paragraph map back to a source span. Edits inside a macro expansion are ambiguous and are reported as conflicts.
Tracked changes and comments from reviewers could even become a reviewable patch or source comments. "Documents as
code, but reviewers stay in Word" would be a strong story.

## 7. Abstraction recovery (L3), the playful part

- **Library signatures.** Recognise expansions of `std:` macros and of user macro libraries passed on the command
  line: a one-cell, borderless, shaded table whose first paragraph is bold and keep-with-next is `\callout`. With
  parameters, matching becomes *anti-unification* (find the most specific template that covers both trees).
- **Macro discovery.** Find repeated IR subtrees that differ only in leaves, and propose a `\define`. This is clone
  detection or grammar compression (Sequitur and Re-Pair style). Thresholds matter; three repetitions of a
  five-node pattern is probably a macro, two of a two-node one is noise.
- **`\let` recovery.** Repeated literal strings such as product names or document IDs. Risky; opt-in only.
- **Style inheritance recovery.** Given a flat list of styles, find the `basedOn` tree that minimises the total
  printed properties. It is a nice small optimisation problem.

## 8. So, how realistic is it?

| Input                                                     | Realistic outcome                                         | Effort     |
| --------------------------------------------------------- | --------------------------------------------------------- | ---------- |
| redocx output, unedited                                 | L2 source, recompiles byte-identical                      | Small      |
| redocx output, edited in Word (with provenance)         | Patch back to the original source                         | Medium     |
| Disciplined Word docs (styles and template used properly) | Good L2 source with fields, captions and refs             | Medium     |
| Typical real-world docs (formatting soup, pasted content) | Faithful L1, idiomatic only with heuristics or a style map| Open-ended |
| SmartArt, OLE, drawing canvases                           | Local raw blobs or warnings                               | n/a        |

The short answer: **a faithful decompiler is very realistic; an idiomatic one is an asymptote.** The oracle in §5 is
what turns the asymptote into steady, measurable progress instead of a pile of heuristics nobody trusts.

## 9. Side benefits

- **Adoption.** Nobody starts from a blank page. "Point it at your last spec, get a `.rdx` you can build" is the most
  convincing demo the project could have.
- **A feature-gap detector.** Run the decompiler over a corpus of real documents and count each fallback to raw
  OOXML by type. The result is a frequency-ranked list of missing language features: a data-driven roadmap.
- **Learning tool.** "Build the thing in Word, decompile it, see how redocx spells it." This is the
  `tools/word-probe/` workflow turned into a user feature.

## 10. A cheap first experiment (if ever)

Before building anything idiomatic: read a dozen real `.docx` files into the IR and measure the share of paragraphs
and runs that could be printed at L1 and L2 without a raw fallback. That one number would say more about realism than
this whole note.

Prior art to look at: Pandoc's docx reader (lossy AST), mammoth.js (style maps), python-docx and docx2python
(reading), and binary decompilers (Ghidra, RetDec) for the structuring and signature ideas.
