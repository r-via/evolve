"""Tests for TDD violation detection — US-051.

Covers:
- _detect_tdd_violation: detection with production-only commit, no-op
  with production+test commit, no-op with structural commit
- TDD VIOLATION: prefix handler in prompt_diagnostics.py
  build_prev_crash_section
- Integration: round_success.py imports _detect_tdd_violation
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from evolve.diagnostics import _detect_tdd_violation


# ---------------------------------------------------------------------------
# _detect_tdd_violation tests
# ---------------------------------------------------------------------------


class TestDetectTddViolation:
    """Tests for the _detect_tdd_violation helper."""

    def _mock_git_diff(self, files: list[str]):
        """Return a patch context for git diff-tree returning given files."""
        result = MagicMock()
        result.returncode = 0
        result.stdout = "\n".join(files) + "\n" if files else ""
        return patch(
            "evolve.infrastructure.diagnostics.detector.subprocess.run", return_value=result
        )

    def test_production_only_commit_detected(self, tmp_path: Path):
        """Production files without test changes → violation."""
        with self._mock_git_diff(["evolve/agent.py", "evolve/cli.py"]):
            result = _detect_tdd_violation(
                tmp_path, tmp_path / "run", 1, is_structural=False
            )
        assert result is not None
        assert "evolve/agent.py" in result
        assert "evolve/cli.py" in result

    def test_production_with_tests_no_violation(self, tmp_path: Path):
        """Production + test files → no violation."""
        with self._mock_git_diff(
            ["evolve/agent.py", "tests/test_agent.py"]
        ):
            result = _detect_tdd_violation(
                tmp_path, tmp_path / "run", 1, is_structural=False
            )
        assert result is None

    def test_structural_commit_exempt(self, tmp_path: Path):
        """Structural commits are exempt even without tests."""
        with self._mock_git_diff(["evolve/agent.py"]):
            result = _detect_tdd_violation(
                tmp_path, tmp_path / "run", 1, is_structural=True
            )
        assert result is None

    def test_test_only_commit_no_violation(self, tmp_path: Path):
        """Only test files changed → no violation."""
        with self._mock_git_diff(["tests/test_foo.py"]):
            result = _detect_tdd_violation(
                tmp_path, tmp_path / "run", 1, is_structural=False
            )
        assert result is None

    def test_no_python_files_no_violation(self, tmp_path: Path):
        """Non-Python files only → no violation."""
        with self._mock_git_diff(["README.md", "SPEC.md"]):
            result = _detect_tdd_violation(
                tmp_path, tmp_path / "run", 1, is_structural=False
            )
        assert result is None

    def test_empty_diff_no_violation(self, tmp_path: Path):
        """Empty diff → no violation."""
        with self._mock_git_diff([]):
            result = _detect_tdd_violation(
                tmp_path, tmp_path / "run", 1, is_structural=False
            )
        assert result is None

    def test_git_failure_returns_none(self, tmp_path: Path):
        """Git command failure → no violation (graceful)."""
        result_mock = MagicMock()
        result_mock.returncode = 1
        with patch(
            "evolve.infrastructure.diagnostics.detector.subprocess.run",
            return_value=result_mock,
        ):
            result = _detect_tdd_violation(
                tmp_path, tmp_path / "run", 1, is_structural=False
            )
        assert result is None

    def test_non_evolve_production_files_ignored(self, tmp_path: Path):
        """Files outside evolve/ are not counted as production."""
        with self._mock_git_diff(["src/main.py", "lib/utils.py"]):
            result = _detect_tdd_violation(
                tmp_path, tmp_path / "run", 1, is_structural=False
            )
        assert result is None

    def test_mixed_evolve_and_non_py_no_violation(self, tmp_path: Path):
        """evolve/ .md files don't count as production Python."""
        with self._mock_git_diff(
            ["evolve/README.md", "tests/test_foo.py"]
        ):
            result = _detect_tdd_violation(
                tmp_path, tmp_path / "run", 1, is_structural=False
            )
        assert result is None


# ---------------------------------------------------------------------------
# build_prev_crash_section TDD VIOLATION prefix test
# ---------------------------------------------------------------------------


class TestBuildPrevCrashTddViolation:
    """Test the TDD VIOLATION branch in build_prev_crash_section."""

    def test_tdd_violation_renders_section(self):
        from evolve.prompt_diagnostics import build_prev_crash_section

        diag = (
            "TDD VIOLATION: Production files modified without "
            "test changes: evolve/agent.py, evolve/cli.py"
        )
        result = build_prev_crash_section(diag)
        assert "## CRITICAL" in result
        assert "TDD violation" in result
        assert "test written first" in result.lower() or "test" in result
        assert "structural commits" in result.lower()
        assert diag in result

    def test_tdd_violation_takes_priority_over_generic(self):
        """TDD VIOLATION prefix is matched before the generic fallback."""
        from evolve.prompt_diagnostics import build_prev_crash_section

        diag = "TDD VIOLATION: test"
        result = build_prev_crash_section(diag)
        assert "CRASHED" not in result
        assert "TDD violation" in result


# ---------------------------------------------------------------------------
# Integration: round_success.py uses _detect_tdd_violation
# ---------------------------------------------------------------------------


class TestRoundSuccessIntegration:
    """Verify round_success.py uses _detect_tdd_violation."""

    def test_round_success_imports_detect_tdd_violation(self):
        src = (
            Path(__file__).resolve().parent.parent
            / "evolve" / "round_success.py"
        ).read_text()
        assert "_detect_tdd_violation" in src

    def test_orchestrator_re_exports_detect_tdd_violation(self):
        import evolve.orchestrator as orch

        assert hasattr(orch, "_detect_tdd_violation")
        from evolve.diagnostics import (
            _detect_tdd_violation as orig,
        )
        assert orch._detect_tdd_violation is orig
