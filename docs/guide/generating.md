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
| markup in the middle of a line: after `= `, `- ` or `1. `, a pipe-table cell | `escape.inline(s)` | `= {escape.inline(s)}` |
| a string body: `\image`, `\url`, `\include` | `escape.string(s)` | `\url{{escape.string(s)}}` |
| an option value | `escape.value(s)` | `alt={escape.value(s)}` (adds the quotes) |

`escape.text` also makes literal what would start a block at the start of a line: `- `, `1. `, `a. `, `=`, `|`,
`$$`. A line break in the data stays a line break in the source: one reads as a space, a blank line starts a new
paragraph. For a line break inside the paragraph, replace `\n` with `\br` after escaping, as the invoice example
does with the address.

Two things still apply to escaped text, as to any text:

- Runs of spaces collapse to one.
- Straight quotes and `--` become typographic quotes and dashes. `\document[smart-quotes=off]` keeps them as typed.

`escape.value` turns line breaks into spaces, since an option value is one line.

## With Jinja

Register the functions as filters and write the template as redocx source. Turn off Jinja's HTML autoescaping, which
would turn `&` into `&amp;`:

```python
import jinja2
from redocx import escape

env = jinja2.Environment(loader=jinja2.FileSystemLoader("templates"), autoescape=False)
env.filters.update(rdx=escape.text, rdx_inline=escape.inline, rdx_value=escape.value, rdx_string=escape.string)
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

Treat any diagnostic as a failure in generated documents: a warning there usually means the data held something
the template did not expect.

From a shell, write the source to a file and run `redocx build FILE.rdx -o OUT.docx`; the exit status is 1 when
the build fails.
