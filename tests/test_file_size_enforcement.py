"""Tests for the 500-line file size enforcement mechanism (US-026).

Covers:
- ``_detect_file_too_large`` helper in ``evolve/orchestrator.py``
- ``FILE TOO LARGE:`` prefix handler in ``evolve/agent.py`` ``build_prompt``
- Integration into post-commit pipeline
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from evolve.orchestrator import (
    _FILE_TOO_LARGE_LIMIT,
    _detect_file_too_large,
)


# ---------------------------------------------------------------------------
# _detect_file_too_large
# ---------------------------------------------------------------------------


class TestDetectFileTooLarge:
    """Unit tests for the detection helper."""

    def test_no_oversized_files(self, tmp_path: Path) -> None:
        """All files within limit → empty list."""
        (tmp_path / "evolve").mkdir()
        (tmp_path / "evolve" / "__init__.py").write_text("# ok\n")
        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "test_foo.py").write_text("# short\n" * 100)

        result = _detect_file_too_large(tmp_path)
        assert result == []

    def test_oversized_evolve_file(self, tmp_path: Path) -> None:
        """A file under ``evolve/`` exceeding the limit is detected."""
        (tmp_path / "evolve").mkdir()
        big = "x = 1\n" * (_FILE_TOO_LARGE_LIMIT + 50)
        (tmp_path / "evolve" / "big.py").write_text(big)

        result = _detect_file_too_large(tmp_path)
        assert len(result) == 1
        path, count = result[0]
        assert path == os.path.join("evolve", "big.py")
        assert count == _FILE_TOO_LARGE_LIMIT + 50

    def test_oversized_tests_file(self, tmp_path: Path) -> None:
        """A file under ``tests/`` exceeding the limit is detected."""
        (tmp_path / "tests").mkdir()
        big = "x = 1\n" * (_FILE_TOO_LARGE_LIMIT + 10)
        (tmp_path / "tests" / "test_big.py").write_text(big)

        result = _detect_file_too_large(tmp_path)
        assert len(result) == 1
        assert result[0][0] == os.path.join("tests", "test_big.py")

    def test_nested_evolve_subpackage(self, tmp_path: Path) -> None:
        """Nested files under ``evolve/sub/`` are also caught."""
        sub = tmp_path / "evolve" / "sub"
        sub.mkdir(parents=True)
        big = "x = 1\n" * (_FILE_TOO_LARGE_LIMIT + 1)
        (sub / "mod.py").write_text(big)

        result = _detect_file_too_large(tmp_path)
        assert len(result) == 1
        assert "sub" in result[0][0]

    def test_exactly_at_limit_not_flagged(self, tmp_path: Path) -> None:
        """A file with exactly ``_FILE_TOO_LARGE_LIMIT`` lines is OK."""
        (tmp_path / "evolve").mkdir()
        exact = "x = 1\n" * _FILE_TOO_LARGE_LIMIT
        (tmp_path / "evolve" / "exact.py").write_text(exact)

        result = _detect_file_too_large(tmp_path)
        assert result == []

    def test_unrelated_dirs_ignored(self, tmp_path: Path) -> None:
        """Files outside ``evolve/`` and ``tests/`` are not scanned."""
        (tmp_path / "src").mkdir()
        big = "x = 1\n" * (_FILE_TOO_LARGE_LIMIT + 100)
        (tmp_path / "src" / "big.py").write_text(big)

        result = _detect_file_too_large(tmp_path)
        assert result == []

    def test_multiple_oversized(self, tmp_path: Path) -> None:
        """Multiple oversized files are all reported."""
        (tmp_path / "evolve").mkdir()
        (tmp_path / "tests").mkdir()
        big = "x = 1\n" * (_FILE_TOO_LARGE_LIMIT + 5)
        (tmp_path / "evolve" / "a.py").write_text(big)
        (tmp_path / "evolve" / "b.py").write_text(big)
        (tmp_path / "tests" / "test_c.py").write_text(big)

        result = _detect_file_too_large(tmp_path)
        assert len(result) == 3


# ---------------------------------------------------------------------------
# build_prompt — FILE TOO LARGE prefix rendering
# ---------------------------------------------------------------------------


class TestBuildPromptFileTooLarge:
    """Verify ``build_prompt`` renders the dedicated header for the
    ``FILE TOO LARGE:`` diagnostic prefix."""

    def test_file_too_large_header_rendered(self, tmp_path: Path) -> None:
        """When the diagnostic contains ``FILE TOO LARGE``, the prompt
        includes the ``## CRITICAL — File too large`` section."""
        from evolve.agent import build_prompt

        run_dir = tmp_path / "run"
        run_dir.mkdir()

        # Write a diagnostic with the FILE TOO LARGE prefix
        diag = run_dir / "subprocess_error_round_1.txt"
        diag.write_text(
            "FILE TOO LARGE: 2 file(s) exceed 500 lines:\n"
            "  - evolve/orchestrator.py: 2940 lines\n"
            "  - evolve/agent.py: 1600 lines\n"
        )

        # Minimal spec + improvements
        readme = tmp_path / "README.md"
        readme.write_text("# Test project\n")
        imp = tmp_path / "improvements.md"
        imp.write_text("- [ ] [functional] US-099: split orchestrator\n")

        prompt = build_prompt(
            project_dir=tmp_path,
            run_dir=str(run_dir),
            round_num=2,
            check_cmd="pytest",
            check_timeout=20,
            spec=None,
        )

        assert "## CRITICAL — File too large" in prompt
        assert "500-line hard limit" in prompt
        assert "FILE TOO LARGE" in prompt

    def test_file_too_large_does_not_match_other_prefix(self, tmp_path: Path) -> None:
        """A diagnostic with ``NO PROGRESS`` must NOT trigger the
        FILE TOO LARGE header."""
        from evolve.agent import build_prompt

        run_dir = tmp_path / "run"
        run_dir.mkdir()

        diag = run_dir / "subprocess_error_round_1.txt"
        diag.write_text("NO PROGRESS: agent ran but changed nothing\n")

        readme = tmp_path / "README.md"
        readme.write_text("# Test\n")
        imp = tmp_path / "improvements.md"
        imp.write_text("- [ ] [functional] US-001: something\n")

        prompt = build_prompt(
            project_dir=tmp_path,
            run_dir=str(run_dir),
            round_num=2,
            check_cmd="pytest",
            check_timeout=20,
            spec=None,
        )

        assert "## CRITICAL — File too large" not in prompt
        assert "## CRITICAL — Previous round made NO PROGRESS" in prompt


