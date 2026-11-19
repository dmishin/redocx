"""Command-line interface: ``build``, ``check``, ``fmt``, ``parse``, ``inspect``, ``import``
and ``explain``."""

from __future__ import annotations

import argparse
import codecs
import difflib
import sys
import zipfile
from collections.abc import Sequence
from pathlib import Path

from redocx import __version__, ir
from redocx.compiler import InternalError, build
from redocx.ir_json import to_json
from redocx.lower.preamble import std_modules
from redocx.ooxml.reader import read_docx
from redocx.ooxml.template import Template, TemplateError, load_template
from redocx.stylesheet import compile_stylesheets, parse_stylesheet
from redocx.stylesheet.collect import STYLESHEET_SUFFIX
from redocx.syntax import Source, ast, parse
from redocx.syntax.cst import dump as dump_cst
from redocx.syntax.diagnostics import Diagnostic, render, render_all
from redocx.syntax.format import FormatError, format_source


def _load(path: str) -> Source | None:
    try:
        return Source.from_path(path)
    except OSError as e:
        print(f"error: cannot read {path}: {e.strerror}", file=sys.stderr)
        return None
    except UnicodeDecodeError:
        print(f"error: {path} is not valid UTF-8", file=sys.stderr)
        return None


def _std_options(parser: argparse.ArgumentParser) -> None:
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--no-std",
        action="store_true",
        help="do not load the standard library (std:callout, std:latex, ...); "
        "\\use{std:name} still loads a module",
    )
    group.add_argument(
        "--no-latex",
        action="store_true",
        help="do not load std:latex: LaTeX commands such as \\textbf are errors with hints",
    )


def _std(no_std: bool, no_latex: bool) -> frozenset[str] | None:
    """The standard library modules to load before the document (None: all of them)."""
    if no_std:
        return frozenset()
    if no_latex:
        return frozenset(std_modules()) - {"latex"}
    return None


def _diagnostics(
    source: Source, std: frozenset[str] | None = None
) -> list[tuple[Source, Diagnostic]]:
    """The diagnostics of a document (.rdx: the whole build, without writing it) or of a
    style sheet (.rdxs)."""
    if source.name.endswith(STYLESHEET_SUFFIX):
        return list(compile_stylesheets([parse_stylesheet(source)]).diagnostics)
    return list(build(source, std=std)[1].diagnostics)


INTERNAL_HELP = "this is a bug in redocx; please report it with the document"


def _load_template(path: str) -> Template | None:
    """The --template file; problems are reported here, as there is no source line for them."""
    try:
        template = load_template(Path(path))
    except TemplateError as e:
        print(f"error: --template: {e}", file=sys.stderr)
        return None
    if template.body_issue is not None:
        print(
            f"warning: --template: the body content of `{template.name}` is not carried over: "
            f"{template.body_issue}; only its styles, theme, page setup, headers and footers "
            "are used",
            file=sys.stderr,
        )
    return template


def _emit_ir(document: ir.Document, target: str) -> bool:
    """Write the IR as JSON to `target` ("-": standard output)."""
    text = to_json(document)
    if target == "-":
        sys.stdout.write(text)
        return True
    try:
        Path(target).write_text(text, encoding="utf-8", newline="\n")
    except OSError as e:
        print(f"error: cannot write {target}: {e.strerror}", file=sys.stderr)
        return False
    print(f"wrote {target}", file=sys.stderr)
    return True


def _build(
    path: str,
    output: str | None,
    template: str | None,
    emit_ir: str | None,
    validate: bool,
    std: frozenset[str] | None = None,
) -> int:
    source = _load(path)
    if source is None:
        return 1
    as_template = output is not None and output.lower().endswith(".dotx")
    loaded = _load_template(template) if template else None
    if template and loaded is None:
        return 1
    try:
        data, result = build(source, template=loaded, as_template=as_template, std=std)
    except InternalError as e:
        print(f"error: {path}: {e}\n  = help: {INTERNAL_HELP}", file=sys.stderr)
        return 1
    if result.diagnostics:
        print(result.render_diagnostics(), file=sys.stderr)
    summary = f"{result.error_count} error{'s' * (result.error_count != 1)}, " + (
        f"{result.warning_count} warning{'s' * (result.warning_count != 1)}"
    )
    if data is None:
        print(f"build failed: {summary}", file=sys.stderr)
        return 1
    if validate and not _schema_valid(data):
        return 1
    document = result.document
    if emit_ir is not None and document is not None and not _emit_ir(document, emit_ir):
        return 1
    target = Path(output) if output else Path(path).with_suffix(".docx")
    try:
        target.write_bytes(data)
    except OSError as e:
        print(f"error: cannot write {target}: {e.strerror}", file=sys.stderr)
        return 1
    print(f"wrote {target} ({summary})", file=sys.stderr)
    return 0


