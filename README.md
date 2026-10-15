# WEX

> Working name. A typed document markup language that compiles to **native, fully formatted Word (.docx) documents**.

WEX is to Word what TeX is to typesetting: a plain-text source language you can version, diff, review,
generate and include, with a compiler that produces the final document. Unlike Markdown-to-DOCX
converters, WEX aims to expose *Word's own document model*: styles, multilevel numbering, sections, headers and footers,
fields, cross-references, footnotes, content controls, tracked changes, and the rest. The output should be a
document a Word user can keep editing as if a careful human had built it.

**Status:** design stage. No code yet.

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
| 8 | [Open questions](docs/08-open-questions.md) | Decisions still to be made |

## License

Open source. Proposed: Apache-2.0 (see [open questions](docs/08-open-questions.md)).
