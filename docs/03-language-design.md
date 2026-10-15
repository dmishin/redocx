# 3. Language Design

> This is a **draft** for discussion. The examples show intent. The exact grammar is fixed in M1.
> File extension: `.wgx`. Encoding: UTF-8.

## 3.1 Design goals for the syntax

0. **DOCX is the only target.** wordgenx is not a universal, output-neutral markup language. Every construct exists
   because Word has a matching concept, and it is named and shaped after that concept. We do not add abstractions to
   make documents portable to HTML or PDF. When "the obvious markup design" and "how Word models it" disagree, Word wins.
1. **Prose reads like prose.** A paragraph of plain text needs no markup.
2. **One uniform command form** covers everything that isn't common enough for sugar.
3. **Common things are short:** headings, emphasis, lists, simple tables, links, math.
4. **Options are named, typed and validated.** No positional magic. Unknown options are errors.
5. **Predictable parsing.** There is no expansion-order cleverness as in TeX. The grammar is context-sensitive only in
   well-defined *modes* (markup, code, math, raw, style sheet).
6. **Every sugar has a command equivalent**, so tools such as formatters, the importer and the LSP can always print the canonical form.

## 3.2 Lexical basics

| Construct | Syntax | Notes |
|-----------|--------|-------|
| Command | `\name[options]{body}{body2}…` | `[options]` and bodies are optional, as each command defines |
| Options | `key=value, key2=value2, flag` | Values: number+unit, string, identifier, color, list `(a, b)`, nested `{…}` |
| Paragraph break | blank line | As in TeX and Markdown |
| Line break | `\br` | `w:br` |
| Page / column break | `\pagebreak`, `\columnbreak` | |
| Comment | `% …` to end of line | As in TeX. A literal percent sign is `\%`. There are no block comments. Because `50% of` is common in business text, the compiler warns when `%` directly follows a digit (likely a forgotten `\%`). |
| Escape | `\\ \% \{ \} \[ \] \* \_ \` \$ \# \= \|` | Escapes the next character |
| Non-breaking space | `~` | As in TeX. Literal tilde is `\~`. |
| Typographic | `--` en dash, `---` em dash, smart quotes | Smart punctuation can be turned off per document |
| Identifier/label | `{#label}` after a block, or `id=label` option | Creates a bookmark |

### Units

`pt`, `cm`, `mm`, `in`, `pc`, `em` (relative to current font size), `%` (where meaningful), `fr` (table column fractions),
`lines` (line-spacing multiples), plus the raw units `twip` and `emu` for exactness. The compiler converts to the OOXML
unit each property needs (twips, half-points, eighths of a point, EMUs, fiftieths of a percent).

### Colors

`#RRGGBB`, CSS-like named colors (`navy`), `auto`, and **theme colors** with tint and shade:
`theme.accent1`, `theme.text1.lighter(40%)`, `theme.accent2.darker(25%)`. These emit `w:themeColor`, `w:themeTint`
and `w:themeShade`, so they follow theme changes in Word.

## 3.3 Document skeleton

```wgx
\document[template="templates/corporate.dotx", lang=en-US, smart-quotes=on]

\meta[
  title    = "wordgenx Architecture Specification",
  author   = "Jane Doe",
  subject  = "Compiler design",
  keywords = (docx, compiler),
  custom.project-code = "WX-001",   % custom document property
]

\use{styles/house.wgxs}             % style sheet (see §3.5)

\page[size=A4, margins=(top=2.5cm, bottom=2.5cm, inside=3cm, outside=2cm), mirror]
\header[first]{}                    % empty first-page header
\header{ \field{STYLEREF "Heading 1"} \tab \field{TITLE} }
\footer{ \align[center]{Page \pagenum of \numpages} }

\titlepage{ … }

\toc[levels=1-3, title="Contents"]

= Introduction {#intro}
…
```

Everything before the first content is *preamble*. Settings commands can also appear later. For example, `\page` after
content starts a new section (§3.9).

## 3.4 Inline markup

