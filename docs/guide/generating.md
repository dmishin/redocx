# Generating documents

A program writes redocx source with any template engine or plain string formatting, then compiles it. Data goes
through `redocx.escape` on the way in, so a customer called `Smith & Sons {Holdings}` or an item called
`*large* | blue` prints as written instead of breaking the build or turning into markup.

```python
from pathlib import Path

from redocx import escape
from redocx.compiler import build

customer = "Smith & Sons {Holdings}"
items = [("Widgets *large* | blue", 4), ("Support @ 50% off", 1)]

rows = "\n".join(f"| {escape.inline(name)} | {qty} |" for name, qty in items)
source = f"""\
\\meta[title={escape.value("Invoice for " + customer)}]

= Invoice for {escape.inline(customer)}

\\table[header-rows=1]{{
| Item | Qty |
{rows}
}}
"""

data, result = build(source, base_dir=Path("."))
if data is None or result.diagnostics:
    raise SystemExit(result.render_diagnostics())
Path("invoice.docx").write_bytes(data)
```

A complete version is [`examples/generated/invoice.py`](../../examples/generated/invoice.py).

## What to escape with

Text goes into three kinds of places, each with its own rules (see [Syntax](syntax.md)):

| Where the data goes | Function | For example |
|---------------------|----------|-------------|
| markup that may start a line: a paragraph, the body of `\p`, `\b`, `\cell`, `\footnote` | `escape.text(s)` | `\p{{escape.text(s)}}` |
| the same, with each line break in the data a line break in the paragraph (an address) | `escape.lines(s)` | `\p{{escape.lines(s)}}` |
| text after a list marker | `escape.text(s, indent=N)` | `1. {escape.text(s, indent=3)}` |
| markup in the middle of a line: after `= `, a pipe-table cell | `escape.inline(s)` | `= {escape.inline(s)}` |
| a string body: `\image`, `\url`, `\include` | `escape.string(s)` | `\url{{escape.string(s)}}` |
| an option value | `escape.value(s)` | `alt={escape.value(s)}` (adds the quotes) |

Escaped text reads back as exactly the data:

- `escape.text` makes literal what would start a block at the start of a line: `- `, `1. `, `a. `, `=`, `|`,
  `$$`, and the Markdown marks redocx warns about (`#`, `>`, `+`). A line break in the data stays a line break in
  the source: one reads as a space, a blank line starts a new paragraph. `escape.lines` makes each one `\br`.
- After a list marker, pass the marker's width as `indent` (3 for `1. `, 2 for `- `): the later lines of the data
  are indented to the item's text, so they stay in the item instead of ending the list.
- `escape.inline` writes line breaks as spaces, so the data stays on its line (a heading, a table row).
- Spaces are kept: a run of them, and those at the start and end, are written `\ `.
- Straight quotes and hyphens are kept as typed (`\"`, `\'`, `\-`). Smart punctuation still applies to the text
  the template writes itself.

`escape.value` turns line breaks into spaces, since an option value is one line.

## Headings, lists and tables from data

The command forms of headings and list items need no indentation, so they are the easiest to write from a loop:

```python
def outline(sections: list[tuple[str, list[str]]]) -> str:
    parts: list[str] = []
    for title, points in sections:
        parts.append(f"\\heading[2]{{{escape.inline(title)}}}\n")
        items = "\n".join(f"  \\item{{{escape.text(point)}}}" for point in points)
        parts.append(f"\\list[Bullets]{{\n{items}\n}}\n")
    return "\n".join(parts)
```

`\heading[level, id=…, unnumbered]` takes paragraph formatting too (`page-break-before`), and an `\item` inside
another `\item` is one level deeper.

In a pipe table, a cell that spans columns is followed by the empty cells it covers, and a cell that spans rows
takes the place of the cells below it, which the rows below leave out:

```rdx
\table[header-rows=1, widths=(3cm, 1fr, 1fr)]{
| Region | Q1 | Q2 |
| \cell[rowspan=2]{North} | 10 | 12 |
| 11 | 13 |
| \cell[colspan=3]{No data for the South} |||
}
```

So a generator writes `colspan=N` and then N-1 empty cells (`|||` for three columns), and for `rowspan=N` writes
the next N-1 rows one cell shorter.

