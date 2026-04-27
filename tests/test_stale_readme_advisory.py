"""Tests for the startup-time stale-README advisory.

Covers SPEC.md § "Stale-README pre-flight check" — the lightweight
observability signal emitted at the very start of ``evolve start`` when
``--spec`` points at a file other than ``README.md`` and the spec has
drifted ahead of the README by more than the configured threshold in
days. Pure observability: never blocks, never modifies any file, never
runs during rounds.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from evolve.diagnostics import (
    _DEFAULT_README_STALE_THRESHOLD_DAYS,
    _README_STALE_ADVISORY_FMT,
    _emit_stale_readme_advisory,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mk_project(
    tmp_path: Path,
    readme_age_days: float = 0.0,
    spec_age_days: float = 0.0,
    spec_name: str = "SPEC.md",
) -> Path:
    """Create a project directory with README.md and a spec file whose
    mtimes are N days old (now - N*86400).
    """
    readme = tmp_path / "README.md"
    readme.write_text("# README\n")
    spec = tmp_path / spec_name
    spec.write_text("# SPEC\n")

    now = time.time()
    os.utime(readme, (now - readme_age_days * 86400, now - readme_age_days * 86400))
    os.utime(spec, (now - spec_age_days * 86400, now - spec_age_days * 86400))
    return tmp_path


# ---------------------------------------------------------------------------
# No-op cases
# ---------------------------------------------------------------------------


class TestNoOpCases:
    """The advisory must be silent in these documented no-op scenarios."""

    def test_spec_none_is_noop(self, tmp_path: Path):
        """When --spec is unset, README IS the spec → no advisory."""
        _mk_project(tmp_path, readme_age_days=100.0, spec_age_days=0.0)
        ui = MagicMock()
        _emit_stale_readme_advisory(tmp_path, None, ui)
        ui.info.assert_not_called()

    def test_spec_equals_readme_is_noop(self, tmp_path: Path):
        """When spec == "README.md", README IS the spec → no advisory."""
        _mk_project(tmp_path, readme_age_days=100.0, spec_age_days=0.0)
        ui = MagicMock()
        _emit_stale_readme_advisory(tmp_path, "README.md", ui)
        ui.info.assert_not_called()

    def test_missing_spec_file_is_noop(self, tmp_path: Path):
        """When --spec points at a non-existent file, silently no-op."""
        (tmp_path / "README.md").write_text("# README\n")
        ui = MagicMock()
        _emit_stale_readme_advisory(tmp_path, "DOES_NOT_EXIST.md", ui)
        ui.info.assert_not_called()

    def test_missing_readme_file_is_noop(self, tmp_path: Path):
        """When README.md is missing, silently no-op — nothing to compare."""
        (tmp_path / "SPEC.md").write_text("# SPEC\n")
        ui = MagicMock()
        _emit_stale_readme_advisory(tmp_path, "SPEC.md", ui)
        ui.info.assert_not_called()

    def test_readme_newer_than_spec_is_noop(self, tmp_path: Path):
        """When README is newer than the spec, no advisory — drift is negative."""
        _mk_project(tmp_path, readme_age_days=0.0, spec_age_days=100.0)
        ui = MagicMock()
        _emit_stale_readme_advisory(tmp_path, "SPEC.md", ui)
        ui.info.assert_not_called()

    def test_threshold_zero_disables_advisory(self, tmp_path: Path, monkeypatch):
        """Threshold of 0 disables the advisory entirely (even at huge drift)."""
        _mk_project(tmp_path, readme_age_days=1000.0, spec_age_days=0.0)
        monkeypatch.setenv("EVOLVE_README_STALE_THRESHOLD_DAYS", "0")
        ui = MagicMock()
        _emit_stale_readme_advisory(tmp_path, "SPEC.md", ui)
        ui.info.assert_not_called()


# ---------------------------------------------------------------------------
# Threshold boundaries
# ---------------------------------------------------------------------------


class TestThresholdBoundaries:
    """The advisory fires only when drift > threshold (strict)."""

    def test_drift_below_default_threshold_silent(self, tmp_path: Path):
        """With default 30-day threshold, 15 days of drift is silent."""
        _mk_project(tmp_path, readme_age_days=15.0, spec_age_days=0.0)
        ui = MagicMock()
        _emit_stale_readme_advisory(tmp_path, "SPEC.md", ui)
        ui.info.assert_not_called()

    def test_drift_at_default_threshold_silent(self, tmp_path: Path):
        """Drift == threshold is NOT > threshold → silent."""
        # README is exactly 30 days old, spec is fresh → drift = 30 days
        _mk_project(tmp_path, readme_age_days=30.0, spec_age_days=0.0)
        ui = MagicMock()
        _emit_stale_readme_advisory(tmp_path, "SPEC.md", ui)
        ui.info.assert_not_called()

    def test_drift_above_default_threshold_fires(self, tmp_path: Path):
        """With default 30-day threshold, 42 days of drift fires advisory."""
        _mk_project(tmp_path, readme_age_days=42.0, spec_age_days=0.0)
        ui = MagicMock()
        _emit_stale_readme_advisory(tmp_path, "SPEC.md", ui)
        ui.info.assert_called_once()
        msg = ui.info.call_args[0][0]
        # Message contains the drift in days and the `evolve sync-readme` hint.
        assert "42" in msg
        assert "README has not been updated" in msg
        assert "evolve sync-readme" in msg

    def test_default_threshold_constant_matches_spec(self):
        """SPEC.md documents the default threshold as 30 days."""
        assert _DEFAULT_README_STALE_THRESHOLD_DAYS == 30

    def test_advisory_format_matches_spec(self):
        """The advisory format string matches the documented message."""
        rendered = _README_STALE_ADVISORY_FMT.format(days=42)
        assert "README has not been updated in 42 days" in rendered
        assert "evolve sync-readme" in rendered


# ---------------------------------------------------------------------------
# Threshold configuration sources
# ---------------------------------------------------------------------------


class TestThresholdConfig:
    """Threshold is read from env > evolve.toml > default."""

    def test_env_var_overrides_default(self, tmp_path: Path, monkeypatch):
        """EVOLVE_README_STALE_THRESHOLD_DAYS env var overrides default."""
        _mk_project(tmp_path, readme_age_days=10.0, spec_age_days=0.0)
        # 10 days of drift: default 30 would be silent, env 5 should fire.
        monkeypatch.setenv("EVOLVE_README_STALE_THRESHOLD_DAYS", "5")
        ui = MagicMock()
        _emit_stale_readme_advisory(tmp_path, "SPEC.md", ui)
        ui.info.assert_called_once()
        assert "10" in ui.info.call_args[0][0]

    def test_evolve_toml_threshold_honored(self, tmp_path: Path, monkeypatch):
        """readme_stale_threshold_days in evolve.toml is honored."""
        monkeypatch.delenv("EVOLVE_README_STALE_THRESHOLD_DAYS", raising=False)
        _mk_project(tmp_path, readme_age_days=10.0, spec_age_days=0.0)
        (tmp_path / "evolve.toml").write_text(
            "readme_stale_threshold_days = 5\n"
        )
        ui = MagicMock()
        _emit_stale_readme_advisory(tmp_path, "SPEC.md", ui)
        ui.info.assert_called_once()

    def test_evolve_toml_zero_disables(self, tmp_path: Path, monkeypatch):
        """readme_stale_threshold_days = 0 in evolve.toml disables advisory."""
        monkeypatch.delenv("EVOLVE_README_STALE_THRESHOLD_DAYS", raising=False)
        _mk_project(tmp_path, readme_age_days=1000.0, spec_age_days=0.0)
        (tmp_path / "evolve.toml").write_text(
            "readme_stale_threshold_days = 0\n"
        )
        ui = MagicMock()
        _emit_stale_readme_advisory(tmp_path, "SPEC.md", ui)
        ui.info.assert_not_called()

    def test_env_beats_evolve_toml(self, tmp_path: Path, monkeypatch):
        """Env var takes precedence over evolve.toml (documented resolution order)."""
        _mk_project(tmp_path, readme_age_days=10.0, spec_age_days=0.0)
        # toml says threshold 5 (would fire), env says 30 (would be silent).
        (tmp_path / "evolve.toml").write_text(
            "readme_stale_threshold_days = 5\n"
        )
        monkeypatch.setenv("EVOLVE_README_STALE_THRESHOLD_DAYS", "30")
        ui = MagicMock()
        _emit_stale_readme_advisory(tmp_path, "SPEC.md", ui)
        ui.info.assert_not_called()

    def test_invalid_env_var_falls_back_to_default(self, tmp_path: Path, monkeypatch):
        """A non-integer env value is ignored; default applies."""
        _mk_project(tmp_path, readme_age_days=42.0, spec_age_days=0.0)
        monkeypatch.setenv("EVOLVE_README_STALE_THRESHOLD_DAYS", "not-an-int")
        ui = MagicMock()
        _emit_stale_readme_advisory(tmp_path, "SPEC.md", ui)
        # 42 > 30 default → advisory still fires.
        ui.info.assert_called_once()

    def test_alternate_spec_path(self, tmp_path: Path, monkeypatch):
        """Works with arbitrary spec filenames (not just SPEC.md)."""
        monkeypatch.delenv("EVOLVE_README_STALE_THRESHOLD_DAYS", raising=False)
        _mk_project(
            tmp_path,
            readme_age_days=100.0,
            spec_age_days=0.0,
            spec_name="CLAIMS.md",
        )
        ui = MagicMock()
        _emit_stale_readme_advisory(tmp_path, "CLAIMS.md", ui)
        ui.info.assert_called_once()


# ---------------------------------------------------------------------------
# Integration — the advisory actually fires inside evolve_loop / run_dry_run /
# run_validate at startup time.
# ---------------------------------------------------------------------------


class TestAdvisoryWiring:
    """The advisory is wired into evolve_loop, run_dry_run, run_validate."""

    def test_emit_helper_called_in_evolve_loop(self, tmp_path: Path, monkeypatch):
        """evolve_loop calls _emit_stale_readme_advisory at startup."""
        import evolve.orchestrator as loop_mod

        calls: list[tuple] = []

        def fake_emit(project_dir, spec, ui):
            calls.append((project_dir, spec))

        monkeypatch.setattr(loop_mod, "_emit_stale_readme_advisory", fake_emit)
        # Short-circuit the rest of evolve_loop so we only test the
        # advisory call at startup, not the full round loop.
        monkeypatch.setattr(loop_mod, "_ensure_git", lambda *a, **k: None)
        monkeypatch.setattr(loop_mod, "_run_rounds", lambda *a, **k: None)

        (tmp_path / "README.md").write_text("# R\n")
        (tmp_path / "SPEC.md").write_text("# S\n")

        loop_mod.evolve_loop(
            project_dir=tmp_path,
            max_rounds=1,
            check_cmd="true",
            spec="SPEC.md",
        )
        assert len(calls) == 1
        assert calls[0][0] == tmp_path
        assert calls[0][1] == "SPEC.md"

    def test_emit_helper_called_in_run_dry_run(self, tmp_path: Path, monkeypatch):
        """run_dry_run calls _emit_stale_readme_advisory at startup."""
        import evolve.orchestrator as loop_mod

        calls: list[tuple] = []
        monkeypatch.setattr(
            loop_mod,
            "_emit_stale_readme_advisory",
            lambda p, s, u: calls.append((p, s)),
        )
        # Short-circuit the rest of run_dry_run.
        import evolve.agent as agent_mod

        monkeypatch.setattr(agent_mod, "run_dry_run_agent", lambda *a, **k: None)

        (tmp_path / "README.md").write_text("# R\n")
        (tmp_path / "SPEC.md").write_text("# S\n")

        loop_mod.run_dry_run(project_dir=tmp_path, check_cmd=None, spec="SPEC.md")
        assert len(calls) == 1
        assert calls[0][1] == "SPEC.md"

    def test_emit_helper_called_in_run_validate(self, tmp_path: Path, monkeypatch):
        """run_validate calls _emit_stale_readme_advisory at startup."""
        import evolve.orchestrator as loop_mod

        calls: list[tuple] = []
        monkeypatch.setattr(
            loop_mod,
            "_emit_stale_readme_advisory",
            lambda p, s, u: calls.append((p, s)),
        )
        import evolve.agent as agent_mod

        monkeypatch.setattr(agent_mod, "run_validate_agent", lambda *a, **k: None)

        (tmp_path / "README.md").write_text("# R\n")
        (tmp_path / "SPEC.md").write_text("# S\n")

        # run_validate reads validate_report.md; create a stub so it doesn't
        # error when parsing missing output.
        loop_mod.run_validate(project_dir=tmp_path, check_cmd=None, spec="SPEC.md")
        assert len(calls) == 1
        assert calls[0][1] == "SPEC.md"
