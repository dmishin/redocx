import pytest

from redocx.ooxml.writer import to_docx_bytes
from tests.golden.cases import CASES
from tests.support import golden
from tests.support.xsd import validate_package


@pytest.mark.parametrize("case", sorted(CASES))
def test_snapshot(case: str) -> None:
    problems = golden.check(case, to_docx_bytes(CASES[case]()))
    assert not problems, "\n".join(problems)


@pytest.mark.parametrize("case", sorted(CASES))
def test_schema_valid(case: str) -> None:
    errors = validate_package(to_docx_bytes(CASES[case]()))
    assert not errors, errors


@pytest.mark.parametrize("case", sorted(CASES))
def test_deterministic(case: str) -> None:
    assert to_docx_bytes(CASES[case]()) == to_docx_bytes(CASES[case]())