## With Jinja

Register the functions as filters and write the template as redocx source. Turn off Jinja's HTML autoescaping, which
would turn `&` into `&amp;`:

```python
import jinja2
from redocx import escape

env = jinja2.Environment(loader=jinja2.FileSystemLoader("templates"), autoescape=False)
env.filters.update(
    rdx=escape.text,
    rdx_lines=escape.lines,
    rdx_inline=escape.inline,
    rdx_value=escape.value,
    rdx_string=escape.string,
)
source = env.get_template("letter.rdx.j2").render(client=client, items=items)
```

```text
\meta[title={{ ("Letter to " ~ client.name) | rdx_value }}]

= {{ client.name | rdx_inline }}

{% for item in items %}
- {{ item.text | rdx_inline }}
{% endfor %}

\p[style=Signature]{ {{- client.contact | rdx -}} }
```

A redocx body opens with one `{` after a command name, so it does not clash with Jinja's `{{` and `{%`. Labels do:
Jinja reads `{#sec:intro}` as the start of a comment. Write the label as `{{ "{#" }}sec:intro}`, or set Jinja's
`comment_start_string` to something else.

## Compiling

`redocx.compiler.build(source, base_dir)` compiles a source string and returns the `.docx` bytes (or `None` when
there are errors) and the result with its diagnostics. Relative file names in the source (`\image`, `\include`,
`\use`) are found from `base_dir`; it defaults to the current directory. `build(..., template="corp.dotx")`
compiles into a template, as `redocx build -t` does.

Pictures and documents the program makes itself need not go through files on disk. `files` gives them by the
name the source uses, as bytes or as a function that returns the bytes (called once, when the document first
needs them):

```python
def chart() -> bytes:
    ...  # draw the chart and return its PNG bytes

source = "\\image[width=12cm]{charts/q3.png}\n"
data, result = build(source, files={"charts/q3.png": chart})
```

A name in `files` wins over a file on disk of the same name, and is inside `root` whatever its name. It works for
`\image` (and its SVG `fallback`), `\embed-docx` and `\watermark[image=…]`; `\include`, `\use` and
`\bibliography` read from disk.

Treat any diagnostic as a failure in generated documents: a warning there usually means the data held something
the template did not expect.

## Data from outside

Escaped data cannot add commands. Data used *as a file name* still names a file: `\image{{escape.string(path)}}`
reads whatever `path` is, `../../secrets.txt` included. When the data comes from users, give a root folder, and
every file the document names (`\include`, `\use`, `\image`, `\embed-docx`, `\bibliography`, `template=`) must be
inside it:

```python
data, result = build(source, base_dir=Path("work"), root=Path("work"))
```

```text
error[RDX0313]: `../../secrets.txt` is outside the root folder
 --> <input>:3:1
  |
3 | \image{../../secrets.txt}
  | ^^^^^^^^^^^^^^^^^^^^^^^^^
  |
  = help: files must be in work (--root)
```

On the command line the option is `--root DIR`, on `build` and `check`. Links through symbolic links are followed
before the check, so a link inside the root that points out of it is outside too.

## From other languages and scripts

`redocx build` reads the source from standard input with `-` and writes the document to standard output with
`-o -`, so any program can pipe through it without temporary files:

```bash
my-generator | redocx build - -o report.docx --warnings-as-errors
my-generator | redocx build - -o - --diagnostics json > report.docx
```

Relative file names in source read from standard input are found from the current directory.

- `--warnings-as-errors` fails the build on any warning, and nothing is written.
- `--diagnostics json` writes one JSON object per line to standard error instead of text, ending with a summary:

```text
{"type": "diagnostic", "severity": "warning", "code": "RDX0113", "message": "`*` opens strong text but is never closed", "file": "<stdin>", "line": 1, "column": 7, "end_line": 1, "end_column": 8, "label": "treated as literal text", "help": "escape it as `\\*` to silence this warning", "notes": []}
{"type": "summary", "errors": 0, "warnings": 1, "output": "-"}
```

Lines and columns count from 1; they are `null` for a problem of the whole file. Problems outside the source (a
file that cannot be read or written) are `{"type": "error", "message": …, "help": …}`. The exit status is 1 when
the build fails. `redocx check` takes the same options.
