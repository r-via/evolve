"""Tests for `evolve update` subcommand (US-046).

Every subprocess call is mocked via ``patch("evolve.updater._run", ...)``
— no live ``pip``, no live ``git``, no network access.  The single real
subprocess test (``test_update_help_exit_zero``) spawns
``python -m evolve update --help`` to verify the subparser is registered;
that follows the existing precedent in ``tests/test_entry_point_integrity.py``.
"""

import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from evolve.updater import (
    _ACTIVE_STATUSES,
    _default_ref,
    _detect_active_session,
    _detect_install_location,
    _git_can_fast_forward,
    _git_dirty,
    run_update,
)


def _fake_cp(stdout: str = "", stderr: str = "", returncode: int = 0):
    """Build a CompletedProcess-shaped MagicMock."""
    cp = MagicMock()
    cp.stdout = stdout
    cp.stderr = stderr
    cp.returncode = returncode
    return cp


# ---------------------------------------------------------------------------
# _detect_install_location
# ---------------------------------------------------------------------------


class TestDetectInstallLocation:
    def test_editable_parsed(self):
        out = (
            "Name: evolve\n"
            "Version: 0.1.0\n"
            "Location: /tmp/site-packages\n"
            "Editable project location: /home/me/evolve\n"
        )
        with patch("evolve.updater._run", return_value=_fake_cp(stdout=out)):
            loc, editable = _detect_install_location()
        assert editable is True
        assert loc == Path("/home/me/evolve")

    def test_non_editable(self):
        out = "Name: evolve\nVersion: 0.1.0\nLocation: /tmp/site-packages\n"
        with patch("evolve.updater._run", return_value=_fake_cp(stdout=out)):
            loc, editable = _detect_install_location()
        assert editable is False
        assert loc == Path("/tmp/site-packages")

    def test_pip_show_failure(self):
        with patch(
            "evolve.updater._run",
            return_value=_fake_cp(returncode=1, stderr="evolve not installed"),
        ):
            loc, editable = _detect_install_location()
        assert loc is None
        assert editable is False

# ---------------------------------------------------------------------------
# _detect_active_session
# ---------------------------------------------------------------------------


class TestDetectActiveSession:
    def test_no_runs_dir(self, tmp_path: Path):
        assert _detect_active_session(tmp_path) is None

    def test_running_status_blocks(self, tmp_path: Path):
        runs = tmp_path / ".evolve" / "runs" / "20260101_000000"
        runs.mkdir(parents=True)
        (runs / "state.json").write_text(json.dumps({"status": "running"}))
        result = _detect_active_session(tmp_path)
        assert result is not None
        assert result.parent.name == "20260101_000000"

    def test_converged_status_ignored(self, tmp_path: Path):
        runs = tmp_path / ".evolve" / "runs" / "20260101_000000"
        runs.mkdir(parents=True)
        (runs / "state.json").write_text(json.dumps({"status": "converged"}))
        assert _detect_active_session(tmp_path) is None

    def test_error_status_ignored(self, tmp_path: Path):
        runs = tmp_path / ".evolve" / "runs" / "20260101_000000"
        runs.mkdir(parents=True)
        (runs / "state.json").write_text(json.dumps({"status": "error"}))
        assert _detect_active_session(tmp_path) is None

    def test_malformed_json_skipped(self, tmp_path: Path):
        runs = tmp_path / ".evolve" / "runs" / "20260101_000000"
        runs.mkdir(parents=True)
        (runs / "state.json").write_text("not json {")
        assert _detect_active_session(tmp_path) is None
        assert "running" in _ACTIVE_STATUSES


# ---------------------------------------------------------------------------
# _git_dirty
# ---------------------------------------------------------------------------


class TestGitDirty:
    def test_clean_tree(self, tmp_path: Path):
        with patch("evolve.updater._run", return_value=_fake_cp(stdout="")):
            assert _git_dirty(tmp_path) is False

    def test_dirty_source_blocks(self, tmp_path: Path):
        with patch(
            "evolve.updater._run",
            return_value=_fake_cp(stdout=" M evolve/cli.py\n"),
        ):
            assert _git_dirty(tmp_path) is True

    def test_only_evolve_dir_ignored(self, tmp_path: Path):
        # Per SPEC archive 019 — `.evolve/` run artifacts don't count.
        out = "?? .evolve/runs/20260101_000000/state.json\n"
        with patch("evolve.updater._run", return_value=_fake_cp(stdout=out)):
            assert _git_dirty(tmp_path) is False

    def test_mixed_only_real_paths_block(self, tmp_path: Path):
        out = (
            "?? .evolve/runs/foo\n"
            " M evolve/agent.py\n"
        )
        with patch("evolve.updater._run", return_value=_fake_cp(stdout=out)):
            assert _git_dirty(tmp_path) is True

    def test_git_failure_treated_clean(self, tmp_path: Path):
        with patch(
            "evolve.updater._run",
            return_value=_fake_cp(returncode=128, stderr="not a git repo"),
        ):
            assert _git_dirty(tmp_path) is False