| Sugar | Meaning | Command form |
|-------|---------|--------------|
| `*text*` | strong (style `Strong` or bold) | `\strong{text}` |
| `_text_` | emphasis | `\emph{text}` |
| `` `code` `` | inline code (character style `Code`) | `\code{code}` |
| `$x^2$` | inline math | `\math{x^2}` |
| `<https://…>` | autolink | `\link{https://…}` |
| `[text](url)` | hyperlink | `\link[url]{text}` |
| `@label` | cross-reference | `\ref{label}` |

Sugar maps to **semantic character styles** (`Strong`, `Emphasis`, `Code`), which are created if the template lacks them.
A setting can map them to direct bold or italic instead.

Direct formatting is always available:

```wgx
\font[family="Consolas", size=10pt, color=theme.accent1, caps, spacing=0.5pt]{text}
\b{bold} \i{italic} \u[double]{underlined} \s{struck} \sup{2} \sub{i} \hl[yellow]{highlight}
\style{Term}{defined term}        % apply a named character style
\lang[de-DE]{Fahrvergnügen}       % language, for spell-check and hyphenation
\hidden{text}                     % hidden text
```

## 3.5 Styles and style sheets

Styles are the center of the design. A style sheet (`.wgxs`, or inline in `\styles{…}`) uses a CSS-like block syntax
with Word's property names:

```wgxs
defaults {
  font: theme.minor; size: 11pt; lang: en-US;
  space-after: 8pt; line-spacing: 1.08;
}

paragraph "Body Text" : Normal {          % : basedOn
  first-line-indent: 0; space-after: 6pt; justify;
  widow-control;
  next: "Body Text";
}

paragraph "Heading 1" : Normal {
  font: theme.major; size: 16pt; color: theme.accent1.darker(25%);
  space-before: 24pt; space-after: 6pt; keep-with-next; keep-lines;
  outline-level: 1;
  numbering: Headings level 1;            % link to multilevel list (§3.7)
  page-break-before: false;
}

character Term { italic; color: theme.accent2; }

linked "Quote" : Normal {                 % linked paragraph+character style
  indent: (left=1cm, right=1cm); italic;
  border-left: single 3pt theme.accent1 space=6pt;
}

table "Data Grid" : "Table Grid" {
  borders: (all = single 0.5pt #BFBFBF);
  cell-margins: (left=0.1cm, right=0.1cm);
  header-row { bold; shading: theme.accent1; color: white; repeat; }
  banded-rows { odd { shading: #F2F2F2; } }
  first-column { bold; }
}
```

- `paragraph`, `character`, `linked`, `table` and `numbering` style kinds map one-to-one to Word style types.
- Styles that already exist in the template can be **modified** (`modify paragraph "Heading 1" { color: … }`) or
  **replaced**.
- Unknown properties are compile errors and suggest the nearest name.
- Escape hatch: `ooxml.pPr: "<w:snapToGrid w:val='0'/>"` injects raw property XML.

The full property vocabulary is in the [feature matrix](05-feature-matrix.md). It is generated from one
**property registry** in code, which also drives docs, LSP completion and validation.

## 3.6 Paragraph-level formatting

```wgx
\p[style="Body Text", align=center, space-before=12pt, keep-with-next]{A paragraph with options.}

\align[right]{Short form for alignment.}

\tabs[(8cm, center), (16cm, right, leader=dot)]{Left\tab Centered\tab Right}
```

Paragraph options and style-sheet properties use the **same vocabulary**.

## 3.7 Headings and lists

```wgx
= Heading 1            % maps to style "Heading 1"
== Heading 2
=== Heading 3 {#sec:details}
=* Unnumbered heading  % suppress numbering, keep outline level

- bullet
- bullet
  - nested bullet
1. numbered
2. numbered
   a. nested, lettered     % format comes from the list definition, not the marker
```

Lists use **Word multilevel numbering definitions** (`abstractNum` / `num`) declared in style sheets:

```wgxs
numbering Headings {
  level 1 { format: decimal;     text: "%1";       style: "Heading 1"; }
  level 2 { format: decimal;     text: "%1.%2";    style: "Heading 2"; }
  level 3 { format: decimal;     text: "%1.%2.%3"; style: "Heading 3"; }
}

numbering Legal {
  level 1 { format: decimal; text: "%1.";   indent: 0.75cm hanging; legal; }
  level 2 { format: lower-letter; text: "(%2)"; indent: 1.5cm hanging; }
  level 3 { format: lower-roman;  text: "(%3)"; indent: 2.25cm hanging; }
}

numbering Checklist { level 1 { bullet: "☐" font: "Segoe UI Symbol"; } }
```

