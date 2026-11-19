"""Command-line interface: ``build``, ``check``, ``fmt``, ``parse``, ``inspect``, ``import``
and ``explain``."""

from __future__ import annotations

import argparse
import codecs
import difflib
import json
import sys
import zipfile
from collections.abc import Sequence
from dataclasses import dataclass
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
from redocx.syntax.diagnostics import to_dict as diagnostic_dict
from redocx.syntax.format import FormatError, format_source

STDIN = "-"
STDIN_NAME = "<stdin>"


def _load(path: str, out: _Reporter | None = None) -> Source | None:
    """The source at `path` ("-": standard input); problems are reported."""
    out = out or _Reporter()
    try:
        if path == STDIN:
            return Source(STDIN_NAME, sys.stdin.buffer.read().decode("utf-8-sig"))
        return Source.from_path(path)
    except OSError as e:
        out.error(f"cannot read {path}: {e.strerror}")
        return None
    except UnicodeDecodeError:
        out.error(f"{STDIN_NAME if path == STDIN else path} is not valid UTF-8")
        return None


class _Reporter:
    """Where `build` and `check` report: text for people, or with `json` one JSON object
    per line, for programs (`--diagnostics json`)."""

    def __init__(self, json: bool = False) -> None:
        self.json = json

    def _emit(self, data: dict[str, object]) -> None:
        print(json.dumps(data, ensure_ascii=False), file=sys.stderr)

    def diagnostics(self, items: Sequence[tuple[Source, Diagnostic]]) -> None:
        if not items:
            return
        if self.json:
            for source, diagnostic in items:
                self._emit(diagnostic_dict(diagnostic, source))
        else:
            print("\n".join(render(d, source) for source, d in items), file=sys.stderr)

    def error(self, message: str, help: str | None = None) -> None:
        if self.json:
            self._emit({"type": "error", "message": message, "help": help})
        else:
            print(f"error: {message}" + (f"\n  = help: {help}" if help else ""), file=sys.stderr)

    def warning(self, message: str) -> None:
        if self.json:
            self._emit({"type": "warning", "message": message})
        else:
            print(f"warning: {message}", file=sys.stderr)

    def info(self, message: str) -> None:
        """A progress line for people; programs get the summary instead."""
        if not self.json:
            print(message, file=sys.stderr)

    def summary(self, text: str, errors: int, warnings: int, output: str | None = None) -> None:
        """The last line: `text` for people; the counts and the file written for programs."""
        if self.json:
            self._emit(
                {"type": "summary", "errors": errors, "warnings": warnings, "output": output}
            )
        else:
            print(text, file=sys.stderr)


def _counts(errors: int, warnings: int) -> str:
    return f"{errors} error{'s' * (errors != 1)}, {warnings} warning{'s' * (warnings != 1)}"


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


def _report_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--warnings-as-errors",
        action="store_true",
        help="fail on warnings too (a build then writes nothing)",
    )
    parser.add_argument(
        "--diagnostics",
        choices=("text", "json"),
        default="text",
        help="json: report on standard error as one JSON object per line, for programs",
    )
    parser.add_argument(
        "--root",
        metavar="DIR",
        help="every file the document names (\\include, \\image, ...) must be inside DIR",
    )


def _std(no_std: bool, no_latex: bool) -> frozenset[str] | None:
    """The standard library modules to load before the document (None: all of them)."""
    if no_std:
        return frozenset()
    if no_latex:
        return frozenset(std_modules()) - {"latex"}
    return None


def _base_dir(source: Source) -> Path | None:
    """Where relative file names are found: the current directory for standard input,
    otherwise the file's own (None)."""
    return Path.cwd() if source.name == STDIN_NAME else None


def _diagnostics(
    source: Source, std: frozenset[str] | None = None, root: str | None = None
) -> list[tuple[Source, Diagnostic]]:
    """The diagnostics of a document (.rdx: the whole build, without writing it) or of a
    style sheet (.rdxs)."""
    if source.name.endswith(STYLESHEET_SUFFIX):
        return list(compile_stylesheets([parse_stylesheet(source)]).diagnostics)
    return list(build(source, _base_dir(source), std=std, root=root)[1].diagnostics)


INTERNAL_HELP = "this is a bug in redocx; please report it with the document"


def _load_template(path: str, out: _Reporter) -> Template | None:
    """The --template file; problems are reported here, as there is no source line for them."""
    try:
        template = load_template(Path(path))
    except TemplateError as e:
        out.error(f"--template: {e}")
        return None
    if template.body_issue is not None:
        out.warning(
            f"--template: the body content of `{template.name}` is not carried over: "
            f"{template.body_issue}; only its styles, theme, page setup, headers and footers "
            "are used"
        )
    return template


def _emit_ir(document: ir.Document, target: str, out: _Reporter) -> bool:
    """Write the IR as JSON to `target` ("-": standard output)."""
    text = to_json(document)
    if target == "-":
        sys.stdout.write(text)
        return True
    try:
        Path(target).write_text(text, encoding="utf-8", newline="\n")
    except OSError as e:
        out.error(f"cannot write {target}: {e.strerror}")
        return False
    out.info(f"wrote {target}")
    return True


