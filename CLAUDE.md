# ReDocx

Language + compiler: .rdx text -> native, fully formatted Word .docx. TeX-ish syntax, shaped after DOCX, not LaTeX.
Main goal: automated docx generation (any template engine emits .rdx, we compile). Human-writable too.
Also reverse: import .docx -> .rdx. Python 3.12+, uv, lxml. Package `redocx`, CLI `redocx`. Sources .rdx, style sheets .rdxs.
Old name wordgenx/.wgx; leftovers = bugs.

## Read first

- docs/01-vision-and-scope.md: goals, non-goals, principles. Start here.
- docs/03-language-design.md: syntax, commands, styles, macros. The spec.
- docs/04-architecture.md: pipeline, package layout.
- docs/07-testing-strategy.md: test layers, why.
- docs/08-open-questions.md: decision log. Check before redeciding anything.
- docs/09-postponed.md: what we deliberately don't do.
- docs/known-issues.md: known defects and rough edges not yet fixed. Remove an entry when it is fixed.
- docs/06-roadmap.md, 05-feature-matrix.md: status. docs/guide/: user guides. docs/reference.md: generated, don't edit.

## Layout

- src/redocx/cli.py: build, check, fmt, parse, inspect, import, explain.
- src/redocx/compiler.py: drives pipeline (compile_source, compile_file, build).
- syntax/: hand-written parser -> lossless CST (cst.py) -> AST (ast.py); diagnostics.py (RDXnnnn codes); format.py (fmt).
- commands/: command schema. builtin.py = every command (placement, typed options, body kind). types.py = value types. bind.py = checks args.
- lower/: AST -> IR. lowering.py = core + dispatch tables; one module per command family; macros; resolve passes (references, citations, index).
- props/registry.py: every formatting property (name, type, OOXML element). Single source of truth.
- stylesheet/: .rdxs and \styles{}.
- ir.py: IR dataclasses (Word-shaped). ir_json.py: --emit-ir.
- ooxml/: writer + parts/, template merge, reader (.docx -> IR), integrity (RDX0401), XSD validate.
- importer/: IR -> .rdx printer, labels, check.py (round-trip oracle).
- escape.py: data -> safe .rdx text (public; importer uses it). math/ (LaTeX subset -> OMML), highlight.py (Pygments), bibliography.py (BibTeX/CSL), std/ (std: macro library, .rdx), explain.py.
- data/: XSDs, CSL styles.
- tests/: per package. golden/ = snapshots. corpus/ = Word-made .docx.
- tools/: make_reference.py, md2rdx.py (docs/*.md -> docs/book), ooxml-validate/ (.NET validator), corpus/make-corpus.ps1 (Word COM).
- examples/: sample docs; all compiled + validated by tests. docs/book/: design docs rebuilt in redocx.

## Run

    uv sync
    uv run redocx build examples/hello.rdx [--validate] [--emit-ir -]
    uv run redocx check FILE.rdx        # diagnostics only
    uv run redocx parse FILE.rdx [--tree cst]
    uv run redocx import X.docx -o out [--check]
    uv run redocx explain NAME

## Test

    uv run pytest                       # all; .NET validator tests skip w/o dotnet
    uv run ruff check; uv run ruff format; uv run pyright   # pyright strict, must be clean
    REDOCX_UPDATE_GOLDEN=1 uv run pytest tests/golden       # after intended output change; review diff
    python tools/make_reference.py      # after command/property change (test_reference fails otherwise)
    python tools/md2rdx.py docs/0*.md --out docs/book/chapters   # after editing docs/0*.md (test_book)

CI = ruff, format --check, pyright, pytest on 3 OSes. All must pass before commit.

## Add/change a command

1. commands/builtin.py: declare with _command(name, placement, doc, _o(...) options, body=...). Option types from commands/types.py. Unimplemented = planned=.
2. Handler in matching lower/<family>.py, add to its BLOCKS or INLINES dict. Gets already bound, typed args. New module -> register dict in lower/lowering.py (BLOCK_HANDLERS / INLINE_HANDLERS).
3. Needs new IR? ir.py, then writer (ooxml/writer.py or parts/), reader (ooxml/reader.py), printer (importer/printer.py) so import round-trips. Else import loses it (printer counts self.lost).
4. explain.py CATEGORIES: list it (test_reference checks every command is listed).
5. Tests in tests/lower/ (or tests/commands/). Golden case if output XML matters. Regenerate reference.
6. Guide in docs/guide/ if user-visible. Guide rdx examples must compile without warnings (tested).

New formatting property = one PropSpec entry in props/registry.py. Usually no other code.

## Add/change syntax (sugar)

Sugar desugars to canonical commands; commands are truth.
1. syntax/cst.py: node kinds. syntax/parser.py: parse into CST. Must stay lossless and total (tests/syntax/test_lossless.py, Hypothesis): never crash, CST prints back to exact input.
2. syntax/ast.py: AST node + CST->AST.
3. lower/lowering.py: lower the AST node (usually same path as its command).
4. syntax/format.py: canonical layout for fmt.
5. importer/printer.py: emit the sugar on import, and escape text that now looks like markup (test_text_that_looks_like_markup_stays_text).
6. New error -> code in syntax/diagnostics.py.
7. Update docs/03-language-design.md + docs/guide/syntax.md. Grammar change = design decision -> docs/08.

## Wording

All prose (docs, docstrings, messages, commits): plain, short, concrete. Simple words. Say what it does, not how great it is.
No hype, no "powerful/seamless/robust", no filler, no emoji. No marketing in guides either.
Name things as user sees them: `\table`, `space-before`, RDX0401. Backticks for code, options, commands.

Diagnostics:
- Lowercase, no final period. Subject first: "`width` is given twice", "unknown option `foo` for `\table`", "`\image` needs a file name".
- Say what's wrong, then help= with the fix or usage. Planned feature: "not supported yet; it is ignored" + milestone.
- Every message has a span. Warn and go on where possible; error only when output would be wrong.

Docstrings/comments:
- First line: what it is or returns, as a noun phrase or plain sentence. "The header content with the watermark at the start..."
- Refer to spec as "design §3.2", "architecture §4.1".
- Comment why, not what. No commented-out code. No TODO without milestone.
- Field docs = docstring string right under the field (see commands/spec.py).

Docs (docs/, guides):
- Lead with a working example. Every rdx example must compile clean (tested).
- Present tense, describe what is, not history. History goes to commits and docs/08.
- Decisions: record in docs/08 with reason. Dropped features: docs/09.
- Edited docs/0*.md -> regenerate book chapters.

Commits:
- Subject: what changed, user-visible terms, no prefix tags, no trailing period. "Warn on field types Word does not know".
- Body: prose paragraphs. What it does now, why, what moved. Then what was regenerated (goldens, reference, book, lockfile). Roadmap item if relevant.
- One topic per commit. Commit only when asked.

## Practices

- Smallest change that fits existing pattern. Find the table/registry entry before writing new code.
- Fix root cause, not test. Golden diff unexpected -> bug until proven otherwise.
- New feature = spec entry + handler + test + guide + reference. Not done until import round-trips or loss is reported.
- Check output in Word for anything layout-related; XSD pass proves little.
- No new dependency without reason (docs/02 ADR spirit: few, pure Python + lxml).
- Prefer data (tables, dataclasses, frozen) over class hierarchies. IR is immutable; use dataclasses.replace.
- Examples in examples/ are real tests; keep them building warning-free.

## Rules

- Output must open in Word with no repair prompt. Valid XSD is not enough; Word is the judge.
- Output deterministic (byte-stable zip). No timestamps/random ids without fixed seed.
- Every built doc passes XSD + integrity check. Element order per ooxml/schema_order.py.
- One source of truth: command table, property registry. Docs/reference/explain generated from them. Don't duplicate.
- Diagnostics: code + span, helpful, no crashes on bad input.
- Ruff line length 100. Strict typing, no Any leaks.
- Word COM scripts (make-corpus etc.): always Close(0)/Quit(0) in finally. No save dialogs.
- User's private test .docx files (outside repo): never copy in, commit, or quote them or their import output.
- Don't commit examples/**/*.docx (gitignored) except examples/media/chapter.docx.
