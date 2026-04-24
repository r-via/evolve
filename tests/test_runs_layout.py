"""Tests for evolve.state._runs_base and _ensure_runs_layout.

Covers SPEC.md § "The .evolve/ directory" — canonical path resolution,
legacy fallback, migration via git mv, and ambiguous-state detection.
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
        with patch("evolve.state._subprocess.run", return_value=mock_run) as m:
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

        with patch("evolve.state._subprocess.run", side_effect=subprocess.CalledProcessError(1, "git")):
            result = _ensure_runs_layout(tmp_path)

        assert result == tmp_path / ".evolve" / "runs"
        assert result.is_dir()
        # File was moved
        assert (result / "test.md").read_text() == "content"
        assert not legacy.exists()
