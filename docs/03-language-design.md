# 3. Language Design

> **Status:** draft v0.2. It includes the decisions from the M0 syntax spike
> ([findings](../examples/spike/FINDINGS.md), referenced below as F-n). The exact grammar is fixed in M1.
> File extensions: `.wgx` (documents and macro files), `.wgxs` (style sheets). Encoding: UTF-8.

## 3.1 Design goals for the syntax

0. **DOCX is the only target.** wordgenx is not a universal, output-neutral markup language. Every construct exists
   because Word has a matching concept, and it is named and shaped after that concept. We do not add abstractions to
   make documents portable to HTML or PDF. When "the obvious markup design" and "how Word models it" disagree, Word wins.
   Conveniences without a Word counterpart (theorems, callouts, subfigures) live in the standard macro library (§3.18),
   not in the core.
1. **Prose reads like prose.** A paragraph of plain text needs no markup. Currency (`$48.2M`) and percentages (`12%`)
   need no escaping.
2. **One uniform command form** covers everything that isn't common enough for sugar.
3. **Common things are short:** headings, emphasis, lists, simple tables, links, references.
4. **Options are named, typed and validated.** No positional magic. Unknown options are errors.
5. **Predictable parsing.** There is no expansion-order cleverness as in TeX and no heuristics. The grammar is
   context-sensitive only in well-defined *modes* (markup, string, raw, math, code, style sheet).
6. **Every sugar has a command equivalent**, so tools such as formatters, the importer and the LSP can always print the
   canonical form.
7. **One body per command.** Extra content goes in named options (F-10).

## 3.2 Lexical basics

| Construct | Syntax | Notes |
|-----------|--------|-------|
| Command | `\name[options]{body}` | `[options]` and `{body}` are optional, as each command defines. At most one body. |
| Options | `key=value, key2=value2, flag` | Values: number+unit, string `"…"`, identifier, color, tuple `(a, b)` or `(k=v, …)`, markup `{…}` |
| Paragraph break | blank line | As in TeX and Markdown |
| Line break | `\br` | `w:br` |
| Tab | `\tab` | `w:tab` |
| Page / column break | `\pagebreak`, `\columnbreak` | |
| Comment | `%` to end of line, **only at the start of a line or after whitespace** | TeX-style (F-2). `12%` and `a%20b` are literal text. `\%` is always a literal percent sign. There are no block comments. |
| Escape | `` \\ \% \{ \} \[ \] \* \_ \` \$ \# \= \| \@ \~ `` | Escapes the next character |
| Non-breaking space | `~` | As in TeX |
| Typographic | `--` en dash, `---` em dash, smart quotes | Smart punctuation can be turned off per document |
| Label | `{#label}` after a heading, list-item marker or display block; or the `id=label` option | Creates a bookmark (§3.11) |
| Dollar sign | `$` | **Literal text** (F-3). Inline math is `\m{…}`. Only `$$` (display math) is special. |
| Reserved | `#{` | Reserved for possible future use. Use `\#` for a literal `#` before `{`. |

### Whitespace after commands (F-1)

Unlike TeX, **whitespace after a command is kept**. `The \product is great` prints a space between the two words.
`\name{}` is still allowed as an explicit terminator (`\product{}s`).

The one exception: **break-like commands** (`\br`, `\tab`, `\pagebreak`, `\columnbreak`) trim horizontal whitespace on both
sides, so `Name:\tab \br` and `\tab Page \pagenum` produce no stray spaces.

### Argument kinds (F-4)

Each command declares, in the command registry, how its body and each option value are read:

| Kind | Interpreted | Used by |
|------|-------------|---------|
| `markup` (default) | All inline markup and commands | `\p`, `\cell`, `\footnote`, … |
| `string` | Nothing except `\}` and `\\`; `_ ~ % * $` are literal | Paths, URLs, style names: `\image`, `\include`, `\link` target |
| `raw` | Nothing; braces must balance | `\field` instructions, `\raw[ooxml]` |
| `math` | LaTeX-math subset | `\m`, `$$…$$` |

For example, `\field{STYLEREF "Heading 1" \n}` works because the body is raw: `\n` is a field switch, not a command.
Nested fields inside a raw field body use Word's own display notation with field braces, `{ MERGEFIELD x }`, as in §3.12.

### Units

`pt`, `cm`, `mm`, `in`, `pc`, `em` (relative to current font size), `%` (where meaningful), `fr` (table column fractions),
`lines` (line-spacing multiples), plus the raw units `twip` and `emu` for exactness. The compiler converts to the OOXML
unit each property needs (twips, half-points, eighths of a point, EMUs, fiftieths of a percent).

Length-plus-rule values use tuples: `height=(1.5cm, at-least)`, `line-spacing=(12pt, exact)`.

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
  custom.project-code = "WX-001",   % custom document property (text; bare 3, 0.5, true: number, yes/no)
]

\settings[even-odd-headers, update-fields-on-open=on]

\use{styles/house.wgxs}             % style sheet (§3.5)
\use{macros/house.wgx}              % macro library (§3.17)

\section[size=A4, margins=(top=2.5cm, bottom=2.5cm, inside=3cm, outside=2cm), mirror, title-page]
\header[first]{}                    % empty first-page header
\header{\field{STYLEREF "Heading 1"} \tab \field{TITLE}}
\footer{\align[center]{Page \pagenum of \numpages}}

\p[style="Title"]{wordgenx Architecture Specification}
\pagebreak

\toc[levels=1-3, title="Contents"]

