"""Command-line interface: ``wordgenx check`` and ``wordgenx parse``."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from wordgenx import __version__
from wordgenx.syntax import Source, ast, parse
from wordgenx.syntax.cst import dump as dump_cst


def _load(path: str) -> Source | None:
    try:
        return Source.from_path(path)
    except OSError as e:
        print(f"error: cannot read {path}: {e.strerror}", file=sys.stderr)
        return None
    except UnicodeDecodeError:
        print(f"error: {path} is not valid UTF-8", file=sys.stderr)
        return None


def _check(paths: Sequence[str]) -> int:
    errors = warnings = 0
    failed = False
    for path in paths:
        source = _load(path)
        if source is None:
            failed = True
            continue
        result = parse(source)
        if result.diagnostics:
            print(result.render_diagnostics(), file=sys.stderr)
        errors += sum(d.is_error for d in result.diagnostics)
        warnings += sum(not d.is_error for d in result.diagnostics)
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


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="wordgenx", description=__doc__)
    parser.add_argument("--version", action="version", version=f"wordgenx {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)

    check = commands.add_parser("check", help="parse files and report diagnostics")
    check.add_argument("files", nargs="+", metavar="FILE")

    dump = commands.add_parser("parse", help="print the syntax tree of a file")
    dump.add_argument("file", metavar="FILE")
    dump.add_argument("--tree", choices=("ast", "cst"), default="ast")

    args = parser.parse_args(argv)
    if args.command == "check":
        return _check(args.files)
    return _parse(args.file, args.tree)


if __name__ == "__main__":
    sys.exit(main())