# ---------------------------------------------------------------------------
# _default_ref
# ---------------------------------------------------------------------------


class TestDefaultRef:
    def test_origin_head_resolved(self, tmp_path: Path):
        with patch(
            "evolve.updater._run",
            return_value=_fake_cp(stdout="refs/remotes/origin/main\n"),
        ):
            assert _default_ref(tmp_path) == "main"

    def test_fallback_when_symbolic_ref_fails(self, tmp_path: Path):
        with patch("evolve.updater._run", return_value=_fake_cp(returncode=1)):
            assert _default_ref(tmp_path) == "main"


# ---------------------------------------------------------------------------
# _git_can_fast_forward
# ---------------------------------------------------------------------------


class TestGitCanFastForward:
    def test_already_up_to_date(self, tmp_path: Path):
        def fake(cmd, cwd=None):
            if cmd[:2] == ["git", "fetch"]:
                return _fake_cp()
            if "rev-parse" in cmd:
                # Both HEAD and origin/main point to same SHA.
                return _fake_cp(stdout="abc123\n")
            return _fake_cp()

        with patch("evolve.updater._run", side_effect=fake):
            ok, info = _git_can_fast_forward(tmp_path, "main")
        assert ok is True
        assert info == "already up-to-date"

    def test_can_fast_forward(self, tmp_path: Path):
        def fake(cmd, cwd=None):
            if cmd[:2] == ["git", "fetch"]:
                return _fake_cp()
            if cmd[:2] == ["git", "rev-parse"] and cmd[2] == "HEAD":
                return _fake_cp(stdout="aaa111\n")
            if cmd[:2] == ["git", "rev-parse"]:
                return _fake_cp(stdout="bbb222\n")
            if "merge-base" in cmd:
                return _fake_cp(returncode=0)
            return _fake_cp()

        with patch("evolve.updater._run", side_effect=fake):
            ok, info = _git_can_fast_forward(tmp_path, "main")
        assert ok is True
        assert info == "bbb222"

    def test_non_fast_forward_refused(self, tmp_path: Path):
        def fake(cmd, cwd=None):
            if cmd[:2] == ["git", "fetch"]:
                return _fake_cp()
            if cmd[:2] == ["git", "rev-parse"] and cmd[2] == "HEAD":
                return _fake_cp(stdout="aaa111\n")
            if cmd[:2] == ["git", "rev-parse"]:
                return _fake_cp(stdout="bbb222\n")
            if "merge-base" in cmd:
                return _fake_cp(returncode=1)  # not an ancestor
            return _fake_cp()

        with patch("evolve.updater._run", side_effect=fake):
            ok, info = _git_can_fast_forward(tmp_path, "main")
        assert ok is False
        assert "non-fast-forward" in info

    def test_fetch_failure(self, tmp_path: Path):
        def fake(cmd, cwd=None):
            if cmd[:2] == ["git", "fetch"]:
                return _fake_cp(returncode=128, stderr="network down")
            return _fake_cp()

        with patch("evolve.updater._run", side_effect=fake):
            ok, info = _git_can_fast_forward(tmp_path, "main")
        assert ok is False
        assert "fetch failed" in info

# ---------------------------------------------------------------------------
# run_update — editable install
# ---------------------------------------------------------------------------


def _editable_pip_show(repo_dir: Path) -> str:
    return (
        f"Name: evolve\n"
        f"Version: 0.1.0\n"
        f"Location: /tmp/site-packages\n"
        f"Editable project location: {repo_dir}\n"
    )


def _non_editable_pip_show(loc: Path) -> str:
    return f"Name: evolve\nVersion: 0.1.0\nLocation: {loc}\n"