= Introduction {#sec:intro}
…
```

Everything before the first content is the *preamble*. `\document`, `\meta`, `\settings`, `\use` and `\styles` are
preamble-only. In the preamble, `\section` configures the first section. After content, it starts a new one (§3.10).

`\document[template="corp.dotx"]` (or `wordgenx build --template corp.dotx`) compiles into a Word template or
document. The output takes the template's styles, theme, page setup, headers and footers, and body content (a cover
page). Style sheets modify its styles. A built-in the markup needs is the template's own when it has one. Details:
architecture §4.6. `wordgenx build corp.wgx -o corp.dotx` writes a template;
[examples/templates/corporate.wgx](../examples/templates/corporate.wgx) is one.

### Document settings (F-14)

`\settings[…]` maps to `settings.xml`. Implemented: `protection=(mode=forms|read-only|comments|tracked-changes,
enforce)` (without a password), `update-fields-on-open=on|off`, `even-odd-headers`, `mirror-margins`,
`default-tab-stop=1.27cm`, `footnotes=(…)` and `endnotes=(…)` (§3.11), `captions=(chapter-numbers, …)` (§3.9).
Planned: `track-changes` (M5), `hyphenation=auto`.

## 3.4 Inline markup

| Sugar | Meaning | Command form |
|-------|---------|--------------|
| `*text*` | strong (character style `Strong`) | `\strong{text}` |
| `_text_` | emphasis (character style `Emphasis`) | `\emph{text}` |
| `` `code` `` | inline code (character style `Code`) | `\code{code}` |
| `\m{x^2}` | inline math (there is no sugar; `$` is literal) | `\m{x^2}` |
| `<https://…>` | autolink | `\url{https://…}` (string body) |
| `[text](url)` | hyperlink | `\link[to="url"]{text}` |
| `@label` | cross-reference (§3.11) | `\ref{label}` |

**Flanking rules.** `*` and `_` open emphasis only at a word start and close only at a word end, so `5 * 3`,
`snake_case_name` and `file_name.txt` need no escapes. `@` starts a reference only after whitespace or opening
punctuation and only when followed by a label character, so `jane@example.com` is literal.

Sugar maps to **semantic character styles**, which are created if the template lacks them, giving idiomatic Word output.
Direct formatting is always available:

```wgx
\font[family="Consolas", size=10pt, color=theme.accent1, caps, spacing=0.5pt]{text}
\b{bold} \i{italic} \u[double]{underlined} \s{struck} \sup{2} \sub{i} \hl[yellow]{highlight}
\style[Term]{defined term}        % apply a named character style
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

paragraph "Body Text" : Normal {          % ": Normal" means basedOn Normal
  first-line-indent: 0; space-after: 6pt; justify;
  widow-control;
  next: "Body Text";
}

paragraph "Heading 1" : Normal {
  font: theme.major; size: 16pt; color: theme.accent1.darker(25%);
  space-before: 24pt; space-after: 6pt; keep-with-next; keep-lines;
  outline-level: 1;
  numbering: Headings level 1;            % link to a multilevel list (§3.7)
}

character Term { italic; color: theme.accent2; }

linked "Quote" : Normal {                 % linked paragraph+character style
  indent: (left=1cm, right=1cm); italic;
  border-left: (single, 3pt, theme.accent1, space=6pt);
}

table "Data Grid" : "Table Grid" {
  borders: (all = (single, 0.5pt, #BFBFBF));
  cell-margins: (left=0.1cm, right=0.1cm);
  header-row { bold; shading: theme.accent1; color: #FFFFFF; repeat; }
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

### Style-sheet details (implemented in M1, `wordgenx.stylesheet`)

Style sheets come from `.wgxs` files loaded with `\use{file.wgxs}`, or from `\styles{…}` bodies in a document. Paths are
relative to the document. Sheets apply in document order.

- **Rules:** `defaults { … }`, `[modify|replace] paragraph|character|linked|table NAME [: BASE] { … }`, and
  `numbering NAME { level N { … } … }`. Names are words or `"quoted strings"`.
- **Declarations:** `key: value;`. A flag `key;` means `key: true`. Bare alignment words (`justify;`, `center;`, …) mean
  `align: …`. The `;` before a closing `}` may be omitted. Comments use `%`, as in documents.
- **Property routing:** each key is looked up in the property registry. Run properties go to the style's run formatting,
  paragraph properties to its paragraph formatting. Character styles accept only run properties.
- **Style-level keys:** `next`, `numbering: NAME [level N]`, `ui-priority`, `quick-format`.
- **Composite values:** `indent: (left=…, right=…, first-line=…, hanging=…)`, `indent: 0.75cm hanging` (left and hanging
  both 0.75cm), `indent: 1cm` (left). `line-spacing: 1.15` is a multiple. `line-spacing: (12pt, exact)` and `(12pt, at-least)`
  set a fixed height.
- **Defaults:** a paragraph style without `: BASE` is based on `Normal`. A character style has no base.
- **Redefinition:** declaring a style twice is an error. `modify` merges into an existing definition (or creates it, e.g.
  for a built-in style). `replace` starts from scratch.
- **Linked styles:** `linked Quote { … }` creates the paragraph style `Quote` and the character style `Quote Char`, linked
  to each other as Word does.
- **Numbering levels:** `format` (kebab-case Word formats: `decimal`, `lower-letter`, `upper-roman`, `bullet`,
  `decimal-zero`, …), `text` (`%1.%2`), `bullet: "•"`, `start`, `style`, `legal`, `suffix: tab|space|nothing`,
  `marker-align`, `restart-after`, `indent`. Any run property (`bold`, `font`, `color`, …) formats the marker.
  Undefined levels of the nine are filled in, continuing an outline pattern (`%1.%2` → `%1.%2.%3`). Linking a level to a
  style (`style: "Heading 1"`) and linking a style to a level (`numbering: Headings level 1`) are equivalent. The
  compiler completes the other side.
- **Table styles** (M2): `table NAME [: BASE] { … }` is a Word table style. At the top level, run and paragraph
  properties format all cell text; `borders`, `cell-margins`, `table-align` and `table-indent` go to the table
  (`w:tblPr`); `shading` and `valign` to every cell. Blocks format parts of the table (`w:tblStylePr`): `header-row`
  (alias `first-row`; `repeat;` also repeats it on each page), `last-row`, `first-column`, `last-column`,
  `banded-rows { odd { … } even { … } }`, `banded-columns { … }` and the corner cells `top-left-cell` …
  `bottom-right-cell`. Inside a block, `borders` and `border-*` are cell borders. A style without a base is based on
  Normal Table. Which parts apply is the table's `look` (Word's default: header row, first column, banded rows).
- **Borders and shading:** a border is `(style, width, colour, space=…)` in any order, with defaults single, 0.5pt
  and auto, or `none`. Styles are Word's line styles in kebab case (`single`, `double`, `dotted`, `dashed`, `thick`,
  `thin-thick-small-gap`, …). `borders: (…)` sets all six sides (top, left, bottom, right, inside-h, inside-v).
  `borders: (outer=…, inner=…, top=none)` sets groups and sides in order. `border-top: …` sets one side.
  `cell-margins` takes one length or `(top=…, left=…, bottom=…, right=…, horizontal=…, vertical=…, all=…)`.
  Shading takes RGB or theme colours (`theme.accent1.lighter(80%)`).
- **Built-ins:** referring to a Word built-in style (`: "Table Grid"`, `style: "Heading 1"`) is valid without
  defining it, and the compiler adds it. `modify` of an undefined built-in starts from the built-in definition.
- **Paragraph borders and shading:** `border-top`, `border-left`, `border-bottom`, `border-right`, `border-between`
  (between paragraphs that share the same borders) and `border-bar` take a border. `borders: (all=…, between=…)` sets
  them together; for paragraphs, `all` is the four sides of a box. `shading` is the background. The same keys work as
  `\p[…]` options. In table styles, `borders` and `shading` mean the table's and the cells'.
- **Not yet:** the `ooxml.*` escape hatch. Unlike documents, style sheets are parsed straight to an AST, not a lossless CST.
  A formatter for `.wgxs` will need one.
- **Diagnostics:** `WGX02xx`. Unknown properties suggest the nearest name. Unresolved style and numbering references are
  reported where they occur.

## 3.6 Paragraph-level formatting

```wgx
\p[style="Body Text", align=center, space-before=12pt, keep-with-next, id=par:key]{A paragraph with options.}