```wgx
\list[Legal]{
  - First clause
    - sub-clause
  - Second clause
}
\list[Legal, continue]{ - Continues numbering from the previous Legal list }
\list[Legal, restart=5]{ - Starts at 5 }
```

The compiler manages `num` instances and `lvlOverride/startOverride`, so "restart" and "continue" behave as Word users
expect.

## 3.8 Tables

Pipe tables cover simple cases. The full command covers everything else.

```wgx
\table[style="Data Grid", header-rows=1, widths=(4cm, 1fr, 1fr), align=center, id=tbl:rev]{
  | Item     | Q1  | Q2  |
  | Revenue  | 10  | 12  |
  | Cost     | \cell[shade=#FFF2CC]{7} | 8 |
  | \cell[colspan=3, align=center]{*Total: 7*} |||
}
\caption[@tbl:rev]{Quarterly revenue}
```

Cells can hold any block content (paragraphs, lists, nested tables) using the block form:

```wgx
\table[widths=(1fr, 2fr)]{
  \row[header, height=1cm exact]{ \cell{Name} \cell{Description} }
  \row{
    \cell[rowspan=2, valign=center]{Merged}
    \cell{
      First paragraph.

      - a list inside a cell
    }
  }
  \row{ \cell{Second row, right column} }   % left cell covered by rowspan
}
```

Supported: column widths (absolute, %, fr, auto), horizontal and vertical merge, per-cell borders and shading, cell margins,
vertical alignment, text direction, row height (at-least or exact), repeat header rows, "can't split row", table alignment
and indent, floating tables, nested tables, and table-style conditional formatting flags (`look=(first-row, banded-rows)`).

## 3.9 Sections and page setup

`\page[…]` opens a **new section** (except in the preamble, where it configures the first section).

```wgx
\page[orientation=landscape, break=next-page]
\header{Landscape appendix header}        % headers belong to the current section
…wide table…
\page[orientation=portrait, columns=(count=2, gap=1cm, separator), break=continuous]
```

Options: size (`A4`, `Letter`, or `(w, h)`), orientation, margins (including gutter and mirror), columns (equal or
custom widths), break type (next-page, continuous, even, odd), vertical alignment, page-number format
(`decimal`, `lower-roman`, …) and restart, line numbering, page borders, text direction, title page (different
first page), and whether headers and footers link to the previous section (`link-headers=false`).

## 3.10 References: bookmarks, cross-references, captions, notes

```wgx
= Results {#sec:results}

\figure[id=fig:arch, width=12cm, align=center]{
  \image{img/architecture.png}
  \caption{Overall architecture}        % caption label "Figure" + SEQ field
}

As shown in @fig:arch (page \pageref{fig:arch}), see also \ref[form=number]{sec:results}.
\ref[form=full]{fig:arch}      % "Figure 3: Overall architecture"
\ref[form=label-number]{…}     % "Figure 3"  (default for figures)
\ref[form=text]{…}             % heading or caption text
\ref[form=above-below]{…}      % "above" / "below"

A claim.\footnote{With a footnote.} Another.\endnote{And an endnote.}
```

- Labels become **bookmarks**. References become `REF` / `PAGEREF` fields with the `\h` hyperlink switch. The compiler
  **pre-computes cached results**, such as "Figure 3", so the document looks right even before Word updates fields.
- Captions use `SEQ Figure` / `SEQ Table` / `SEQ Equation` fields, optionally with chapter numbers (`STYLEREF`), as
  Word's Insert Caption does.
- Unresolved references are compile errors.

## 3.11 Fields and generated content

```wgx
\toc[levels=1-3, hyperlinks, title="Contents"]
\listof[figures]   \listof[tables]
\index                         % from \ix{term} / \ix{term!subterm} entries (XE fields)
\pagenum   \numpages   \sectionpages
\date[format="d MMMM yyyy"]    % DATE field;  \date[fixed] for literal text
\field{AUTHOR}                 % any Word field, raw instruction
\field[result="cached text"]{IF { MERGEFIELD x } = 1 "yes" "no"}
```

