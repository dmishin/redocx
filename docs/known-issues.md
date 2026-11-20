# Known issues

Defects and rough edges that are known and not yet fixed. Each entry says what happens, why, and what a fix would
take. Features left out on purpose are in [Postponed features](09-postponed.md), not here. When an issue is fixed,
remove it from this list and say so in the commit and the [changelog](../CHANGELOG.md).

## Math

### `\&` in an aligned row is an alignment point in Word

Word marks alignment points in an equation array with `&` in the math text, and redocx writes `&` the same way.
A literal ampersand written as `\&` in a row of `aligned` (or any array) is therefore also an alignment point when
Word lays the equation out, and import reads it back as `&`. Outside arrays `\&` is a plain ampersand.

Fix: write a literal `&` in an array row as text (`m:nor`), which Word does not treat as an alignment point, or warn.
Check in Word first.

### Font commands are not groups for scripts

`{ab}^2` puts the superscript on the whole group, as in LaTeX. `\mathrm{sin}^2` does not: the formula becomes
`s`, `i` and `n²`, all upright. In LaTeX the superscript goes on the whole of `\mathrm{sin}`. Word shows both the
same, but in Word's equation editor the base is `n`. The same holds for `\mathbf`, `\mathbb` and the other font
commands. The importer avoids it by writing `{\mathrm{sin}}^2`.

Fix: in `math/parser.py` (`_Parser.atom`), treat a font command's argument as the base, as for `{…}`. This changes
the OMML that existing sources compile to, so it goes through the compatibility rule (Q14).

### Some math has no LaTeX form and imports as raw OMML

The importer prints each part of an equation as LaTeX, checks it by parsing it back, and writes what does not come
back the same as `\ooxml{…}`. The result compiles to the same equation, but is hard to read. These have no form in
the subset:

- characters: a plain space (U+0020), and `*`, `'`, `^`, `~` as math text (the parser reads them as `∗`, a prime, a
  superscript and a no-break space);
- a math font given without a style (`m:scr` without `m:sty`);
- linear and skewed fractions (`a/b` built up as `m:f` with `m:type` `lin` or `skw`), and a fraction without a bar
  outside parentheses (`\binom` is only the parenthesised form);
- a matrix with left-aligned columns, except inside a left brace (`cases`);
- accent, brace and big-operator characters that are not in `math/symbols.py`;
- `m:box`, `m:borderBox`, `m:sPre`, `m:phant`, a delimiter with separators (`(a|b)`) or with `m:grow`;
- a matrix whose last row is one empty cell (the parser drops a final empty row);
- formatting on math text (colour, size, highlight).

Each needs a language addition (for example `\ ` for a plain space, a command for a prescript) and is a design
decision (docs/08) before it is code.

### Word's brace with conditions is not `cases`

Word centres the columns of the matrix inside a left brace. `cases` is left-aligned, so a brace with conditions
from Word imports as `\left\{\begin{matrix}…\end{matrix}\right.`. That compiles to the same equation, but it is not
what someone would write. Fix: print centred ones as `cases` too; this changes the alignment in Word, so decide
first whether that is wanted.

### Printed LaTeX is compact

The importer writes formulas without spaces (`x=\frac{-b\pm\sqrt{b^2-4ac}}{2a}`). It puts the operand of a big
operator in braces when it has more than one atom (`\sum_{i=1}^n{i^2}`). Word stores parentheses as delimiter
objects, so each pair becomes `\left( … \right)` (`f\left(x\right)`). Everything compiles to the same equation; it
is only less readable than hand-written source. Fix: spaces around relations and binary operators, and plain `(`
`)` where the delimiter holds a single row of simple content. The printed form must still parse back the same.

### A prime followed by a superscript is rejected

`f'^2` is an error, "double superscript: use braces, x^{a^b}". In LaTeX it is valid and means `f^{\prime 2}`: the
prime is a superscript, and the `^2` joins it. The parser (`math/parser.py`, `_Parser.atom`) turns primes into a
superscript and then rejects any `^` that follows. `f''_1` and `f^{\prime 2}` work.

Fix: when the superscript so far holds only primes, append the `^` argument to it. Found by the math benchmark
(`prime-and-sup`).

### LaTeX commands the subset does not have

