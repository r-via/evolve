"""Tests for evolve.state._runs_base and _ensure_runs_layout.

Covers SPEC.md § "The .evolve/ directory" — canonical path resolution,
legacy fallback, migration via git mv, and ambiguous-state detection.
Also tests the wiring of _ensure_runs_layout into evolve_loop startup.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from evolve.state import _runs_base, _ensure_runs_layout, _RunsLayoutError


class TestRunsBase:
    """Tests for _runs_base — canonical vs legacy path resolution."""

    def test_returns_canonical_when_evolve_runs_exists(self, tmp_path: Path):
        canonical = tmp_path / ".evolve" / "runs"
        canonical.mkdir(parents=True)
        assert _runs_base(tmp_path) == canonical

    def test_returns_legacy_when_only_runs_exists(self, tmp_path: Path):
        legacy = tmp_path / "runs"
        legacy.mkdir()
        assert _runs_base(tmp_path) == legacy

    def test_returns_canonical_when_neither_exists(self, tmp_path: Path):
        result = _runs_base(tmp_path)
        assert result == tmp_path / ".evolve" / "runs"

    def test_prefers_canonical_when_both_exist(self, tmp_path: Path):
        (tmp_path / ".evolve" / "runs").mkdir(parents=True)
        (tmp_path / "runs").mkdir()
        assert _runs_base(tmp_path) == tmp_path / ".evolve" / "runs"


class TestEnsureRunsLayout:
    """Tests for _ensure_runs_layout — migration and error cases."""

    def test_creates_canonical_when_neither_exists(self, tmp_path: Path):
        result = _ensure_runs_layout(tmp_path)
        assert result == tmp_path / ".evolve" / "runs"
        assert result.is_dir()

    def test_returns_canonical_when_already_exists(self, tmp_path: Path):
        canonical = tmp_path / ".evolve" / "runs"
        canonical.mkdir(parents=True)
        result = _ensure_runs_layout(tmp_path)
        assert result == canonical

    def test_raises_on_ambiguous_state(self, tmp_path: Path):
        (tmp_path / ".evolve" / "runs").mkdir(parents=True)
        (tmp_path / "runs").mkdir()
        with pytest.raises(_RunsLayoutError, match="Both.*exist"):
            _ensure_runs_layout(tmp_path)

    def test_migrates_legacy_via_git_mv(self, tmp_path: Path):
        legacy = tmp_path / "runs"
        legacy.mkdir()
        (legacy / "improvements.md").write_text("test")

        mock_run = MagicMock(returncode=0)
        with patch("evolve.infrastructure.filesystem.state_manager._subprocess.run", return_value=mock_run) as m:
            result = _ensure_runs_layout(tmp_path)

        assert result == tmp_path / ".evolve" / "runs"
        # git mv was called
        call_args = m.call_args
        assert "git" in call_args[0][0]
        assert "mv" in call_args[0][0]

    def test_migrates_legacy_fallback_on_git_failure(self, tmp_path: Path):
        legacy = tmp_path / "runs"
        legacy.mkdir()
        (legacy / "test.md").write_text("content")

        with patch("evolve.infrastructure.filesystem.state_manager._subprocess.run", side_effect=subprocess.CalledProcessError(1, "git")):
            result = _ensure_runs_layout(tmp_path)

        assert result == tmp_path / ".evolve" / "runs"
        assert result.is_dir()
        # File was moved
        assert (result / "test.md").read_text() == "content"
        assert not legacy.exists()


class TestEnsureRunsLayoutWiring:
    """Tests that evolve_loop calls _ensure_runs_layout at startup."""

    def test_evolve_loop_calls_ensure_runs_layout(self, tmp_path: Path):
        """evolve_loop calls _ensure_runs_layout before any path usage (AC 1)."""
        from evolve.orchestrator import evolve_loop

        (tmp_path / "README.md").write_text("# Test")
        (tmp_path / ".evolve" / "runs").mkdir(parents=True)

        with patch("evolve.orchestrator._ensure_runs_layout") as mock_ensure, \
             patch("evolve.orchestrator._ensure_git"), \
             patch("evolve.orchestrator._run_rounds"):
            evolve_loop(tmp_path, max_rounds=1)

        mock_ensure.assert_called_once_with(tmp_path)

    def test_evolve_loop_exits_on_runs_layout_error(self, tmp_path: Path):
        """_RunsLayoutError causes sys.exit(2) with error message (AC 2)."""
        from evolve.orchestrator import evolve_loop

        (tmp_path / "README.md").write_text("# Test")

        with patch("evolve.orchestrator._ensure_runs_layout",
                   side_effect=_RunsLayoutError("Both exist")), \
             patch("evolve.orchestrator.get_tui") as mock_tui, \
             pytest.raises(SystemExit, match="2"):
            evolve_loop(tmp_path, max_rounds=1)

        mock_tui().error.assert_called_once()
        assert "Both exist" in mock_tui().error.call_args[0][0]
