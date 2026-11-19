# Contributing to ReDocx

## Setup

You need [uv](https://docs.astral.sh/uv/). It installs Python 3.12+ if needed. The Open XML SDK validator tests also
need the [.NET SDK](https://dotnet.microsoft.com/download) 8 or later; without it they are skipped.

```bash
uv sync
uv run pytest
```

## Before you open a pull request

CI runs these on Windows, Linux and macOS, and all must pass:

```bash
uv run ruff check
uv run ruff format --check
uv run pyright                 # strict mode, must be clean
uv run pytest
```

Some changes need generated files refreshed in the same commit:

| You changed | Run |
|-------------|-----|
| a command, option or formatting property | `python tools/make_reference.py` (updates `docs/reference.md`) |
| `docs/0*.md` | `python tools/md2rdx.py docs/0*.md --out docs/book/chapters` |
| the XML a document compiles to, on purpose | `REDOCX_UPDATE_GOLDEN=1 uv run pytest tests/golden`, then review the diff |

## What a change includes

- **A command or option:** its entry in `src/redocx/commands/builtin.py`, a handler in the matching
  `src/redocx/lower/` module, a test, the regenerated reference, and a guide section if users see it. If it needs new
  IR, the reader and the importer's printer must handle it too, so `redocx import` round-trips it or reports the
  loss.
- **A formatting property:** one entry in `src/redocx/props/registry.py`.
- **New syntax:** the parser must stay lossless and never crash (`tests/syntax/test_lossless.py`). A grammar change
  is a design decision: record it in `docs/08-open-questions.md` and follow the compatibility rule there (Q14).
- **A bug fix:** a test that fails without it. An unexpected golden diff is a bug until shown otherwise.

Output must open in Word without a repair prompt; passing the schemas is not enough. For anything that changes
layout, open the result in Word.

Every `rdx` example in `docs/guide/` and every file in `examples/` is compiled by the tests and must build without
warnings.

## Writing

Docs, messages and commit messages are plain and short. Say what something does, in the words a user sees
(`\table`, `space-before`, `RDX0401`). Diagnostics start lowercase, have no final period, and give the fix in
`help=`.

Commit subjects say what changed, without prefixes or a final period ("Warn on field types Word does not know").
The body explains what it does now and why, and lists what was regenerated. One topic per commit.

## Sign-off

Contributions are accepted under the [Developer Certificate of Origin](https://developercertificate.org/). Sign off
each commit with `git commit -s`.

## License

By contributing you agree that your work is licensed under the [Apache License 2.0](LICENSE).
