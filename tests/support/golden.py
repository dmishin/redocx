"""Golden snapshots: every package entry, canonicalized, compared with committed files.

Snapshots live in tests/golden/expected/<case>/<entry>. XML entries are stored as
canonical XML (C14N 2.0: sorted attributes, no insignificant whitespace), then
indented for readable diffs; other entries as their SHA-256.

To accept new output: set REDOCX_UPDATE_GOLDEN=1 and run the golden tests,
then review the snapshot diff with git before committing.
"""

import hashlib
import os
import zipfile
from io import BytesIO
from pathlib import Path

from lxml import etree

from tests.support.xsd import is_xml

EXPECTED = Path(__file__).resolve().parents[1] / "golden" / "expected"
UPDATE = os.environ.get("REDOCX_UPDATE_GOLDEN") == "1"


def canonical_xml(data: bytes) -> str:
    canonical = etree.canonicalize(data.decode("utf-8"), strip_text=True)
    tree = etree.fromstring(canonical.encode("utf-8"))
    etree.indent(tree, space="  ")
    return etree.tostring(tree, encoding="unicode") + "\n"


def snapshot(docx: bytes) -> dict[str, str]:
    """Entry name -> snapshot text."""
    result: dict[str, str] = {}
    with zipfile.ZipFile(BytesIO(docx)) as archive:
        for name in archive.namelist():
            data = archive.read(name)
            if is_xml(name):
                result[name] = canonical_xml(data)
            else:
                result[f"{name}.sha256"] = hashlib.sha256(data).hexdigest() + "\n"
    return result


def _path(case: str, entry: str) -> Path:
    # "[Content_Types].xml" is a fine file name on all platforms.
    return EXPECTED / case / entry


def check(case: str, docx: bytes) -> list[str]:
    """Compare (or, in update mode, rewrite) a case's snapshots; return problems."""
    actual = snapshot(docx)
    case_dir = EXPECTED / case
    existing: set[str] = (
        {p.relative_to(case_dir).as_posix() for p in case_dir.rglob("*") if p.is_file()}
        if case_dir.exists()
        else set()
    )

    if UPDATE:
        for stale in existing - actual.keys():
            _path(case, stale).unlink()
        for entry, text in actual.items():
            path = _path(case, entry)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8", newline="\n")
        return []

    problems = [f"missing snapshot: {e}" for e in sorted(actual.keys() - existing)]
    problems += [f"entry no longer produced: {e}" for e in sorted(existing - actual.keys())]
    for entry in sorted(actual.keys() & existing):
        expected = _path(case, entry).read_text(encoding="utf-8")
        if expected != actual[entry]:
            problems.append(
                f"snapshot differs: {entry}\n--- expected\n{expected}--- actual\n{actual[entry]}"
            )
    if problems:
        problems.append("(set REDOCX_UPDATE_GOLDEN=1 to accept the new output)")
    return problems
