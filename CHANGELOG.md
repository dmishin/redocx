# Changelog

Changes that users see, newest first. The language follows the compatibility rule in
[decision Q14](docs/08-open-questions.md): a source that compiles keeps its meaning in later 0.x versions, and
anything that would change it is listed here first as a deprecation warning.

## Unreleased

- **Math:** more environments: `dcases`, `rcases`, `alignedat`, `multlined` and the starred matrices of
  mathtools with a column alignment (`pmatrix*[r]`). The old font switches `{\bf x}`, `\rm`, `\it`,
  `\sf`, `\tt` and `\cal`; `\emph`, `\textsl`; `\textsf`, `\texttt` and `\textsc` as plain text with a
  warning. Spaces with a length (`\hspace{1em}`, `\mkern3mu`) become the nearest Unicode spaces.
  `\tag`, `\hline`, `\cline` and `\hdotsfor` are accepted with a warning and ignored, `\shoveleft` and
  `\shoveright` without one. More AMS symbols: `\Lsh`, `\lll`, `\ggg`, `\bigcirc`, `\Finv`, the harpoons.
- **Math:** more structures: `\overrightarrow` and the other arrows, brackets and parentheses over and
  under; the extensible arrows `\xrightarrow[below]{above}` and friends; `\prescript`; `\middle`;
  `\substack` and `subarray`; `\genfrac` and TeX's `\over`, `\atop`, `\choose`, `\brack`, `\brace`;
  `\mathclap`, `\mathllap`, `\mathrlap`; `\varlimsup` and the other amsmath limits. `\leftroot` and
  `\uproot` are accepted and ignored. Import reads `m:sPre`, separators in `m:d` and arrows under
  their label, and prints them with these commands.
- **Math:** colour: `\textcolor`, `\color`, `\colorbox` and `\fcolorbox`, with xcolor's base names, mixes
  (`red!30`) and the `HTML`, `rgb`, `RGB`, `cmyk` and `gray` models. Import reads coloured and shaded math
  and prints it with these commands.
- **Math:** boxes and phantoms: `\boxed`, `\fbox`, `\cancel`, `\bcancel`, `\xcancel` (Word's border box) and
  `\phantom`, `\hphantom`, `\vphantom`, `\smash` (Word's phantom). `\cancelto` is a warning: Word has no
  arrow, so the value is written as a superscript. Import prints them as these commands.
- **Math (fix):** empty scripts (`y^{}`, `x_{}^{}`) wrote OMML that fails the schema; they are now empty
  arguments, as Word writes an empty placeholder. Every build now checks that each math object has the arguments
  the schema requires; what is missing is an error, `RDX0402`, and nothing is written.
- **Math (fix):** bold italic text in a formula (`m:nor` with `w:b` and `w:i`) kept neither; it now keeps both.
- **Math:** `f'^2` is accepted and means `f^{\prime 2}`, as in LaTeX.
- **Math:** more symbols (`\sqcup`, `\dashv`, `\varGamma` and the other italic capitals, `\Updownarrow`,
  `\oiint`, `\ddddot`, ...), the spaces `\thinspace` to `\enspace`, `\bmod`, `\pmod`, `\mod`, `\pod`, and
  `\mathbin`, `\mathrel` and the other class commands, which give their argument as it is.
- **Import:** equations come back as LaTeX (`\m{…}`, `$$ … $$`). What the LaTeX subset cannot say is kept as
  `\ooxml{…}` inside the formula, or the whole equation as `\raw[ooxml]{<m:oMath>…}`. Numbered equations come
  back numbered, labelled `{#eq:…}` when a cross-reference points at them; `--check` compares every equation.
- **Math:** `\ooxml{…}` in a formula holds raw OMML; `\raw[ooxml]` accepts whole equations.
- **Math (fix):** the rows of `aligned` and other equation arrays now align at `&` in Word. They were written with
  `m:aln`, which Word ignores; now `&` goes in the math text, as Word writes it.
- **Math:** `{…}^2` and `{…}_i` put the script on the whole group, as LaTeX does. It looks the same in Word; in
  Word's equation editor the group is now the base.

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
