# Math and code

## Math

Formulas are written in LaTeX notation and compiled to Word's own equations (OMML), which Word users can edit in its
equation editor.

```rdx
Inline, \m{e^{i\pi} + 1 = 0}, and on a line of its own:

$$ \int_0^\infty e^{-x^2}\,dx = \frac{\sqrt{\pi}}{2} $$ {#eq:gauss}

$$\begin{aligned}
  f(x) &= (x + 1)^2 \\
       &= x^2 + 2x + 1
\end{aligned}$$ {#eq:square}

By @eq:gauss and @eq:square, the integral is finite.
```

- `\m{…}` is inline math; `$` alone is just a dollar sign. `$$ … $$` at the start of a line, or
  `\math[id=eq:x]{…}`, is a display equation.
- A labelled display equation is numbered "(1)", at the right margin, the way Word numbers equations; `@eq:x`
  prints "(1)" and `\ref[form=number]{eq:x}` "1". `\math[numbered]` numbers one without a label,
  `\math[nonumber]` leaves a labelled one unnumbered. With chapter-numbered captions
  (`\settings[captions=(chapter-numbers)]`) equations are numbered "(3.2)".

The supported LaTeX:

| Kind | Commands |
|------|----------|
| Scripts | `x^2`, `x_i`, `x_i^2`, `x'`, `f'^2`; `{…}` groups (`{ab}^2` puts the script on the whole group); `\prescript{14}{6}{C}`, `\substack` |
| Fractions, roots | `\frac`, `\dfrac`, `\tfrac`, `\cfrac`, `\binom`, `\genfrac`, `{a \over b}`, `{n \choose k}`, `\sqrt`, `\sqrt[n]` |
| Big operators | `\sum \prod \int \iint \oint \bigcup \bigcap …`, `\limits`, `\nolimits` |
| Functions | `\sin \cos \log \ln \exp \lim \max \min …`, `\operatorname{argmin}`, `\mathop`, `\bmod`, `\pmod{n}` |
| Delimiters | `\left( … \right)`, with `[ ] \{ \} \| \langle \rangle \lfloor \rfloor \lceil \rceil .`; `\middle|`; `\big(` and friends |
| Environments | `matrix pmatrix bmatrix Bmatrix vmatrix Vmatrix array cases aligned align split gathered`, `pmatrix*[r]`, `dcases rcases alignedat multlined` |
| Accents, arrows | `\hat \bar \tilde \vec \dot \ddot`, `\overline \underline \overbrace \underbrace`, `\overrightarrow`, `\overbracket`, `\overset \underset`; `\xrightarrow[below]{above}` |
| Boxes, phantoms | `\boxed \fbox`, `\cancel \bcancel \xcancel \cancelto`, `\phantom \hphantom \vphantom \smash` |
| Colour | `\textcolor{red}{…}`, `{\color{blue} …}`, `\colorbox{yellow}{text}`, `\fcolorbox{red}{yellow}{text}`; names, `red!30`, `[HTML]{00A0FF}`, `[rgb]{1,0.5,0}` |
| Fonts, text | `\mathrm \mathbf \mathit \boldsymbol \mathbb \mathcal \mathfrak \mathsf \mathtt`, `{\bf x}`, `\text{…}`, `\textit \textbf \emph` |
| Symbols, spacing | Greek letters, relations, arrows, `\infty \partial \nabla \forall \exists`, `\not`; `\, \: \; \enspace \quad \qquad`, `\hspace{1em}`, `\mkern3mu` |

A command outside this list is an error at its place in the formula, with the nearest supported command.
A few are accepted with a warning and left out: `\tag` (number the equation with `{#eq:…}` instead),
`\hline` and `\cline` (Word's math has no rules), `\hdotsfor`.

### Raw OMML

For what LaTeX notation cannot say, such as a box that makes a group act as one operator, `\ooxml{…}` puts Word's own math XML
(OMML) into a formula. The rest of the formula stays LaTeX:

```rdx
It holds when \m{a \ooxml{<m:box><m:boxPr><m:opEmu m:val="1"/></m:boxPr><m:e><m:r><m:t>==</m:t></m:r></m:e></m:box>} b}.
```

The XML is checked when the document compiles: it must be `m:` elements that are valid at that place in an
equation, or the build stops with an error pointing at the `\ooxml`. Write `{`, `}` and `\` in its text as `&#x7B;`,
`&#x7D;` and `&#x5C;`. A whole equation can be raw too, with `\raw[ooxml]{<m:oMath>…</m:oMath>}`.

`redocx import` writes `\ooxml{…}` for the parts of an equation that it cannot print as LaTeX (see
[Import](import.md)).

## Code blocks

````rdx
```python {#lst:hello, caption="Saying hello", line-numbers}
def hello(name):
    print(f"Hello, {name}")
```

@lst:hello prints a greeting.
````

- The language is any Pygments name (`python`, `json`, `cpp`, `sql`, `yaml`, …) or `rdx` and `rdxs`. An unknown
  language is a warning, and the code is shown plain; so is code without a language, or with `highlight=off`.
- The block uses the *Code Block* paragraph style; each kind of token has a character style (*Code Keyword*,
  *Code String*, *Code Comment*, …) coloured from a Pygments theme, `vs` unless
  `\settings[code-theme=friendly]` says otherwise. Restyle any of them in a style sheet:
  `character "Code Keyword" { bold; color: #7F0055; }`.
- Options: `{#label}`, `caption="…"` (a *Listing* caption), `line-numbers`, `first-line=10`.
- Spaces and line breaks are kept exactly. `` `inline code` `` uses the *Code* character style.
