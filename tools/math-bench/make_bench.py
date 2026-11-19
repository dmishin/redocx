"""Build the math benchmark: each formula of formulas.json as redocx renders it next to a
LaTeX reference picture.

    uv run python tools/math-bench/make_bench.py [formulas.json] [--out DIR] [--only ID,...]

Typesets with LaTeX once; --fresh does it again. Needs MiKTeX or TeX Live on PATH (latex,
dvisvgm, dvipng). Writes DIR/bench.rdx, DIR/bench.docx and DIR/ref/*.svg; DIR defaults to
tools/math-bench/out. A formula redocx accepts but writes as invalid OMML counts as rejected.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from redocx import escape
from redocx.compiler import build, compile_source
from redocx.ooxml.validate import validate_package
from redocx.syntax.diagnostics import Severity

HERE = Path(__file__).resolve().parent

PREAMBLE = r"""\documentclass[varwidth=24cm,border=1pt,11pt]{standalone}
\usepackage{amsmath,amssymb,amsfonts,mathtools,bm,mathrsfs,esint,cancel,xcolor}
\begin{document}
"""

ERROR_SHADE = "#FDE2E1"
WARN_SHADE = "#FFF2CC"
LATEX_SHADE = "#E8E8E8"


@dataclass
class Entry:
    n: int
    id: str
    category: str
    title: str
    latex: str
    errors: list[str] = field(default_factory=list[str])
    warnings: list[str] = field(default_factory=list[str])
    ref: str | None = None
    """The reference picture, relative to the output folder."""
    latex_error: str = ""


def run(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=60, check=False, stdin=subprocess.DEVNULL,
    )  # fmt: skip


def first_tex_error(log: str) -> str:
    lines = log.splitlines()
    for i, line in enumerate(lines):
        if line.startswith("!"):
            return " ".join(x.strip() for x in lines[i : i + 2]).strip()
    return "latex failed"


def reference(entry: Entry, refdir: Path) -> None:
    """Typeset the formula with LaTeX into ref/NNN.svg (and a PNG fallback)."""
    name = f"{entry.n:03d}"
    for ext in ("svg", "png"):
        if (refdir / f"{name}.{ext}").exists():
            entry.ref = f"ref/{name}.{ext}"
            return
    (refdir / f"{name}.tex").write_text(
        PREAMBLE + "\\[\n" + entry.latex + "\n\\]\n\\end{document}\n", encoding="utf-8"
    )
    p = run(
        ["latex", "--disable-installer", "-interaction=nonstopmode", "-halt-on-error",
         f"{name}.tex"],
        refdir,
    )  # fmt: skip
    dvi = refdir / f"{name}.dvi"
    if p.returncode != 0 or not dvi.exists():
        log = refdir / f"{name}.log"
        text = log.read_text(encoding="utf-8", errors="replace") if log.exists() else p.stdout
        entry.latex_error = first_tex_error(text)
        clean(refdir, name)
        return
    svg = run(["dvisvgm", "--no-fonts", "--exact-bbox", "-o", f"{name}.svg", f"{name}.dvi"], refdir)
    png = run(
        ["dvipng", "-D", "200", "-T", "tight", "-bg", "Transparent", "-o", f"{name}.png",
         f"{name}.dvi"],
        refdir,
    )  # fmt: skip
    if svg.returncode == 0 and (refdir / f"{name}.svg").exists():
        entry.ref = f"ref/{name}.svg"
    elif png.returncode == 0 and (refdir / f"{name}.png").exists():
        entry.ref = f"ref/{name}.png"
    else:
        entry.latex_error = "dvisvgm/dvipng failed"
    clean(refdir, name)


def clean(refdir: Path, name: str) -> None:
    for ext in ("aux", "log", "dvi", "tex"):
        (refdir / f"{name}.{ext}").unlink(missing_ok=True)


def math_source(latex: str) -> str:
    return "\\math{" + latex + "}"


def check(entry: Entry) -> None:
    """What redocx says about the formula alone in a document."""
    result = compile_source(math_source(entry.latex) + "\n")
    for _, d in result.diagnostics:
        text = f"{d.code.value}: {d.message}" + (f" ({d.help})" if d.help else "")
        (entry.errors if d.severity is Severity.ERROR else entry.warnings).append(text)
    if not result.ok and not entry.errors:
        entry.errors.append("compile failed without a diagnostic")
    if result.ok:
        docx, _ = build(math_source(entry.latex) + "\n")
        if docx is not None:
            for part, problems in validate_package(docx).items():
                entry.errors += [f"invalid OMML in {part}: {x}" for x in problems[:1]]


def code_lines(latex: str) -> str:
    lines = [ln for ln in latex.split("\n")] or [""]
    out: list[str] = []
    for ln in lines:
        shown = ln.replace("\t", "    ")
        out.append('\\font[font="Consolas", size=8pt]{' + escape.inline(shown or " ") + "}")
    return "\n\n".join(out)


def red(text: str) -> str:
    return "\\font[color=#C00000, size=8pt]{" + escape.inline(text) + "}"


def row(e: Entry) -> str:
    title = escape.inline(e.title)
    cat = escape.inline(e.category)
    small = "\\font[size=8pt]"
    left = (
        f"\\cell{{{e.n}}}\n  \\cell{{{small}{{*{cat}*}}\n\n{small}{{{title}}}\n\n"
        f"{small}{{`{e.id}`}}}}"
    )
    src = f"\\cell{{\n{code_lines(e.latex)}\n}}"
    if e.errors:
        rdx = f"\\cell[shade={ERROR_SHADE}]{{\n" + "\n\n".join(red(x) for x in e.errors) + "\n}"
    elif e.warnings:
        rdx = (
            f"\\cell[shade={WARN_SHADE}]{{\n{math_source(e.latex)}\n\n"
            + "\n\n".join(red(x) for x in e.warnings)
            + "\n}"
        )
    else:
        rdx = f"\\cell{{\n{math_source(e.latex)}\n}}"
    if e.ref:
        ref = f"\\cell[align=center]{{\\image[alt={escape.value(e.id)}]{{{e.ref}}}}}"
    else:
        ref = f"\\cell[shade={LATEX_SHADE}]{{\n{red('LaTeX: ' + e.latex_error)}\n}}"
    return f"\\row[cant-split]{{\n  {left}\n  {src}\n  {rdx}\n  {ref}\n}}"


def summary(entries: list[Entry]) -> str:
    n = len(entries)
    err = [e for e in entries if e.errors]
    warn = [e for e in entries if e.warnings and not e.errors]
    texfail = [e for e in entries if e.latex_error]
    out = [
        "= Math benchmark: redocx vs LaTeX",
        "",
        f"{n} formulas. redocx rejects {len(err)}, warns on {len(warn)}, "
        f"accepts {n - len(err)} ({100 * (n - len(err)) // max(n, 1)}%). "
        f"LaTeX itself fails on {len(texfail)}.",
        "",
        "Accepted does not mean right: compare the last two columns.",
        "",
        "== By category",
        "",
        "\\table[header-rows=1, widths=(8cm, 2cm, 2cm, 2cm)]{",
        "  | Category | Formulas | Rejected | LaTeX fails |",
    ]
    cats: dict[str, list[Entry]] = defaultdict(list)
    for e in entries:
        cats[e.category].append(e)
    for cat, es in cats.items():
        out.append(
            f"  | {escape.inline(cat)} | {len(es)} | {sum(bool(e.errors) for e in es)} "
            f"| {sum(bool(e.latex_error) for e in es)} |"
        )
    out.append("}")
    out += ["", "== redocx errors", "", "\\table[header-rows=1, widths=(1fr, 1.5cm, 8cm)]{"]
    out.append("  | Message | Count | Rows |")
    kinds: Counter[str] = Counter()
    rows: dict[str, list[int]] = defaultdict(list)
    for e in err:
        key = e.errors[0].split(" (")[0]
        kinds[key] += 1
        rows[key].append(e.n)
    for key, count in kinds.most_common():
        nums = ", ".join(str(x) for x in rows[key])
        out.append(f"  | {escape.inline(key)} | {count} | {nums} |")
    out.append("}")
    return "\n".join(out)


def document(entries: list[Entry]) -> str:
    head = [
        "% Generated by tools/math-bench/make_bench.py; do not edit.",
        "\\document[lang=en-GB]",
        '\\meta[title="Math benchmark"]',
        "\\section[size=A4, orientation=landscape, margins=1.2cm]",
        "",
        summary(entries),
        "",
        "\\pagebreak",
        "",
        "== Formulas",
        "",
        "\\table[widths=(0.9cm, 3.2cm, 6.5cm, 1fr, 1fr)]{",
        "\\row[header]{ \\cell{*\\#*} \\cell{*Formula*} \\cell{*LaTeX source*} "
        "\\cell{*redocx (Word equation)*} \\cell{*LaTeX reference*} }",
    ]
    return "\n".join(head + [row(e) for e in entries] + ["}", ""])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("formulas", nargs="?", default=str(HERE / "formulas.json"))
    ap.add_argument("--out", default=str(HERE / "out"))
    ap.add_argument("--only", default="", help="comma-separated ids")
    ap.add_argument("--fresh", action="store_true", help="typeset the references again")
    args = ap.parse_args()
    for tool in ("latex", "dvisvgm", "dvipng"):
        if shutil.which(tool) is None:
            print(f"{tool} is not on PATH", file=sys.stderr)
            return 2
    data = json.loads(Path(args.formulas).read_text(encoding="utf-8"))
    only = set(filter(None, args.only.split(",")))
    entries = [
        Entry(i + 1, d["id"], d["category"], d["title"], d["latex"])
        for i, d in enumerate(data)
        if not only or d["id"] in only
    ]
    out = Path(args.out)
    refdir = out / "ref"
    if args.fresh and refdir.exists():
        shutil.rmtree(refdir)
    refdir.mkdir(parents=True, exist_ok=True)
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda e: reference(e, refdir), entries))
    for e in entries:
        check(e)
    rdx = out / "bench.rdx"
    rdx.write_text(document(entries), encoding="utf-8")
    from redocx.syntax.source import Source

    data_bytes, result = build(Source.from_path(rdx))
    if result.diagnostics:
        print(result.render_diagnostics(), file=sys.stderr)
    if data_bytes is None:
        print("bench.rdx did not build", file=sys.stderr)
        return 1
    (out / "bench.docx").write_bytes(data_bytes)
    rejected = sum(bool(e.errors) for e in entries)
    texfail = sum(bool(e.latex_error) for e in entries)
    print(f"{len(entries)} formulas, redocx rejects {rejected}, LaTeX fails {texfail}")
    print(f"wrote {out / 'bench.docx'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