`tools/math-bench/make_bench.py` compiles the 349 formulas of `tools/math-bench/formulas.json` (written without
regard to what redocx supports) and puts each next to its LaTeX picture in `tools/math-bench/out/bench.docx`. On the
first run, redocx rejected 112. Apart from the prime bug above, every rejection is "unknown math command" or
"unknown environment" (RDX0311). By kind, with a rough cost:

- Symbols only, a table entry each in `math/symbols.py`: `\varkappa`, `\varGamma` (and the other `\var` capitals),
  `\hslash`, `\Bbbk`, `\sqcup`, `\sqsubset`, `\dashv`, `\oslash`, `\boxplus`, `\intercal`, `\smallsetminus`,
  `\leftrightharpoons`, `\Uparrow`, `\nrightarrow`, `\nsubseteq`, `\gtrapprox`, `\eqqcolon`, `\measuredangle`,
  `\blacksquare`, `\maltese`, `\clubsuit`, `\backprime`, `\dotsb`, `\iiiint`, `\idotsint`, `\oiint`,
  `\ointctrclockwise`, `\ulcorner`, `\ddddot`; `\Updownarrow` and `\backslash` as delimiters; `\biggm`.
- Spacing, as Unicode spaces or nothing: `\enspace`, `\thickspace`, `\hspace{…}`, `\mkern`, `\mathstrut`.
- Functions and operators: `\bmod`, `\pmod`, `\mod`, `\varlimsup`, `\injlim`, `\mathopen`, `\mathbin`,
  `\mathrel`, `\mathnormal`.
- Structures that map to an OMML object:
  - `\phantom`, `\smash` (`m:phant`);
  - `\boxed`, `\fbox` (`m:borderBox`);
  - `\prescript`, `\sideset` (`m:sPre`);
  - `\overrightarrow`, `\xrightarrow` and the other extensible arrows (`m:groupChr`);
  - `\overbracket`;
  - `\middle` (separators in `m:d`);
  - `\substack` and `subarray` (a one-column `m:eqArr` or matrix);
  - `\genfrac`, `\over`, `\choose`;
  - `\leftroot`.
- Environments: `alignedat`, `multlined`, `dcases`, `dcases*`, `rcases`, `pmatrix*`, `bmatrix*`; `\hline`,
  `\cline` and `\hdotsfor` in arrays.
- Text and font switches: `\textsf`, `\textsc`, `\rm` and the other old switches.
- Colour and marks: `\color`, `\textcolor`, `\colorbox`, `\fcolorbox`, `\cancel`, `\cancelto`. Word math text can
  be coloured; it has no strike-out mark, so cancel needs a design decision.
- Numbering: `\tag` and `\tag*`. Display equations are numbered by `\math[id=…]`, so this is a design decision
  (docs/08): ignore with a warning, or take the tag as the number.
- No equivalent: `\mathchoice`, `\raisebox`, `\rule`, `\hbox`.

Accepted formulas can still come out wrong; the benchmark is checked by eye in Word. Rerun it after a change to
`math/`: `uv run python tools/math-bench/make_bench.py`.

## Import

### Captions numbered per chapter are not captions

A caption whose number is per chapter is written as `Figure {STYLEREF "Heading 1" \s}–{SEQ Figure \s 1}`. The
importer (`importer/labels.py`, `_caption`) only recognises `Figure {SEQ Figure}`. Such captions import as Caption
paragraphs with their fields and bookmarks as raw XML, and references to them as `\field{REF …}` instead of
`\caption` and `@fig:…`. The thesis example shows it. The result compiles back to the same document.

Fix: accept the STYLEREF form in `_caption` and set `captions=(chapter-numbers, …)` from it, the same way as for
equations.

### Equation numbering per chapter is a guess

When every numbered equation has a number like "2.1", the importer sets `captions=(chapter-numbers)` with the
separator it saw. It assumes the chapters are Heading 1. With other chapter headings, the recompiled numbers differ
and `--check` reports it. Word's own `#(1)` numbers are typed text, and redocx numbers equations itself. Numbers
typed out of order (1, 2, 5) are therefore renumbered (1, 2, 3), and `--check` reports that as well.

### Display equations that share a paragraph

A display equation (`m:oMathPara`) in a paragraph that also has text is printed as inline math. Paragraph
formatting on a paragraph that holds only a display equation is dropped. A bookmark around an equation number that
no cross-reference points at is dropped too, unless Word hid it (`_…`). The import report counts each of these.