@dataclass(frozen=True)
class _BuildOptions:
    output: str | None = None
    template: str | None = None
    emit_ir: str | None = None
    validate: bool = False
    std: frozenset[str] | None = None
    strict: bool = False
    """--warnings-as-errors"""
    root: str | None = None


def _build(path: str, options: _BuildOptions, out: _Reporter) -> int:
    output = options.output
    if path == STDIN and output is None:
        out.error("reading from standard input needs `-o`", help="write `-o FILE.docx`, or `-o -`")
        return 1
    if output == "-" and options.emit_ir == "-":
        out.error("`-o -` and `--emit-ir -` both write to standard output", help="give one a file")
        return 1
    source = _load(path, out)
    if source is None:
        return 1
    as_template = output is not None and output.lower().endswith(".dotx")
    loaded = _load_template(options.template, out) if options.template else None
    if options.template and loaded is None:
        return 1
    try:
        data, result = build(
            source,
            _base_dir(source),
            loaded,
            as_template=as_template,
            std=options.std,
            root=options.root,
        )
    except InternalError as e:
        out.error(f"{source.name}: {e}", help=INTERNAL_HELP)
        return 1
    out.diagnostics(result.diagnostics)
    errors, warnings = result.error_count, result.warning_count
    summary = _counts(errors, warnings)
    if data is None or (options.strict and warnings):
        reason = " (warnings are errors)" if data is not None else ""
        out.summary(f"build failed: {summary}{reason}", errors, warnings)
        return 1
    if options.validate and not _schema_valid(data, out):
        return 1
    document, emit_ir = result.document, options.emit_ir
    if emit_ir is not None and document is not None and not _emit_ir(document, emit_ir, out):
        return 1
    if output == "-":
        sys.stdout.buffer.write(data)
        sys.stdout.buffer.flush()
        out.summary(f"wrote the document to standard output ({summary})", errors, warnings, "-")
        return 0
    target = Path(output) if output else Path(path).with_suffix(".docx")
    try:
        target.write_bytes(data)
    except OSError as e:
        out.error(f"cannot write {target}: {e.strerror}")
        return 1
    out.summary(f"wrote {target} ({summary})", errors, warnings, str(target))
    return 0


def _schema_valid(data: bytes, out: _Reporter) -> bool:
    """--validate: every part of the written document against the ECMA-376 schemas."""
    from redocx.ooxml.validate import validate_package  # loads the schemas

    errors = validate_package(data)
    for part, messages in errors.items():
        for message in messages:
            out.error(f"{part}:{message}")
    if errors:
        count = sum(len(m) for m in errors.values())
        out.error(
            f"build failed: the written document does not match the schemas ({count} "
            f"error{'s' * (count != 1)}; nothing was written)",
            help=INTERNAL_HELP,
        )
    return not errors


def _check(
    paths: Sequence[str],
    std: frozenset[str] | None,
    strict: bool,
    out: _Reporter,
    root: str | None = None,
) -> int:
    errors = warnings = 0
    failed = False
    for path in paths:
        source = _load(path, out)
        if source is None:
            failed = True
            continue
        try:
            diagnostics = _diagnostics(source, std, root)
        except InternalError as e:
            out.error(f"{source.name}: {e}", help=INTERNAL_HELP)
            failed = True
            continue
        out.diagnostics(diagnostics)
        errors += sum(d.is_error for _, d in diagnostics)
        warnings += sum(not d.is_error for _, d in diagnostics)
    text = f"checked {len(paths)} file{'s' * (len(paths) != 1)}: {_counts(errors, warnings)}"
    out.summary(text, errors, warnings)
    return 1 if errors or failed or (strict and warnings) else 0


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
    build_cmd.add_argument("file", metavar="FILE", help="the document (-: standard input)")
    build_cmd.add_argument(
        "-o",
        "--output",
        metavar="OUT",
        help="output file (default: FILE.docx; .dotx: a template; -: standard output)",
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
    _report_options(build_cmd)

    check = commands.add_parser("check", help="report diagnostics without writing output")
    check.add_argument("files", nargs="+", metavar="FILE", help="-: standard input")
    _std_options(check)
    _report_options(check)

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
        options = _BuildOptions(
            output=args.output,
            template=args.template,
            emit_ir=args.emit_ir,
            validate=args.validate,
            std=_std(args.no_std, args.no_latex),
            strict=args.warnings_as_errors,
            root=args.root,
        )
        return _build(args.file, options, _Reporter(json=args.diagnostics == "json"))
    if args.command == "check":
        std = _std(args.no_std, args.no_latex)
        out = _Reporter(json=args.diagnostics == "json")
        return _check(args.files, std, args.warnings_as_errors, out, args.root)
    if args.command == "fmt":
        return _fmt(args.files, args.check, args.diff)
    if args.command == "inspect":
        return _inspect(args.file)
    return _parse(args.file, args.tree)


if __name__ == "__main__":
    sys.exit(main())