class TestRunUpdateEditable:
    def test_happy_path(self, tmp_path: Path, capsys):
        def fake(cmd, cwd=None):
            if "pip" in cmd and "show" in cmd:
                return _fake_cp(stdout=_editable_pip_show(tmp_path))
            if cmd[:2] == ["git", "status"]:
                return _fake_cp(stdout="")
            if "symbolic-ref" in cmd:
                return _fake_cp(stdout="refs/remotes/origin/main\n")
            if cmd[:2] == ["git", "fetch"]:
                return _fake_cp()
            if cmd[:2] == ["git", "rev-parse"] and cmd[2] == "HEAD":
                return _fake_cp(stdout="oldsha111\n")
            if cmd[:2] == ["git", "rev-parse"]:
                return _fake_cp(stdout="newsha222333\n")
            if "merge-base" in cmd:
                return _fake_cp(returncode=0)
            if cmd[:2] == ["git", "merge"]:
                return _fake_cp()
            return _fake_cp()

        with patch("evolve.updater._run", side_effect=fake):
            rc = run_update(dry_run=False, ref=None)
        assert rc == 0
        out = capsys.readouterr().out
        assert "editable" in out
        assert "updated to" in out

    def test_dirty_tree_refused(self, tmp_path: Path, capsys):
        def fake(cmd, cwd=None):
            if "pip" in cmd and "show" in cmd:
                return _fake_cp(stdout=_editable_pip_show(tmp_path))
            if cmd[:2] == ["git", "status"]:
                return _fake_cp(stdout=" M evolve/cli.py\n")
            return _fake_cp()

        with patch("evolve.updater._run", side_effect=fake):
            rc = run_update(dry_run=False, ref=None)
        assert rc == 1
        err = capsys.readouterr().err
        assert "BLOCKED" in err
        assert "dirty" in err

    def test_non_ff_refused(self, tmp_path: Path, capsys):
        def fake(cmd, cwd=None):
            if "pip" in cmd and "show" in cmd:
                return _fake_cp(stdout=_editable_pip_show(tmp_path))
            if cmd[:2] == ["git", "status"]:
                return _fake_cp(stdout="")
            if "symbolic-ref" in cmd:
                return _fake_cp(stdout="refs/remotes/origin/main\n")
            if cmd[:2] == ["git", "fetch"]:
                return _fake_cp()
            if cmd[:2] == ["git", "rev-parse"] and cmd[2] == "HEAD":
                return _fake_cp(stdout="aaa\n")
            if cmd[:2] == ["git", "rev-parse"]:
                return _fake_cp(stdout="bbb\n")
            if "merge-base" in cmd:
                return _fake_cp(returncode=1)
            return _fake_cp()

        with patch("evolve.updater._run", side_effect=fake):
            rc = run_update(dry_run=False, ref=None)
        assert rc == 1
        err = capsys.readouterr().err
        assert "non-fast-forward" in err

    def test_dry_run_emits_planned_commands(self, tmp_path: Path, capsys):
        def fake(cmd, cwd=None):
            if "pip" in cmd and "show" in cmd:
                return _fake_cp(stdout=_editable_pip_show(tmp_path))
            if cmd[:2] == ["git", "status"]:
                return _fake_cp(stdout="")
            if "symbolic-ref" in cmd:
                return _fake_cp(stdout="refs/remotes/origin/main\n")
            return _fake_cp()

        with patch("evolve.updater._run", side_effect=fake):
            rc = run_update(dry_run=True, ref=None)
        assert rc == 0
        out = capsys.readouterr().out
        assert "[dry-run]" in out
        assert "fetch" in out
        assert "merge --ff-only" in out

    def test_explicit_ref_used(self, tmp_path: Path, capsys):
        captured: list[list[str]] = []

        def fake(cmd, cwd=None):
            captured.append(list(cmd))
            if "pip" in cmd and "show" in cmd:
                return _fake_cp(stdout=_editable_pip_show(tmp_path))
            if cmd[:2] == ["git", "status"]:
                return _fake_cp(stdout="")
            return _fake_cp()

        with patch("evolve.updater._run", side_effect=fake):
            rc = run_update(dry_run=True, ref="release-1.2")
        assert rc == 0
        out = capsys.readouterr().out
        # The explicit ref must appear in the dry-run plan, not 'main'.
        assert "release-1.2" in out

# ---------------------------------------------------------------------------
# run_update — non-editable install
# ---------------------------------------------------------------------------