def _schema_valid(data: bytes) -> bool:
    """--validate: every part of the written document against the ECMA-376 schemas."""
    from redocx.ooxml.validate import validate_package  # loads the schemas

    errors = validate_package(data)
    for part, messages in errors.items():
        for message in messages:
            print(f"error: {part}:{message}", file=sys.stderr)
    if errors:
        count = sum(len(m) for m in errors.values())
        print(
            f"build failed: the written document does not match the schemas ({count} "
            f"error{'s' * (count != 1)}; nothing was written)\n  = help: {INTERNAL_HELP}",
            file=sys.stderr,
        )
    return not errors


def _check(paths: Sequence[str], std: frozenset[str] | None = None) -> int:
    errors = warnings = 0
    failed = False
    for path in paths:
        source = _load(path)
        if source is None:
            failed = True
            continue
        try:
            diagnostics = _diagnostics(source, std)
        except InternalError as e:
            print(f"error: {path}: {e}\n  = help: {INTERNAL_HELP}", file=sys.stderr)
            failed = True
            continue
        if diagnostics:
            print("\n".join(render(d, src) for src, d in diagnostics), file=sys.stderr)
        errors += sum(d.is_error for _, d in diagnostics)
        warnings += sum(not d.is_error for _, d in diagnostics)
    summary = f"{errors} error{'s' * (errors != 1)}, {warnings} warning{'s' * (warnings != 1)}"
    print(f"checked {len(paths)} file{'s' * (len(paths) != 1)}: {summary}", file=sys.stderr)
    return 1 if errors or failed else 0


def _fmt(paths: Sequence[str], check: bool, diff: bool) -> int:
    """Rewrite files in the canonical layout; with `check` or `diff`, only report."""
    changed = unchanged = 0
    failed = False
    for path in paths:
        if path.endswith(STYLESHEET_SUFFIX):
            print(f"{path}: skipped: style sheets are not formatted yet", file=sys.stderr)
            continue
        source = _load(path)
        if source is None:
            failed = True
            continue
        try:
            result = format_source(source)
        except FormatError as e:
            print(f"error: {e}\n  = help: {INTERNAL_HELP}", file=sys.stderr)
            failed = True
            continue
        if result.text is None:
            if result.diagnostics:
                print(render_all(result.diagnostics, source), file=sys.stderr)
            print(f"{path}: {result.problem}", file=sys.stderr)
            failed = True
            continue
        if not result.changed(source.text):
            unchanged += 1
            continue
        changed += 1
        if diff:
            lines = difflib.unified_diff(
                source.text.splitlines(keepends=True),
                result.text.splitlines(keepends=True),
                f"{path} (original)",
                f"{path} (formatted)",
            )
            sys.stdout.writelines(lines)
        if check or diff:
            print(f"would reformat {path}", file=sys.stderr)
            continue
        try:
            raw = Path(path).read_bytes()
            bom = codecs.BOM_UTF8 if raw.startswith(codecs.BOM_UTF8) else b""
            Path(path).write_bytes(bom + result.text.encode("utf-8"))
        except OSError as e:
            print(f"error: cannot write {path}: {e.strerror}", file=sys.stderr)
            failed = True
            continue
        print(f"reformatted {path}", file=sys.stderr)
    verb = "would be reformatted" if check or diff else "reformatted"
    print(
        f"{changed} file{'s' * (changed != 1)} {verb}, {unchanged} unchanged",
        file=sys.stderr,
    )
    return 1 if failed or ((check or diff) and changed) else 0


def _parse(path: str, tree: str) -> int:
    source = _load(path)
    if source is None:
        return 1
    result = parse(source)
    if tree == "cst":
        print(dump_cst(result.cst))
    else:
        for block in result.ast.blocks:
            print(ast.dump(block))
    if result.diagnostics:
        print(result.render_diagnostics(), file=sys.stderr)
    return 0 if result.ok else 1


def _inspect(path: str) -> int:
    try:
        result = read_docx(path)
    except OSError as e:
        print(f"error: cannot read {path}: {e.strerror}", file=sys.stderr)
        return 1
    except (ValueError, zipfile.BadZipFile) as e:  # NotADocxError, or content the IR rejects
        print(f"error: {path}: {e}", file=sys.stderr)
        return 1
    print(f"{path}\n{result.report.render()}")
    return 0