\align[right]{Short form for alignment.}

\tabs[(8cm, center), (16cm, right, leader=dot)]{Left\tab Centered\tab Right}
```

Paragraph options and style-sheet properties use the **same vocabulary**.

## 3.7 Headings, lists and numbered paragraphs

```wgx
= Heading 1 {#sec:intro}   % maps to style "Heading 1"
== Heading 2
=== Heading 3
=* Unnumbered heading      % suppress numbering, keep outline level

- bullet
- bullet
  - nested bullet
1. numbered
2. numbered
   a. nested               % the marker only signals "numbered"; the format comes from the definition
```

`=` is heading sugar only at the start of a line and followed by a space.

Lists use **Word multilevel numbering definitions** (`abstractNum` / `num`) declared in style sheets. A level can be
**linked to a paragraph style**, as in Word:

```wgxs
numbering Headings {
  level 1 { format: decimal; text: "%1";       style: "Heading 1"; }
  level 2 { format: decimal; text: "%1.%2";    style: "Heading 2"; }
  level 3 { format: decimal; text: "%1.%2.%3"; style: "Heading 3"; }
}

numbering MSA {
  level 1 { format: decimal;      text: "%1.";   indent: (left=1.25cm, hanging=1.25cm); style: "Clause 1"; }
  level 2 { format: decimal;      text: "%1.%2"; indent: (left=1.25cm, hanging=1.25cm); style: "Clause 2"; legal; }
  level 3 { format: lower-letter; text: "(%3)";  indent: (left=2.5cm,  hanging=1.25cm); style: "Clause 3"; }
}

numbering Checklist { level 1 { bullet: "☐"; font: "Segoe UI Symbol"; } }
```

**Clause-numbered documents (F-9).** `\list[Name]{…}` uses list sugar for any numbering. When the levels are style-linked, each item
gets that level's paragraph style automatically. A whole contract is one list, and **items can carry labels**:

```wgx
\list[MSA]{
1. {#cl:term} Term
   1. This Agreement commences on the Effective Date …
2. {#cl:charges} Charges and Payment
   1. {#cl:invoice} The Supplier shall invoice the Customer monthly …
   2. If any sum is not paid when due, the Supplier may:
      a. charge interest …; and
      b. suspend the Services, provided that clause @cl:invoice has been complied with.
}
\list[MSA, continue]{ … }        % continue numbering after intervening content
\list[MSA, restart=5]{ … }       % start at 5
```

- An item continues across lines indented to its text column. A blank line followed by indented text adds another paragraph
  to the same item (unnumbered continuation, as Word's Shift+Enter or "continue list" would).
- The compiler manages `num` instances and `lvlOverride/startOverride`, so "restart" and "continue" behave as Word users
  expect.

## 3.8 Tables

Pipe tables cover simple cases. The full command covers everything else.

```wgx
\caption[Table, id=tbl:rev]{Quarterly revenue}
\table[style="Data Grid", header-rows=1, widths=(4cm, 1fr, 1fr), columns=(left, decimal, decimal), align=center]{
  | Item     | Q1    | Q2    |
  | Revenue  | 10.5  | 12.25 |
  | Cost     | \cell[shade=#FFF2CC]{7} | 8 |
  | \cell[colspan=3, align=center]{*Total: 7*} |||
}
```

Pipe-table rules (F-15): cells are split on `|` **except** inside `` `code` ``, `\m{…}` or any command body. `\|` is a literal
pipe. A cell made only of `\cell[…]{…}` takes the options of that command.

**Column options:** `columns=(…)` gives per-column defaults: alignment (`left`, `center`, `right`, `decimal`) and
optionally `(decimal, tab=1.8cm)`. Decimal alignment emits a decimal tab stop in each cell paragraph, as Word does.

Cells can hold any block content (paragraphs, lists, nested tables) using the block form:

```wgx
\table[widths=(1fr, 2fr)]{
  \row[header, height=(1cm, exact)]{ \cell{Name} \cell{Description} }
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
vertical alignment, text direction, row height, repeat header rows, "can't split row", table alignment
and indent, floating tables, nested tables, and table-style conditional formatting flags (`look=(first-row, last-row, banded-rows)`).

## 3.9 Captions (F-5)

As in Word, **a caption is a paragraph**: style `Caption`, containing a label and a `SEQ` field
(`Figure {SEQ Figure \* ARABIC}`). **The caption owns the label.** A cross-reference to a figure or table points at a
bookmark around the caption, as Word's Insert Cross-reference does.

```wgx
\caption[Table, id=tbl:income]{Condensed income statement}   % above the table
\include{generated/q3-income-statement.wgx}

\figure[id=fig:arch, width=12cm, align=center]{               % sugar: image paragraph + caption, kept together
  \image{img/architecture.png}
  \caption{Overall architecture}                              % kind Figure inferred; \figure's id moves to the caption
}
```

- The kind (`Figure`, `Table`, `Equation`, `Listing`, or any custom kind) is given explicitly or inferred from the adjacent
  table, image, or code block. Without a neighbour to go by, the kind is `Figure`.
- The caption reads "Figure 1: text". It carries four bookmarks, one per reference form: label and number (the main
  label), number only, text only, and the whole caption. A caption above its table or code keeps with the next paragraph.
- `\figure` centres its paragraphs (or uses its `align`), gives images without a size its `width`, and keeps the
  paragraphs together on one page. A labelled `\figure` must contain a `\caption`.
- A block command on a line of its own ends the paragraph, so `\image{…}` and `\caption{…}` may sit on consecutive
  lines. A block command in the middle of a line of text is still an error.
- Chapter-numbered captions ("Figure 3.2") use a document setting:
  `\settings[captions=(chapter-numbers, chapter-style="Heading 1", separator=".")]`, which emits `STYLEREF` + `SEQ … \s 1`
  (separators: `.`, `-`, `:`, en and em dash). Numbers restart with each chapter, and references print "Figure 3.2".
- Code blocks and display equations can take `caption="…"` directly.

## 3.10 Sections and page setup (F-6)

`\section[…]` is **Word's section**. It sets the page layout and, after content, inserts a section break. It is not a heading.

```wgx
\section[orientation=landscape, break=next-page]
\header{Landscape appendix header}        % headers belong to the current section
…wide table…
\section[orientation=portrait, columns=(count=2, gap=1cm, separator), break=continuous]
```

Options: size (`A4`, `Letter`, or `(w, h)`), orientation, margins (including gutter; `inside=`/`outside=` or the
flag `mirror` turn on Word's mirror margins, a document-wide setting), columns (equal widths, gap, separator), break
type (next-page, continuous, even, odd), vertical alignment (`valign=top|center|bottom|justify`), page numbers
(`page-numbers=(format=lower-roman, start=1)`), line numbering
(`line-numbers=on` or `(count-by=5, start=1, distance=0.5cm, restart=each-page|each-section|continuous)`), title page
(different first page), and whether headers and footers link to the previous section (`link-headers=false`).
Columns of different widths, page borders and text direction come later (M4).

## 3.11 References, labels, notes

Labels become **bookmarks**. References become `REF` / `PAGEREF` / `NOTEREF` fields with the `\h` hyperlink switch. The compiler
**pre-computes cached results**, so the document reads correctly before Word updates fields. Unresolved references are compile
errors. References resolve across `\include`d files.

**What `@label` prints depends on what it points to** (F-7), matching Word's Insert Cross-reference defaults:

| Target | `@label` prints | Field |
|--------|-----------------|-------|
| Caption (figure, table, listing, custom kind) | "Figure 3.1" (label and number) | `REF` on the label-and-number part of the caption |
| Display equation | "(3.2)" | `REF` |
| Numbered heading, clause or list item | "4.2" (full-context paragraph number) | `REF \w` |
| Unnumbered heading or labelled paragraph | the text | `REF` |
| `\seq` counter (§3.13) | its number | `REF` |
| Footnote / endnote | the note number | `NOTEREF` |

```wgx
As shown in @fig:arch (page \pageref{fig:arch}), see clause @cl:invoice.
\ref[form=full]{fig:arch}         % "Figure 3: Overall architecture"
\ref[form=number]{fig:arch}       % "3"
\ref[form=text]{sec:results}      % heading or caption text
\ref[form=above-below]{fig:arch}  % "above" / "below"
\ref[form=relative]{cl:invoice}   % "(a)" (relative paragraph number, REF \r)

A claim.\footnote{With a footnote.} Another.\endnote{And an endnote.}
```

A lint warns when the word before `@label` repeats the label it prints, for example "Table @tbl:x" printing "Table Table 3".

Labels come from headings (`{#sec:x}`), list items (`1. {#it:x} …`), `\p[id=…]`, captions (`\caption[id=…]`,
`\figure[id=…]`, code blocks `{#lst:x, caption="…"}`) and counters (`\seq[…, id=…]`). Bookmark names are derived from the
label (`sec:intro` → `_Ref_sec_intro`, at most 40 characters). Cached results are computed the way Word computes them:
list and heading numbers are simulated from the numbering definitions, SEQ fields are counted in document order.
A `PAGEREF` cannot be known in advance; it gets "1" and the document asks Word to update fields when it opens.

**Notes.** `\footnote[id=fn:x]{…}` and `\endnote{…}` put the reference mark where they stand and their body in
`footnotes.xml` / `endnotes.xml`, styled `Footnote Text` / `Footnote Reference` (`Endnote …` for endnotes) and starting
with the note's own number, as notes inserted in Word do. The body may hold several paragraphs, lists and tables, but
no other note; notes cannot appear in headers or footers. Numbering is set document-wide with
`\settings[footnotes=(format=decimal|lower-roman|upper-roman|lower-letter|upper-letter|symbols,
restart=continuous|each-section|each-page, position=page-bottom|beneath-text, start=N)]` (endnotes: `restart` without
`each-page`, `position=section-end|document-end`). The compiler writes it to `settings.xml` and to every section,
because Word reads the section's copy. `@fn:x` prints the note number (`NOTEREF`); with `restart=each-page` the number
is left for Word to update.

References in headers and footers resolve like any other. Not yet: `form=relative` in full-context form (it prints
the paragraph's own number), and the "Table Table 3" lint.

## 3.12 Fields and generated content

```wgx
\toc[levels=1-3, hyperlinks, title="Contents"]
\listof[Figure]   \listof[Table]
\index[columns=2]                 % from \ix{term} / \ix{term!subterm} entries (XE fields)
\pagenum   \numpages   \sectionpages
\date[kind=fixed, format="d MMMM yyyy"]   % kind: fixed (default, text at build time) | today (DATE) | saved (SAVEDATE) | created (CREATEDATE)
\field{AUTHOR}                    % any Word field; the body is raw
\field{DOCPROPERTY "project-code"} % a custom property; its value is the cached result
\field[result="cached text"]{IF { MERGEFIELD x } = 1 "yes" "no"}
```

`\toc` and `\listof` write the `TOC` field with its entries already filled in: one paragraph per heading (styles
`TOC 1`–`TOC 9`) or caption (`table of figures`), each a link to a hidden `_Toc` bookmark with a `PAGEREF` for the page
number. Page numbers are left for Word, which updates them when the document opens. `title=` adds a `TOC Heading`
paragraph, which is not itself listed.

`\field` is the general escape hatch. It takes the raw field instruction and an optional cached result. It emits
`w:fldChar` begin/separate/end. `{ … }` inside the instruction are nested fields, in Word's display notation.

## 3.13 Custom counters (F-12)

```wgx
\seq[Req]                                    % SEQ Req, cached result computed by the compiler
\seq[Req, format=decimal, restart=heading-1] % restart at each Heading 1 (SEQ \s 1)
\seq[Req, id=req:auth]                       % labelled: @req:auth prints its number
```

## 3.14 Math

LaTeX-style math, compiled **directly to OMML** (Office Math). It is native and editable in Word's equation editor.
`$` is **not** a math delimiter (F-3).

```wgx
Inline \m{e^{i\pi} + 1 = 0} and display:

$$ \int_0^\infty e^{-x^2}\,dx = \frac{\sqrt{\pi}}{2} $$ {#eq:gauss}

$$\begin{aligned}
  a &= b + c \\
    &= d
\end{aligned}$$ {#eq:chain}
```

- Display math may also be written `\math[id=eq:gauss, numbered|nonumber]{…}`. A display equation is a paragraph of
  its own (`m:oMathPara`); inline math (`m:oMath`) sits in the text.
- **Numbering** (Q9, settled by surveying what Word writes): labelled display equations are numbered the way Word numbers
  them when you type `#(1)`: an `m:eqArr` whose row ends with `#` and the number in parentheses, which Word aligns to
  the right margin. The compiler computes the numbers (per chapter, "(3.2)", when
  `\settings[captions=(chapter-numbers, …)]` is set). In an `aligned` block the number goes on the last row.
  `@eq:gauss` prints "(1.1)": a `REF` to a bookmark around the number, with the parentheses outside the field, so
  Word's own update gives the same text. `\ref[form=number]{eq:gauss}` prints "1.1".
- Custom operators: `\operatorname{argmin}` (`\operatorname*` for limits underneath) or `\mathop{argmin}`.
- Unsupported commands are errors at their position in the document (`WGX0311`), with the nearest known command as a
  suggestion.

**The supported subset** (`wordgenx.math`):

| Kind | Commands |
|------|----------|
| Scripts | `x^2`, `x_i`, `x_i^2`, `x'`, `x''`; `{…}` groups |
| Fractions, roots | `\frac` (`\dfrac`, `\tfrac`, `\cfrac`), `\binom`, `\sqrt`, `\sqrt[n]` |
| Big operators | `\sum \prod \coprod \int \iint \iiint \oint \bigcup \bigcap \bigvee \bigwedge \bigoplus \bigotimes \bigodot`, with `\limits` / `\nolimits` |
| Functions | `\sin \cos \tan \log \ln \exp \lim \max \min \sup \inf \det \gcd …`, `\operatorname`, `\mathop` |
| Delimiters | `\left … \right` with `( ) [ ] \{ \} \| \langle \rangle \lfloor \rfloor \lceil \rceil .`; `\big(` and friends |
| Environments | `matrix pmatrix bmatrix Bmatrix vmatrix Vmatrix array cases aligned align split gathered`; `&` and `\\` |
| Accents, bars | `\hat \bar \tilde \vec \dot \ddot \check \breve \acute \grave`, `\overline \underline \overbrace \underbrace`, `\overset \underset \stackrel` |
| Fonts, text | `\mathrm \mathbf \mathit \boldsymbol \mathbb \mathcal \mathfrak \mathsf \mathtt`, `\text` |
| Symbols | Greek letters (upper-case upright), relations, arrows, operators, `\infty \partial \nabla \forall \exists …`, `\not` |
| Spacing | `\, \: \; \quad \qquad \ ~`; `\!` is ignored |

## 3.15 Code blocks

````wgx
```python {#lst:hello, caption="Hello world", line-numbers}
def hello():
    print("hi")
```
````

Highlighted with Pygments into runs with character styles (`Code Keyword`, `Code String`, …) that are generated in the
style sheet and themeable. The block uses a `Code Block` paragraph style. A `caption` creates a `Listing` caption that owns
the label.

- **Languages:** any Pygments lexer name or alias (`python`, `json`, `cpp`, `sql`, `yaml`, …), plus `wgx` and `wgxs`
  for wordgenx itself. An unknown language is a warning with the nearest name; the code is shown plain. No language, or
  `highlight=off`, means plain code.
- **Styles:** each token kind has a character style: `Code Keyword`, `Code Type`, `Code Constant`, `Code Builtin`,
  `Code Function`, `Code Decorator`, `Code Attribute`, `Code Label`, `Code Variable`, `Code String`, `Code Escape`,
  `Code Number`, `Code Comment`, `Code Preprocessor`, `Code Operator`, `Code Heading`, `Code Inserted`, `Code Deleted`.
  Their default look comes from a Pygments theme, `vs` unless `\settings[code-theme=friendly]` (any Pygments style)
  says otherwise. A style sheet or template restyles any of them (`character "Code Keyword" { bold; color: #7F0055; }`).
  Only styles that format something are applied, so tokens the theme leaves plain stay plain text.
- **Line numbers:** `line-numbers` numbers the lines from 1, `first-line=N` from N. The numbers are runs in the
  `Code Line Number` style, followed by a tab to a hanging indent, so wrapped lines stay aligned with the code.

## 3.16 Images, drawings, boxes

```wgx
\image[width=8cm, alt="Logo"]{img/logo.png}                     % inline; the body is a string (path)
\image[float, wrap=square, side=right, x=(0cm, from=margin), y=(0cm, from=paragraph), width=5cm]{img/photo.jpg}
\textbox[width=6cm, border=(single, 1pt, theme.accent1), float, wrap=tight]{Callout content…}
\watermark[text="DRAFT", color=#D9D9D9, rotation=-45]
```

## 3.17 Collaboration and forms

```wgx
\comment[author="R. Patel", date=2026-10-13, text="Please confirm the commercial position."]{thirty (30) days}
\comment[author="R. Patel", text={Rich comment with *markup*.}]{commented range}
\ins[author="J. Doe"]{inserted text}   \del[author="J. Doe"]{deleted text}          % tracked revisions

\control[type=text, tag=name, placeholder="Enter name", lock=content]
\control[type=dropdown, tag=status, items=("Draft", "Final")]{Draft}
\control[type=date, tag=start, format="d MMM yyyy"]
\control[type=checkbox, tag=agree, checked]
\control[type=rich-text, tag=address]
\control[type=picture, tag=signature]
\control[type=repeating, tag=contacts, min=1, max=3]{ …block content repeated per entry… }
```

Form protection is set with `\settings[protection=(mode=forms, enforce)]`.

A later CLI feature, `wordgenx diff old.wgx new.wgx -o redline.docx`, produces a **tracked-changes redline** between two
versions of a source.

## 3.18 Macros, includes, constants (F-8)

```wgx
\include{chapters/02-design.wgx}          % splice another source file here
\use{macros/house.wgx}                    % load macro definitions only (no content)

\let{product}{wordgenx Compiler}          % zero-parameter constant
The \product is great.

\define{field}[label, tag, type=text, placeholder="Click to enter"]{
  \p[style="Field Label"]{#label}
  \p{\control[type=#type, tag=#tag, placeholder=#placeholder]}
}

\define{callout}[title="Key takeaway", id]{
  \table[style="Callout", widths=(1fr), id=#id]{\row{\cell{
    \p[style="Callout Title"]{#title}
    #body
  }}}
}

\callout[title="Mind the gap"]{Volume growth is outpacing hub capacity.}
```

- A macro has named parameters and **at most one body**, available as `#body`. A parameter without a default is
  optional: when not given it expands to nothing, and an option whose value is that parameter is dropped.
- `#name` is valid **only inside a `\define` body**. It works in text and in option values. It never collides with command
  names.
- An `id` option can be forwarded with `id=#id`. An empty optional `id` is dropped.
- Macros are **hygienic and declarative**. They substitute at the AST level, not through token-level expansion as in TeX.
- **There is no scripting in the language.** There are no conditionals, loops or expressions. Programmability comes from the
  **Python API**: build the IR directly, or render `.wgx` from Jinja. Untrusted sources can then be compiled safely.

### Standard macro library (prelude) (F-13)

Conveniences that Word has no native concept for ship as a readable `.wgx` library, loaded with `\use{std:…}`, built only
from Word primitives:

| Module | Provides | Built from | Status |
|--------|----------|------------|--------|
| `std:callout` | `\callout[title, shade]`, `\note`, `\tip`, `\warning`, `\caution` (each with `title`) | A borderless one-cell table with a shaded cell and a bold title | M1 ✓ |
| `std:signature` | `\signature-block[party, name, role]`, `\approval[role]` | Borderless tables with underlined tabs as signature lines | M1 ✓ |
| `std:theorem` | `\theorem`, `\lemma`, `\definition`, `\proof` | Styled paragraphs + `SEQ Theorem` | M2 (needs `\seq`) |
| `std:subfigure` | `\subfigures{…}` | Borderless layout table with images and letter-format `SEQ` captions | M2 (needs captions) |

The modules live in `src/wordgenx/std/` as ordinary macro files. Users can copy and override any of them. A module is
loaded once even if `\use`d repeatedly. Because the language has no conditionals, a variant such as the callout kind
is a separate macro (`\warning`), not a `kind=` option. Writing the library confirmed that macros calling macros,
`#body` forwarding and parameters in option values are expressive enough.

## 3.19 Citations and bibliography (F-11)

```wgx
\bibliography[file="refs.bib", style="apa"]       % preamble: data (BibTeX/CSL-JSON) and CSL style
… as shown by \cite{daganzo2005} …                % (Daganzo, 2005)
\cite[page=214]{daganzo2005}                      % (Daganzo, 2005, p. 214)
\cite[prefix="see", suffix="Lemma 2"]{tib2013}    % (see Tibshirani, 2013, Lemma 2)
\cite{ortuzar2011, daganzo2005}                   % multiple keys
\textcite{tibshirani1996}                         % Tibshirani (1996)
\printbibliography[title="References"]
```

Citations are rendered with CSL (citeproc-py) as text with hyperlinks to bookmarks in the bibliography. The bibliography
uses a `Bibliography` paragraph style. Native Word citations (`CITATION` fields, `sources.xml`) are a later option.

- **Data:** `file=` takes one file or a tuple; `.bib` is BibTeX, `.json` is CSL-JSON, and later files win on a shared key.
  Keys are case-insensitive. `\bibliography` belongs in the preamble.
- **Styles:** `style=harvard` (Cite Them Right, bundled with citeproc-py) is the default. Also bundled: `apa` (APA
  7th edition), `ieee` (IEEE Reference Guide) and `chicago` or `chicago-author-date` (Chicago 18th edition,
  author-date). They come unchanged from the CSL styles repository and are under CC BY-SA 3.0; the source commit and
  the licence are in `src/wordgenx/data/csl/README.md`. Any other style is a path to a `.csl` file (the extension may
  be left out), relative to the document. The document's `lang` picks the CSL locale when citeproc-py has it (`en-GB`
  gives British quotation marks).
- **Rendering:** all citations are rendered together once the document has been read, so numbering by first citation
  (numeric styles) is right. Multi-key citations are sorted and joined by the style. wordgenx fixes four things
  citeproc-py gets wrong, for every style: year suffixes when the style asks for them ("Smith, 2020a", "2020b", in
  title order), the entry's own page range even when a citation had a locator, a period or comma moved inside a
  closing quote when the locale says so (American English), and `initialize="false"` (full given names in Chicago).
  Not done yet: collapsing ("Smith, 2020a, 2020b" stays "Smith, 2020a; Smith, 2020b"; "[4]–[6]" stays "[4], [5], [6]")
  and name-based disambiguation (adding given names or more authors).
- **`\cite` options:** a locator (`page`, `chapter`, `section`, `figure`, `table`, `paragraph`, `line`, `volume`, `note`,
  `equation`, …), `prefix`, `suffix`. `\textcite` puts the names in the text: "Tibshirani (1996)" for author-date
  styles, "Tibshirani [2]" for numeric ones. One author gives the family name, two give "A and B", three or more give
  "A et al.".
- **`\printbibliography`:** the cited entries (`all`: every entry in the data) under an unnumbered heading
  (`title="References"`, `title=none`, `level=1..9`). Each entry is bookmarked, and citations link to the entry of their
  first key. URLs and DOIs in entries are links. A numeric style's label (`[3]`) is followed by a tab to the hanging
  indent.
- Unknown keys are errors with the nearest key as a suggestion (`WGX0312`).

## 3.20 Escape hatches (in order of preference)

1. `\style{Name}{…}`: use any style that exists in the template.
2. Option or style-sheet key `ooxml.rPr` / `ooxml.pPr` / `ooxml.tblPr` / `ooxml.tcPr`: raw property XML for the
   generated element (`\p[ooxml.pPr="<w:snapToGrid w:val='0'/>"]{…}`). Each element replaces the one wordgenx would
   write under the same name; the elements must be ones that container holds.
3. `\raw[ooxml]{<w:r>…</w:r>}`: raw run- or block-level WordprocessingML. The body is raw. A body of `w:p`, `w:tbl`
   (or a `w:sdt` holding them) is a block of its own; anything else goes inline. Run-level and block-level elements
   cannot be mixed.
4. `\embed-docx{part.docx}`: insert another document as `altChunk`. Word merges it on open (M3).

Raw XML is checked when the document compiles: it must be well-formed WordprocessingML (`w:` elements; the usual
prefixes `w:`, `r:`, `a:`, `wp:`, `pic:`, `w14:`, `mc:`, `v:`, `o:`, `m:` need no declaration). A bad escape hatch is a
compile error, not a corrupt file. Relationship ids (`r:id`) are not rewritten, so raw XML cannot refer to images or
links of its own yet.

## 3.21 Diagnostics and lints

Every error has a code (`WGX1203`), a span, a message and, where possible, a suggestion:

```
error[WGX1203]: unknown paragraph property `space-befor`
  --> chapters/02-design.wgx:14:22
   |
14 | \p[style=Body, space-befor=12pt]{…}
   |                ^^^^^^^^^^^ did you mean `space-before`?
```

Planned lints: a repeated label word before `@label` (§3.11); an unused label; for contracts, a defined term that is
never used, or a capitalised term used but never defined.

Syntax errors use the codes `WGX01xx` (`wordgenx.syntax.diagnostics.Code`). Later stages use other ranges.

## 3.22 Parsing rules (settled in M1)

The parser (`wordgenx.syntax`) fixes these details that the sections above leave open:

- **Command names** are letters and digits with inner hyphens (`\embed-docx`) and are read greedily: `\product-based`
  is the command `product-based`. Write `\product{}-based` instead.
- **Options and body must touch the name.** `\b{x}` is a command with a body. `\b {x}` is the command `\b`, a space,
  then a stray `{` (an error). `[` must directly follow the name, and `{` must directly follow the name or `]`.
- **Bare `{` in text is an error.** Escape it as `\{`. `\#{` is a literal `#{`.
- **`\define` and `\let`** are the only forms with two brace groups: `\define{name}[params]{body}`.
- **Labels** match `[A-Za-z][A-Za-z0-9_-]*(:[A-Za-z0-9_-]+)*`, e.g. `fig:arch`, `cl:term`. Trailing sentence punctuation is
  not part of a reference: `see @fig:arch.` refers to `fig:arch`.
- **Autolinks** `<scheme:…>` map to `\url{…}` (string body). `\link[to="…"]{text}` has a markup body.
- **Block starts** are recognised at the start of a line (after indentation): `=` headings followed by a space, `- ` and
  `1. ` list markers, ` ``` ` fences, `$$`, and `|` table rows. These also end a running paragraph. `$$` is special only at
  a block start.
- **Letter list markers** (`a. `) are recognised only inside a list item, so a sentence starting with "A. Smith wrote" is never a
  list.
- **List continuation** lines must be indented at least to the item's text column; there is no Markdown-style "lazy"
  continuation. A line indented less ends the item.
- **Code spans** close on the same line. Use N backticks to include fewer than N, as in ``` ``a ` b`` ```.
- **Unclosed `*` / `_`** are a warning and are kept as literal text.
- **Pipe-table cells** are separated by `|` outside code spans, math and command bodies. A final whitespace-only cell after the
  last `|` is ignored.
- **Whitespace** is kept as written in the AST. Collapsing runs of spaces, line breaks into spaces, and smart punctuation
  (`--`, `---`, quotes) happen at lowering.
- **Limits:** nesting deeper than 64 levels is an error (`WGX0116`). To avoid exponential backtracking, the parser bounds
  the work it spends on unclosed `*`, `_` and `[` openers. Ordinary documents never reach the bound; adversarial input
  degrades to literal text.

## 3.23 Lowering rules (settled in M1)

Lowering (`wordgenx.lower`) turns the AST into the document IR. It fixes these behaviours:

- **Block vs inline commands.** Block commands (`\p`, `\align`, `\list`, `\include`, `\section`, the preamble commands,
  and later `\table`, `\figure`, …) must stand alone. A paragraph made only of block commands (and blank lines or
  comments between them) is a sequence of blocks. Mixing a block command with text in one paragraph is an error
  (`WGX0303`); separate them with a blank line. `\pagebreak` and `\columnbreak` work inline, and alone they form their
  own paragraph.
- **Text.** Runs of spaces and line breaks collapse to one space. Spaces at paragraph edges and around `\tab` / `\br` /
  breaks are dropped. Smart punctuation (`--` → –, `---` → —, curly quotes) applies to text, not to code or URLs, and
  `\document[smart-quotes=off]` turns it off.
- **Character styles.** `*…*` → `Strong`, `_…_` → `Emphasis`, `` `…` `` → `Code`, links → `Hyperlink`. A Word run has one
  character style, so for nested styles the innermost wins and the outer style's formatting is added as direct
  formatting.
- **Built-in styles on demand.** Styles the markup relies on (`Heading 1`–`9`, `Title`, `Subtitle`, `List Paragraph`,
  `Strong`, `Emphasis`, `Hyperlink`, `Code`, `Code Block`) are created with neutral formatting only when used and not
  defined by a style sheet. Any other undefined style is an error with a suggestion (`WGX0307`).
- **Style sheets apply document-wide.** `\styles` and `\use{*.wgxs}` may appear anywhere, including in included files.
  Style references are checked after the whole document is read.
- **Lists.** `-` lists use the `Bullets` numbering and `1.` lists use `Numbers`. Both are created unless a style sheet
  defines them. Each separate list counts from its start (a new Word list instance). A nested list of the same kind is
  the next level of the same list. A nested list of the other kind starts a new list one level deeper. Further paragraphs
  of an item are unnumbered and indented to the item's text. List paragraphs get the level's linked style, or
  `List Paragraph`. `\list[Name]{…}` uses a named numbering for everything inside, `\list[Name, continue]{…}` continues
  the previous list of that numbering, and `\list[Name, restart=5]{…}` starts at 5.
- **Headings.** `=`…`=========` use `Heading 1`–`9`. `=*` adds an explicit "no numbering" so a style-numbered heading
  stays unnumbered.
- **Macros** expand at the AST level when called. A body that is a single paragraph splices into the surrounding text.
  Anything else must stand alone as blocks. Built-in command names cannot be redefined, and expansion deeper than 32
  levels is an error.
- **Tables.** A pipe table on its own is a table with the defaults. `\table[...]{...}` takes pipe rows and/or
  `\row{\cell{...} ...}`, and a pipe row may start right after the `{` (`\table{| a | b |}`). Defaults: Word's
  `Table Grid` style (`style=none` for no borders, `style="Data Grid"` for a style-sheet table style),
  equal column widths filling the text width, fixed layout. `widths=(4cm, 25%, 1fr, auto)` mixes lengths, percentages
  of the text width, and fractions of what remains. A cell with `colspan=N` in a pipe row covers the next N-1 cells,
  which must be empty (`| \cell[colspan=3]{x} |||`). `rowspan` cells take the position in the rows below. Short rows
  are padded with empty cells. `header-rows=N` or `\row[header]` repeat rows on each page. `columns=(left, right, …)`
  sets the default paragraph alignment per column. `decimal` lines numbers up on a decimal tab stop near the right of
  the cell (or `(decimal, tab=1.8cm)` from its left); header rows and merged cells are right-aligned instead.
  Tables also take `borders`, `border-*`, `cell-margins`, `table-indent` (overriding the style) and
  `look=(header-row, last-row, first-column, last-column, banded-rows, banded-columns)` or `look=none`.
  Cells take `colspan`, `rowspan`, `shade` (RGB or theme colour), `valign`, `align`, `borders`/`border-*`, and any
  block content, including lists and nested tables. Adjacent tables get a paragraph between them, as Word requires.
- **Images.** `\image[width=…, height=…, alt="…"]{file}` embeds PNG, JPEG, GIF or BMP as an inline picture. The size
  comes from the pixel size and resolution (96 dpi if unknown). Giving one dimension keeps the aspect ratio, and
  `width=50%` is relative to the text width. Pictures wider than the text are shrunk to fit. Identical files are
  stored once.
- **Sections.** `\section[...]` before any content sets up the first section. After content it ends the current
  section, and the new one starts with the same page setup. Headers and footers continue (Word's "link to previous")
  unless redefined or `link-headers=false`. Page numbering continues unless restarted. `title-page` applies only to the
  section that asks for it. Options: `size`, `orientation`, `margins`, `gutter`, `header-distance`,
  `footer-distance`, `break=next-page|continuous|even-page|odd-page|next-column`,
  `page-numbers=lower-roman` or `(format=…, start=1)`, `columns=2` or `(count=2, gap=1cm, separator)`, `title-page`,
  `link-headers=false`. `\section` cannot appear inside tables, headers or footers.
- **Headers and footers.** `\header[default|first|even]{…}` and `\footer[…]{…}` belong to the current section. Their
  paragraphs use Word's `Header`/`Footer` styles, which come with centre and right tab stops matching the text width,
  so `Left \tab Centre \tab Right` lines up. A `first` part implies a different first page. An `even` part turns on
  odd/even headers for the document.
- **Fields.** `\pagenum`, `\numpages` and `\sectionpages` are PAGE, NUMPAGES and SECTIONPAGES. `\field[result=…]{CODE}`
  is any Word field, with nested fields in `{ }` as in Word's field-code view. Results known in advance (document
  properties such as TITLE) are filled in; the others are marked for Word to update when the document opens.
  `\date[kind=fixed|today|saved|created, format="d MMMM yyyy"]` takes Word date pictures. `fixed` is plain text at
  build time and honours `SOURCE_DATE_EPOCH`.
- **Tab stops.** `\tabs[(8cm, center), (16cm, right, leader=dot)]{…}`, or the `tabs` property in style sheets and
  `\p`: `tabs: 8cm center, 16cm right leader=dot;`.
- **Text options** (`title`, `alt`, `style`, …) take a word, a `"string"` (which ends on its line), or plain text in
  braces, which may span lines: `alt={a long description …}`. In option values, `#` plus six hex digits is always a
  colour, even inside `\define`.
- **Commands not implemented yet** (figures, fields, notes, references, math, …) produce a warning
  (`WGX0304`) naming the milestone, and the document still builds. For `\comment`, `\ins` and `\control` the body text is
  kept; for the others the command is dropped.
