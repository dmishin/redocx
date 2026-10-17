# wordgenx

> A typed document markup language that compiles to **native, fully formatted Word (.docx) documents**.

wordgenx is to Word what TeX is to typesetting: a plain-text source language you can version, diff, review,
generate and include, with a compiler that produces the final document. Unlike Markdown-to-DOCX
converters, wordgenx aims to expose *Word's own document model*: styles, multilevel numbering, sections, headers and footers,
fields, cross-references, footnotes, content controls, tracked changes, and the rest. The output should be a
document a Word user can keep editing as if a careful human had built it.

**Status:** early development (milestone M1). `wordgenx build` compiles documents with paragraphs, headings, inline
formatting, hyperlinks, lists with real Word numbering, style sheets, macros and includes. Tables, images, fields,
notes and cross-references are next (see the [roadmap](docs/06-roadmap.md)).

## Design documents

| # | Document | What it covers |
|---|----------|----------------|
| 1 | [Vision & scope](docs/01-vision-and-scope.md) | Goals, non-goals, principles, prior art, what is and isn't possible |
| 2 | [ADR-0001: Implementation language](docs/02-adr-implementation-language.md) | Python vs .NET vs Java vs Rust vs JS, and the decision |
| 3 | [Language design](docs/03-language-design.md) | Syntax, commands, styles, lists, tables, math, macros, escape hatches |
| 4 | [Architecture](docs/04-architecture.md) | Compiler pipeline, intermediate representation, OOXML writer, templates |
| 5 | [Feature matrix](docs/05-feature-matrix.md) | Word features × implementation tier |
| 6 | [Roadmap](docs/06-roadmap.md) | Milestones M0–M6 and exit criteria |
| 7 | [Testing strategy](docs/07-testing-strategy.md) | How we know the output is correct |
| 8 | [Decisions & open questions](docs/08-open-questions.md) | Decision log and what is still open |

## Development

Requirements: [uv](https://docs.astral.sh/uv/) (it installs Python 3.12+ if needed) and, for the OOXML validator
tests, the [.NET SDK](https://dotnet.microsoft.com/download) 8 or later.

```bash
uv sync                    # create .venv with dev dependencies
uv run pytest              # tests (validator tests are skipped without the .NET SDK)
uv run ruff check          # lint
uv run ruff format         # format
uv run pyright             # strict type check
uv run wordgenx build examples/hello.wgx             # compile to examples/hello.docx
uv run wordgenx check examples/syntax-tour.wgx       # compile and report diagnostics, write nothing
uv run wordgenx fmt examples/hello.wgx               # rewrite in the canonical layout (--check, --diff)
uv run wordgenx build examples/hello.wgx --emit-ir - # also print the IR as JSON
uv run wordgenx parse examples/syntax-tour.wgx       # print the AST (--tree cst for the CST)
uv run wordgenx inspect examples/hello.docx          # read a .docx back, report structure and coverage
uv run wordgenx import examples/hello.docx -o hello  # turn a .docx into source: .wgx, .wgxs, pictures, report
uv run wordgenx import report.docx --keep-styles    # ... compiling into the original's styles as a template
uv run wordgenx import report.docx --commands-only  # ... with commands only, no sugar (level L1)
```

Golden snapshots (`tests/golden/expected/`) record the exact XML of each test document. After an intended output
change, regenerate them and review the diff before committing:

```bash
WORDGENX_UPDATE_GOLDEN=1 uv run pytest tests/golden
```

Every golden document is also validated against the ECMA-376 schemas (`schemas/`) and by Microsoft's Open XML SDK.

Layout: `src/wordgenx/` (package), `tests/`, `schemas/` (XSDs for tests), `tools/ooxml-validate/` (.NET validator,
CI and dev only), `docs/` (design), `examples/` (sample sources).

### Python API (early)

```python
from wordgenx import ir
from wordgenx.props import para_props, run_props
from wordgenx.ooxml.writer import write_docx

doc = ir.Document(
    styles=(
        ir.paragraph_style(
            "Heading 1",
            next="Normal",
            paragraph=para_props(keep_with_next=True, outline_level=1),
            run=run_props(font="theme.major", size="16pt", color="theme.accent1"),
        ),
    ),
    body=(
        ir.paragraph("Hello", style="Heading 1"),
        ir.paragraph("Plain text and ", ir.run("bold text", bold=True), ".", align="justify"),
    ),
)
write_docx(doc, "hello.docx")
```

## License

Open source. Apache-2.0.