def _import(path: str, output: str | None, keep_styles: bool, sugar: bool, check: bool) -> int:
    from redocx.importer import import_docx  # the importer compiles; load it only here

    folder = Path(output) if output else Path(path).with_suffix("")
    try:
        imported = import_docx(path, folder, keep_styles=keep_styles, sugar=sugar)
    except OSError as e:
        print(f"error: cannot read {path}: {e.strerror}", file=sys.stderr)
        return 1
    except (ValueError, zipfile.BadZipFile) as e:
        print(f"error: {path}: {e}", file=sys.stderr)
        return 1
    print(imported.report(), end="")
    print(f"wrote {imported.source} and its style sheet, pictures and report", file=sys.stderr)
    if imported.errors:
        return 1
    # --check: the import must compile to what the original was
    return 1 if check and not (imported.comparison and imported.comparison.same) else 0


def _explain(name: str | None, document: str | None, properties: str | None) -> int:
    from redocx import explain
    from redocx.props.registry import Target

    if properties is not None:
        print(explain.explain_properties(Target[properties.upper()]), end="")
        return 0
    if name is None:
        print(explain.overview(), end="")
        return 0
    path = Path(document) if document else None
    if path is not None and not path.is_file():
        print(f"error: cannot read {document}", file=sys.stderr)
        return 1
    text, found = explain.explain(name, path)
    print(text, end="", file=sys.stdout if found else sys.stderr)
    return 0 if found else 1


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="redocx", description=__doc__)
    parser.add_argument("--version", action="version", version=f"redocx {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)

    build_cmd = commands.add_parser("build", help="compile a document to .docx")
    build_cmd.add_argument("file", metavar="FILE")
    build_cmd.add_argument(
        "-o", "--output", metavar="OUT", help="output file (default: FILE.docx; .dotx: a template)"
    )
    build_cmd.add_argument(
        "-t",
        "--template",
        metavar="TEMPLATE",
        help="a .dotx or .docx to compile into (overrides \\document[template=...])",
    )
    build_cmd.add_argument(
        "--emit-ir",
        metavar="JSON",
        help="also write the document's IR as JSON to this file (-: standard output)",
    )

    build_cmd.add_argument(
        "--validate",
        action="store_true",
        help="also check the written document against the ECMA-376 schemas (slower)",
    )
    _std_options(build_cmd)

    check = commands.add_parser("check", help="report diagnostics without writing output")
    check.add_argument("files", nargs="+", metavar="FILE")
    _std_options(check)

    fmt = commands.add_parser("fmt", help="rewrite files in the canonical layout")
    fmt.add_argument("files", nargs="+", metavar="FILE")
    fmt.add_argument(
        "--check", action="store_true", help="change nothing; fail if a file would change"
    )
    fmt.add_argument(
        "--diff", action="store_true", help="change nothing; print the changes as a diff"
    )

    dump = commands.add_parser("parse", help="print the syntax tree of a file")
    dump.add_argument("file", metavar="FILE")
    dump.add_argument("--tree", choices=("ast", "cst"), default="ast")

    inspect = commands.add_parser(
        "inspect", help="read a .docx and report its structure and coverage"
    )
    inspect.add_argument("file", metavar="FILE.docx")

    importer = commands.add_parser(
        "import",
        help="turn a .docx into redocx source: a folder with the source, styles and pictures",
    )
    importer.add_argument("file", metavar="FILE.docx")
    importer.add_argument(
        "-o", "--output", metavar="DIR", help="the folder (default: FILE without .docx)"
    )
    importer.add_argument(
        "--check",
        action="store_true",
        help="fail unless the imported source compiles to what the original was",
    )
    importer.add_argument(
        "--commands-only",
        action="store_true",
        help="print commands only (\\p[style=...] rather than = headings, ...)",
    )
    importer.add_argument(
        "--keep-styles",
        action="store_true",
        help="keep the original's styles, theme and lists exactly: compile into it as a template",
    )

    explainer = commands.add_parser(
        "explain", help="explain a command, formatting property, macro or std: module"
    )
    explainer.add_argument(
        "name", nargs="?", metavar="NAME", help="table, space-before, note, std:callout, ..."
    )
    explainer.add_argument(
        "--in", dest="document", metavar="FILE.rdx", help="also the macros this document defines"
    )
    explainer.add_argument(
        "--properties",
        choices=("run", "paragraph", "table", "cell"),
        help="list the formatting properties of one kind",
    )

    args = parser.parse_args(argv)
    if args.command == "explain":
        return _explain(args.name, args.document, args.properties)
    if args.command == "import":
        return _import(args.file, args.output, args.keep_styles, not args.commands_only, args.check)
    if args.command == "build":
        std = _std(args.no_std, args.no_latex)
        return _build(args.file, args.output, args.template, args.emit_ir, args.validate, std)
    if args.command == "check":
        return _check(args.files, _std(args.no_std, args.no_latex))
    if args.command == "fmt":
        return _fmt(args.files, args.check, args.diff)
    if args.command == "inspect":
        return _inspect(args.file)
    return _parse(args.file, args.tree)


if __name__ == "__main__":
    sys.exit(main())