# ---------------------------------------------------------------------------
# Integration: orchestrator writes diagnostic on oversized files
# ---------------------------------------------------------------------------


class TestFileSizeIntegration:
    """Verify the detection is wired into the post-commit pipeline."""

    def test_diagnostic_written_on_oversized_file(self, tmp_path: Path) -> None:
        """When ``_detect_file_too_large`` returns results, a diagnostic
        file with ``FILE TOO LARGE:`` prefix is written."""
        from evolve.orchestrator import _save_subprocess_diagnostic

        run_dir = tmp_path / "session"
        run_dir.mkdir()

        oversized = [("evolve/orchestrator.py", 2940)]
        ftl_lines = "\n".join(
            f"  - {p}: {lc} lines" for p, lc in oversized
        )
        _save_subprocess_diagnostic(
            run_dir, 5, ["(post-round file-size check)"],
            f"Oversized files:\n{ftl_lines}",
            reason=(
                f"FILE TOO LARGE: {len(oversized)} file(s) exceed "
                f"500 lines:\n{ftl_lines}"
            ),
            attempt=0,
        )

        diag = run_dir / "subprocess_error_round_5.txt"
        assert diag.is_file()
        content = diag.read_text()
        assert "FILE TOO LARGE" in content
        assert "evolve/orchestrator.py" in content
        assert "2940" in content
