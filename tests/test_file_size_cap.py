"""Unified 500-line file size cap enforcement per SPEC § "Hard rule:
source files MUST NOT exceed 500 lines".

A single test scans every ``*.py`` file under ``evolve/`` and
``tests/`` and fails on any file exceeding 500 lines (comments
and blank lines included, per SPEC).
"""

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
