"""Unified 500-line file size cap enforcement per SPEC § "Hard rule:
source files MUST NOT exceed 500 lines".

A single test scans every ``*.py`` file under ``evolve/`` and
``tests/`` and fails on any file exceeding 500 lines (comments
and blank lines included, per SPEC).
"""

import pytest
from pathlib import Path


_PROJECT_ROOT = Path(__file__).resolve().parent.parent


def test_all_python_files_under_500_lines():
    """Every *.py file under evolve/ and tests/ must be ≤ 500 lines."""
    violations: list[tuple[str, int]] = []

    for directory in ("evolve", "tests"):
        base = _PROJECT_ROOT / directory
        if not base.is_dir():
            continue
        for py_file in sorted(base.rglob("*.py")):
            line_count = len(py_file.read_text().splitlines())
            if line_count > 500:
                rel = py_file.relative_to(_PROJECT_ROOT)
                violations.append((str(rel), line_count))

    if violations:
        report = "\n".join(
            f"  {path}: {count} lines" for path, count in violations
        )
        raise AssertionError(
            f"{len(violations)} file(s) exceed the 500-line cap:\n{report}"
        )


def test_cap_violation_detected(tmp_path):
    """Negative test: verify the assertion fires on an oversized file."""
    # Create a temporary >500-line .py file
    big_file = tmp_path / "evolve" / "too_big.py"
    big_file.parent.mkdir(parents=True, exist_ok=True)
    big_file.write_text("\n".join(f"x = {i}" for i in range(501)))

    violations: list[tuple[str, int]] = []
    for py_file in sorted(tmp_path.joinpath("evolve").rglob("*.py")):
        line_count = len(py_file.read_text().splitlines())
        if line_count > 500:
            violations.append((str(py_file.relative_to(tmp_path)), line_count))

    assert len(violations) == 1
    path, count = violations[0]
    assert "too_big.py" in path
    assert count == 501

    # Verify the report format matches what the real test produces
    report = "\n".join(
        f"  {p}: {c} lines" for p, c in violations
    )
    assert "too_big.py: 501 lines" in report