class TestRunUpdateNonEditable:
    def test_pip_upgrade_happy_path(self, tmp_path: Path, capsys):
        def fake(cmd, cwd=None):
            if "show" in cmd and "evolve" in cmd:
                return _fake_cp(stdout=_non_editable_pip_show(tmp_path))
            if "install" in cmd and "--upgrade" in cmd:
                return _fake_cp()
            return _fake_cp()

        with patch("evolve.updater._run", side_effect=fake):
            rc = run_update(dry_run=False, ref=None)
        assert rc == 0
        assert "pip upgrade complete" in capsys.readouterr().out

    def test_pip_upgrade_failure(self, tmp_path: Path, capsys):
        def fake(cmd, cwd=None):
            if "show" in cmd and "evolve" in cmd:
                return _fake_cp(stdout=_non_editable_pip_show(tmp_path))
            if "install" in cmd:
                return _fake_cp(returncode=1, stderr="boom")
            return _fake_cp()

        with patch("evolve.updater._run", side_effect=fake):
            rc = run_update(dry_run=False, ref=None)
        assert rc == 2
        err = capsys.readouterr().err
        assert "ERROR" in err

    def test_dry_run_shows_pip_command(self, tmp_path: Path, capsys):
        def fake(cmd, cwd=None):
            if "show" in cmd and "evolve" in cmd:
                return _fake_cp(stdout=_non_editable_pip_show(tmp_path))
            return _fake_cp()

        with patch("evolve.updater._run", side_effect=fake):
            rc = run_update(dry_run=True, ref=None)
        assert rc == 0
        out = capsys.readouterr().out
        assert "[dry-run]" in out
        assert "pip install --upgrade evolve" in out

    def test_ref_warning_for_non_editable(self, tmp_path: Path, capsys):
        def fake(cmd, cwd=None):
            if "show" in cmd and "evolve" in cmd:
                return _fake_cp(stdout=_non_editable_pip_show(tmp_path))
            return _fake_cp()

        with patch("evolve.updater._run", side_effect=fake):
            run_update(dry_run=True, ref="v1.2.3")
        err = capsys.readouterr().err
        assert "--ref is honored only for editable" in err


# ---------------------------------------------------------------------------
# run_update — error paths
# ---------------------------------------------------------------------------


class TestRunUpdateErrorPaths:
    def test_no_install_detected_returns_two(self, capsys):
        with patch(
            "evolve.updater._run",
            return_value=_fake_cp(returncode=1, stderr="not installed"),
        ):
            rc = run_update(dry_run=False, ref=None)
        assert rc == 2
        err = capsys.readouterr().err
        assert "ERROR" in err
        assert "install location" in err

    def test_active_session_blocks(self, tmp_path: Path, capsys):
        runs = tmp_path / ".evolve" / "runs" / "20260101_000000"
        runs.mkdir(parents=True)
        (runs / "state.json").write_text(json.dumps({"status": "running"}))

        def fake(cmd, cwd=None):
            if "show" in cmd and "evolve" in cmd:
                return _fake_cp(stdout=_editable_pip_show(tmp_path))
            return _fake_cp()

        with patch("evolve.updater._run", side_effect=fake):
            rc = run_update(dry_run=False, ref=None)
        assert rc == 1
        err = capsys.readouterr().err
        assert "BLOCKED" in err
        assert "active evolve session" in err


# ---------------------------------------------------------------------------
# CLI integration — `python -m evolve update --help`
# ---------------------------------------------------------------------------


class TestUpdateCLIHelp:
    """Verifies the subparser is registered in evolve/cli.py.

    Spawns a real subprocess (precedent: tests/test_entry_point_integrity.py).
    Bounded by timeout=10 to stay well under the 20s pytest ceiling.
    """

    def test_update_help_exit_zero(self):
        result = subprocess.run(
            [sys.executable, "-m", "evolve", "update", "--help"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 0
        out = result.stdout.lower()
        assert "update" in out
        assert "--dry-run" in out
        assert "--ref" in out


# ---------------------------------------------------------------------------
# File-size invariant — SPEC § "Hard rule: source files MUST NOT exceed 500 lines"
# ---------------------------------------------------------------------------


def test_updater_module_under_500_lines():
    src = Path(__file__).resolve().parent.parent / "evolve" / "updater.py"
    n = sum(1 for _ in src.read_text().splitlines())
    assert n <= 500, f"evolve/updater.py is {n} lines, must be ≤500"


def test_test_updater_under_500_lines():
    n = sum(1 for _ in Path(__file__).read_text().splitlines())
    assert n <= 500, f"tests/test_updater.py is {n} lines, must be ≤500"