`\field` is the general escape hatch. It takes the field instruction and an optional cached result. It emits
`w:fldChar` begin/separate/end, with nested fields supported.

## 3.12 Math

LaTeX-style math, compiled **directly to OMML** (Office Math). It is native and editable in Word's equation editor.

```wgx
Inline $e^{i\pi} + 1 = 0$ and display:

$$ \int_0^\infty e^{-x^2}\,dx = \frac{\sqrt{\pi}}{2} $$ {#eq:gauss}

$$\begin{aligned}
  a &= b + c \\
    &= d
\end{aligned}$$
```

Display equations with a label are numbered: an equation table or OMML `m:eqArr` with a `SEQ Equation` field, depending
on a setting. The supported LaTeX subset is documented. Unsupported macros are errors with a location.

## 3.13 Code blocks

````wgx
```python {#lst:hello, caption="Hello world", line-numbers}
def hello():
    print("hi")
```
````

Highlighted with Pygments into runs with character styles (`Code Keyword`, `Code String`, …) that are generated in the
style sheet and themeable, or into direct colors. The block uses a `Code Block` paragraph style.

## 3.14 Images, drawings, boxes

```wgx
\image[width=8cm, alt="Logo"]{img/logo.png}                     % inline
\image[float, wrap=square, side=right, x=(0cm, from=margin), y=(0cm, from=paragraph), width=5cm]{img/photo.jpg}
\textbox[width=6cm, border=single 1pt theme.accent1, float, wrap=tight]{ Callout content… }
\watermark[text="DRAFT", color=#D9D9D9, rotation=-45]
```

## 3.15 Collaboration features

```wgx
\comment[author="J. Doe", date=2026-10-15]{reviewed text}{Is this number right?}
\ins[author="J. Doe"]{inserted text}   \del[author="J. Doe"]{deleted text}   % tracked revisions
\control[type=dropdown, items=("Draft","Final"), tag=status]{Draft}            % content control
\control[type=checkbox, checked]
\control[type=text, placeholder="Enter name", lock=content]
```

A later CLI feature, `wordgenx diff old.wgx new.wgx -o redline.docx`, produces a **tracked-changes redline** between two
versions of a source. This would be very useful for legal and specification work.

## 3.16 Macros, includes, variables

```wgx
\include{chapters/02-design.wgx}

\let{product}{wordgenx Compiler}
The \product is great.

\define{note}[kind=info]{body}{
  \table[style="Callout \kind", widths=(1fr)]{ \row{ \cell{\body} } }
}
\note[kind=warning]{Mind the gap.}
```

- Macros are **hygienic and declarative**. They use named parameters with defaults and substitute at the AST level, not
  through token-level expansion as in TeX.
- **There is no scripting in the language** (decided; see [decisions](08-open-questions.md)). There are no conditionals,
  loops or expressions. Programmability comes from the **Python API**: build the IR directly, or render `.wgx` from Jinja.
  Untrusted sources can then be compiled safely. If scripting is reconsidered later, the syntax `#{ … }` is kept
  reserved so it can be added without breaking documents.

## 3.17 Escape hatches (in order of preference)

1. `\style{Name}{…}`: use any style that exists in the template.
2. Option `ooxml.rPr` / `ooxml.pPr` / `ooxml.tblPr`: inject raw property XML into the generated element.
3. `\raw[ooxml]{<w:r>…</w:r>}`: raw run- or block-level WordprocessingML. It is checked for well-formedness, and namespaces
   are declared automatically.
4. `\embed-docx{part.docx}`: insert another document as `altChunk`. Word merges it on open.

Raw content is validated with the rest of the output. A bad escape hatch fails the build. It does not produce a corrupt file.

## 3.18 Diagnostics

Every error has a code (`WGX1203`), a span, a message and, where possible, a suggestion:

```
error[WGX1203]: unknown paragraph property `space-befor`
  --> chapters/02-design.wgx:14:22
   |
14 | \p[style=Body, space-befor=12pt]{…}
   |                ^^^^^^^^^^^ did you mean `space-before`?
```
