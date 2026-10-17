"""Validate generated packages with Microsoft's Open XML SDK (tools/ooxml-validate)."""

import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest

from tests.golden.cases import CASES
from wordgenx.compiler import build
from wordgenx.ooxml import Package
from wordgenx.ooxml.writer import write_docx
from wordgenx.syntax import Source

ROOT = Path(__file__).resolve().parents[2]
PROJECT = ROOT / "tools" / "ooxml-validate"
EXAMPLES = sorted(
    p
    for p in [*(ROOT / "examples").rglob("*.wgx"), *(ROOT / "docs" / "book").glob("*.wgx")]
    if "spike" not in p.parts and p.parent.name != "chapters"
)


def _dotnet_sdk_available() -> bool:
    dotnet = shutil.which("dotnet")
    if dotnet is None:
        return False
    result = subprocess.run([dotnet, "--list-sdks"], capture_output=True, text=True, check=False)
    return result.returncode == 0 and bool(result.stdout.strip())


pytestmark = [
    pytest.mark.validator,
    pytest.mark.skipif(not _dotnet_sdk_available(), reason=".NET SDK not installed"),
]


@pytest.fixture(scope="session")
def validator_dll(tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("ooxml-validate")
    subprocess.run(
        ["dotnet", "build", str(PROJECT), "-c", "Release", "-o", str(out), "--nologo", "-v", "q"],
        check=True,
        capture_output=True,
    )
    return out / "ooxml-validate.dll"


def _validate(dll: Path, *paths: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["dotnet", str(dll), *map(str, paths)], capture_output=True, text=True, check=False
    )


def test_hello_package_is_valid(
    validator_dll: Path, hello_package: Callable[[], Package], tmp_path: Path
) -> None:
    path = tmp_path / "hello.docx"
    hello_package().save(path)
    result = _validate(validator_dll, path)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("case", sorted(CASES))
def test_golden_case_is_valid(validator_dll: Path, case: str, tmp_path: Path) -> None:
    path = tmp_path / f"{case}.docx"
    write_docx(CASES[case](), path)
    result = _validate(validator_dll, path)
    assert result.returncode == 0, result.stdout + result.stderr


def test_examples_are_valid(validator_dll: Path, tmp_path: Path) -> None:
    """Every example and the book, compiled from source: one validator run for all."""
    paths: list[Path] = []
    for example in EXAMPLES:
        data, result = build(Source.from_path(example))
        assert data is not None, result.render_diagnostics()
        paths.append(tmp_path / f"{example.stem}.docx")
        paths[-1].write_bytes(data)
    result = _validate(validator_dll, *paths)
    assert result.returncode == 0, result.stdout + result.stderr


def test_validator_reports_schema_errors(
    validator_dll: Path, hello_package: Callable[[], Package], tmp_path: Path
) -> None:
    pkg = hello_package()
    document = pkg.part("/word/document.xml")
    document.data = document.data.replace(b"<w:body>", b"<w:body><w:bogus/>")
    path = tmp_path / "broken.docx"
    pkg.save(path)
    result = _validate(validator_dll, path)
    assert result.returncode == 1
    assert "bogus" in result.stdout
