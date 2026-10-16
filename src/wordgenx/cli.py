"""Command-line interface: ``wordgenx build``, ``check``, ``parse`` and ``inspect``."""

from __future__ import annotations

import argparse
import sys
import zipfile
from collections.abc import Sequence
from pathlib import Path

from wordgenx import __version__
from wordgenx.compiler import InternalError, build
from wordgenx.ooxml.reader import read_docx
from wordgenx.ooxml.template import Template, TemplateError, load_template
from wordgenx.stylesheet import compile_stylesheets, parse_stylesheet
from wordgenx.stylesheet.collect import STYLESHEET_SUFFIX
from wordgenx.syntax import Source, ast, parse
from wordgenx.syntax.cst import dump as dump_cst
from wordgenx.syntax.diagnostics import Diagnostic, render


def _load(path: str) -> Source | None:
    try:
        return Source.from_path(path)
    except OSError as e:
        print(f"error: cannot read {path}: {e.strerror}", file=sys.stderr)
        return None
    except UnicodeDecodeError:
        print(f"error: {path} is not valid UTF-8", file=sys.stderr)
        return None


def _diagnostics(source: Source) -> list[tuple[Source, Diagnostic]]:
    """The diagnostics of a document (.wgx: the whole build, without writing it) or of a
    style sheet (.wgxs)."""
    if source.name.endswith(STYLESHEET_SUFFIX):
        return list(compile_stylesheets([parse_stylesheet(source)]).diagnostics)
    return list(build(source)[1].diagnostics)


INTERNAL_HELP = "this is a bug in wordgenx; please report it with the document"


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


def _build(path: str, output: str | None, template: str | None) -> int:
    source = _load(path)
    if source is None:
        return 1
    as_template = output is not None and output.lower().endswith(".dotx")
    loaded = _load_template(template) if template else None
    if template and loaded is None:
        return 1
    try:
        data, result = build(source, template=loaded, as_template=as_template)
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
    target = Path(output) if output else Path(path).with_suffix(".docx")
    try:
        target.write_bytes(data)
    except OSError as e:
        print(f"error: cannot write {target}: {e.strerror}", file=sys.stderr)
        return 1
    print(f"wrote {target} ({summary})", file=sys.stderr)
    return 0


def _check(paths: Sequence[str]) -> int:
    errors = warnings = 0
    failed = False
    for path in paths:
        source = _load(path)
        if source is None:
            failed = True
            continue
        try:
            diagnostics = _diagnostics(source)
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


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="wordgenx", description=__doc__)
    parser.add_argument("--version", action="version", version=f"wordgenx {__version__}")
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

    check = commands.add_parser("check", help="report diagnostics without writing output")
    check.add_argument("files", nargs="+", metavar="FILE")

    dump = commands.add_parser("parse", help="print the syntax tree of a file")
    dump.add_argument("file", metavar="FILE")
    dump.add_argument("--tree", choices=("ast", "cst"), default="ast")

    inspect = commands.add_parser(
        "inspect", help="read a .docx and report its structure and coverage"
    )
    inspect.add_argument("file", metavar="FILE.docx")

    args = parser.parse_args(argv)
    if args.command == "build":
        return _build(args.file, args.output, args.template)
    if args.command == "check":
        return _check(args.files)
    if args.command == "inspect":
        return _inspect(args.file)
    return _parse(args.file, args.tree)


if __name__ == "__main__":
    sys.exit(main())
