"""Branch-coverage tests for loop.py targeting specific uncovered lines.

Each test class docstring lists the loop.py line range it exercises. These
are edge-case branches that other test suites don't reach: OSError handling,
subprocess error paths, deprecated aliases, and corrupted filename inputs.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import evolve.orchestrator as _orch
from evolve.git import _git_show_at
from evolve.orchestrator import (
    _auto_detect_check,
    _generate_evolution_report,
    _run_rounds,
    evolve_loop,
    run_dry_run,
    run_single_round,
    run_validate,
)
from evolve.state import _get_current_improvement


def _close_coro(coro):
    """asyncio.run side-effect that closes the coro to silence warnings."""
    coro.close()


# ---------------------------------------------------------------------------
# _auto_detect_check — Makefile read OSError (lines 71-72)
# ---------------------------------------------------------------------------

class TestAutoDetectMakefileOSError:
    def test_makefile_unreadable_returns_none(self, tmp_path: Path):
        """OSError while reading Makefile does NOT crash — returns None."""
        # Makefile present but raises OSError on read
        (tmp_path / "Makefile").write_text("test:\n\techo ok\n")

        with patch("shutil.which", side_effect=lambda name: "/usr/bin/make" if name == "make" else None), \
             patch("evolve.orchestrator.Path.read_text", side_effect=OSError("permission denied")):
            # Monkey-patch read_text just for Makefile path — simplest: patch on the method
            result = _auto_detect_check(tmp_path)

        # OSError swallowed — no pytest/npm/cargo/go available in test env, falls through to None
        assert result is None


# ---------------------------------------------------------------------------
# _git_show_at — subprocess error handling (lines 666-667)
# ---------------------------------------------------------------------------

class TestGitShowAtSubprocessError:
    def test_subprocess_error_returns_none(self, tmp_path: Path):
        """SubprocessError during git show returns None."""
        with patch(
            "evolve.orchestrator.subprocess.run",
            side_effect=subprocess.SubprocessError("boom"),
        ):
            assert _git_show_at(tmp_path, "HEAD", "runs/improvements.md") is None

    def test_filenotfound_returns_none(self, tmp_path: Path):
        """FileNotFoundError (git binary missing) returns None."""
        with patch(
            "evolve.orchestrator.subprocess.run",
            side_effect=FileNotFoundError("no git"),
        ):
            assert _git_show_at(tmp_path, "HEAD", "runs/improvements.md") is None

    def test_os_error_returns_none(self, tmp_path: Path):
        """Generic OSError returns None."""
        with patch("evolve.orchestrator.subprocess.run", side_effect=OSError("io error")):
            assert _git_show_at(tmp_path, "HEAD", "runs/improvements.md") is None


# ---------------------------------------------------------------------------
# _get_current_improvement — yolo deprecated alias (line 834)
# ---------------------------------------------------------------------------

class TestGetCurrentImprovementYoloAlias:
    def test_yolo_true_overrides_allow_installs(self, tmp_path: Path):
        """yolo=True is applied as allow_installs=True (line 833-834 branch)."""
        imp = tmp_path / "improvements.md"
        imp.write_text(
            "- [ ] [functional] [needs-package] install httpx\n"
            "- [ ] [functional] regular item\n"
        )
        # Without yolo alias: skips needs-package → returns regular item
        assert _get_current_improvement(imp, allow_installs=False) == "[functional] regular item"
        # With yolo=True (deprecated): takes needs-package item
        result = _get_current_improvement(imp, allow_installs=False, yolo=True)
        assert "[needs-package]" in result

    def test_yolo_false_explicit_keeps_allow_installs_false(self, tmp_path: Path):
        """yolo=False overrides allow_installs=True → needs-package skipped."""
        imp = tmp_path / "improvements.md"
        imp.write_text(
            "- [ ] [functional] [needs-package] install httpx\n"
            "- [ ] [functional] regular item\n"
        )
        # yolo=False wins over allow_installs=True, per the `if yolo is not None` guard
        result = _get_current_improvement(imp, allow_installs=True, yolo=False)
        assert result == "[functional] regular item"


# ---------------------------------------------------------------------------
# evolve_loop / run_single_round — yolo deprecated alias (lines 1072-1073, 1926-1927)
# ---------------------------------------------------------------------------

class TestYoloAliasEntryPoints:
    def test_evolve_loop_yolo_is_applied(self, tmp_path: Path):
        """evolve_loop's `if yolo is not None: allow_installs = yolo` branch fires."""
        (tmp_path / "README.md").write_text("# Test\n")
        (tmp_path / "runs").mkdir()

        # Patch _run_rounds so the loop returns immediately without running a
        # subprocess. Spy on the allow_installs argument it receives.
        captured = {}

        def _fake_run_rounds(*args, **kwargs):
            captured.update(kwargs)
            # Positional: project_dir, run_dir, improvements_path, ui, start_round,
            # max_rounds, check_cmd, allow_installs
            if len(args) >= 8:
                captured["allow_installs_positional"] = args[7]

        with patch("evolve.orchestrator._run_rounds", side_effect=_fake_run_rounds), \
             patch("evolve.orchestrator._ensure_git"), \
             patch("evolve.orchestrator.load_hooks", return_value={}):
            evolve_loop(
                tmp_path,
                max_rounds=1,
                check_cmd="pytest",
                allow_installs=False,
                yolo=True,  # deprecated alias — should win
            )

        # After the yolo branch, allow_installs is True.
        # Check positional 7th arg (index 7) OR kwarg
        assert captured.get("allow_installs_positional") is True

    def test_run_single_round_yolo_is_applied(self, tmp_path: Path):
        """run_single_round's `if yolo is not None: allow_installs = yolo` branch fires."""
        (tmp_path / "README.md").write_text("# Test\n")
        runs = tmp_path / "runs"
        runs.mkdir()
        run_dir = runs / "session"
        run_dir.mkdir()
        improvements = runs / "improvements.md"
        improvements.write_text(
            "- [ ] [functional] [needs-package] needs install\n"
        )

        # Spy on analyze_and_fix — it receives allow_installs.
        captured = {}

        def _fake_analyze(*args, **kwargs):
            captured["allow_installs"] = kwargs.get("allow_installs", args[2] if len(args) > 2 else None)

        with patch("evolve.orchestrator.subprocess.run", return_value=MagicMock(returncode=0, stdout="", stderr="")), \
             patch("evolve.agent.analyze_and_fix", side_effect=_fake_analyze), \
             patch.dict("sys.modules", {"claude_agent_sdk": MagicMock()}):
            run_single_round(
                tmp_path,
                round_num=1,
                check_cmd=None,
                allow_installs=False,
                run_dir=run_dir,
                yolo=True,  # deprecated alias
            )

        # yolo=True should have promoted allow_installs to True
        assert captured.get("allow_installs") is True


# ---------------------------------------------------------------------------
# evolve_loop resume — corrupted conversation log filenames
# (lines 1114-1116, 1123-1125)
# ---------------------------------------------------------------------------

class TestResumeCorruptedFilenames:
    def test_resume_with_non_numeric_convo_filename_survives(self, tmp_path: Path):
        """Resume sort-key gracefully handles a non-numeric conversation_loop_*.md."""
        (tmp_path / "README.md").write_text("# Test\n")
        runs = tmp_path / "runs"
        session = runs / "20260101_000000"
        session.mkdir(parents=True)
        # Mix: valid numeric + corrupted name. Sort key returns -1 for the bad one.
        (session / "conversation_loop_1.md").write_text("ok")
        (session / "conversation_loop_abc.md").write_text("corrupted")

        called = {}

        def _fake_run_rounds(*args, **kwargs):
            # Expect start_round == 2 (last good numeric round + 1)
            called["start_round"] = args[4] if len(args) > 4 else kwargs.get("start_round")

        with patch("evolve.orchestrator._run_rounds", side_effect=_fake_run_rounds), \
             patch("evolve.orchestrator._ensure_git"), \
             patch("evolve.orchestrator.load_hooks", return_value={}):
            evolve_loop(
                tmp_path,
                max_rounds=10,
                check_cmd="pytest",
                resume=True,
            )

        # last numeric round was 1 → start_round = 2
        assert called.get("start_round") == 2

    def test_resume_when_only_corrupted_convo_log(self, tmp_path: Path):
        """Resume with ONLY a corrupted convo log: start_round falls back to 1."""
        (tmp_path / "README.md").write_text("# Test\n")
        runs = tmp_path / "runs"
        session = runs / "20260101_000000"
        session.mkdir(parents=True)
        # Only a corrupted filename — the `int(last.rsplit("_", 1)[1])` raises.
        (session / "conversation_loop_abc.md").write_text("corrupted")

        called = {}

        def _fake_run_rounds(*args, **kwargs):
            called["start_round"] = args[4] if len(args) > 4 else kwargs.get("start_round")

        with patch("evolve.orchestrator._run_rounds", side_effect=_fake_run_rounds), \
             patch("evolve.orchestrator._ensure_git"), \
             patch("evolve.orchestrator.load_hooks", return_value={}):
            evolve_loop(
                tmp_path,
                max_rounds=10,
                check_cmd="pytest",
                resume=True,
            )

        # ValueError caught → start_round defaults to 1
        assert called.get("start_round") == 1


# ---------------------------------------------------------------------------
# run_dry_run / run_validate — check output with stderr (lines 2078, 2164)
# ---------------------------------------------------------------------------

class TestCheckOutputStderr:
    def test_dry_run_captures_stderr(self, tmp_path: Path):
        """run_dry_run appends stderr to check_output (line 2078)."""
        (tmp_path / "README.md").write_text("# Test\n")

        check_result = MagicMock(
            returncode=1,
            stdout="some stdout",
            stderr="ERROR: something broke",
        )

        captured_check = {}

        def _fake_dry_agent(*args, **kwargs):
            captured_check["check_output"] = kwargs.get("check_output", "")

        with patch("evolve.orchestrator.subprocess.run", return_value=check_result), \
             patch("evolve.agent.run_dry_run_agent", side_effect=_fake_dry_agent), \
             patch.dict("sys.modules", {"claude_agent_sdk": MagicMock()}):
            run_dry_run(tmp_path, check_cmd="pytest", timeout=10)

        assert "stderr:" in captured_check["check_output"]
        assert "something broke" in captured_check["check_output"]

    def test_validate_captures_stderr(self, tmp_path: Path):
        """run_validate appends stderr to check_output (line 2164)."""
        (tmp_path / "README.md").write_text("# Test\n")

        check_result = MagicMock(
            returncode=1,
            stdout="some stdout",
            stderr="ERROR: validation broke",
        )

        captured = {}

        def _fake_val_agent(*args, **kwargs):
            captured["check_output"] = kwargs.get("check_output", "")

        # run_validate_agent must set exit code — patch it to write pass report
        def _write_report(*args, **kwargs):
            rd = kwargs.get("run_dir")
            if rd is not None:
                (rd / "validate_report.md").write_text("All claims: PASS\n")
            _fake_val_agent(*args, **kwargs)

        with patch("evolve.orchestrator.subprocess.run", return_value=check_result), \
             patch("evolve.agent.run_validate_agent", side_effect=_write_report), \
             patch.dict("sys.modules", {"claude_agent_sdk": MagicMock()}):
            run_validate(tmp_path, check_cmd="pytest", timeout=10)

        assert "stderr:" in captured["check_output"]
        assert "validation broke" in captured["check_output"]


# ---------------------------------------------------------------------------
# _generate_evolution_report — visual timeline section (lines 1023-1034)
# ---------------------------------------------------------------------------

class TestGenerateEvolutionReportVisualTimeline:
    def test_visual_timeline_included_when_frames_exist(self, tmp_path: Path):
        """Visual timeline appears when capture_frames=True and frames/ has PNGs."""
        project_dir = tmp_path / "proj"
        project_dir.mkdir()
        runs_dir = project_dir / "runs"
        runs_dir.mkdir()
        run_dir = runs_dir / "20260101_120000"
        run_dir.mkdir()
        (runs_dir / "improvements.md").write_text("# Improvements\n- [x] done\n")

        # Create frames directory with PNG files
        frames_dir = run_dir / "frames"
        frames_dir.mkdir()
        (frames_dir / "round_1_end.png").write_bytes(b"\x89PNG\r\n\x1a\n")
        (frames_dir / "converged.png").write_bytes(b"\x89PNG\r\n\x1a\n")

        with patch("evolve.orchestrator.subprocess.run", return_value=MagicMock(returncode=1, stdout="")):
            _generate_evolution_report(
                project_dir, run_dir, max_rounds=10, final_round=1,
                converged=True, capture_frames=True,
            )

        report = (run_dir / "evolution_report.md").read_text()
        assert "## Visual timeline" in report
        assert "![Round 1 End](frames/round_1_end.png)" in report
        assert "![Converged](frames/converged.png)" in report

    def test_visual_timeline_skipped_when_frames_dir_missing(self, tmp_path: Path):
        """Visual timeline NOT emitted when capture_frames=True but no frames/."""
        project_dir = tmp_path / "proj"
        project_dir.mkdir()
        runs_dir = project_dir / "runs"
        runs_dir.mkdir()
        run_dir = runs_dir / "20260101_120000"
        run_dir.mkdir()
        (runs_dir / "improvements.md").write_text("# Improvements\n- [x] done\n")

        with patch("evolve.orchestrator.subprocess.run", return_value=MagicMock(returncode=1, stdout="")):
            _generate_evolution_report(
                project_dir, run_dir, max_rounds=10, final_round=1,
                converged=True, capture_frames=True,
            )

        report = (run_dir / "evolution_report.md").read_text()
        assert "## Visual timeline" not in report

    def test_visual_timeline_skipped_when_frames_dir_empty(self, tmp_path: Path):
        """Visual timeline NOT emitted when frames/ is empty (no .png)."""
        project_dir = tmp_path / "proj"
        project_dir.mkdir()
        runs_dir = project_dir / "runs"
        runs_dir.mkdir()
        run_dir = runs_dir / "20260101_120000"
        run_dir.mkdir()
        (runs_dir / "improvements.md").write_text("# Improvements\n- [x] done\n")
        (run_dir / "frames").mkdir()  # empty dir

        with patch("evolve.orchestrator.subprocess.run", return_value=MagicMock(returncode=1, stdout="")):
            _generate_evolution_report(
                project_dir, run_dir, max_rounds=10, final_round=1,
                converged=True, capture_frames=True,
            )

        report = (run_dir / "evolution_report.md").read_text()
        assert "## Visual timeline" not in report


# ---------------------------------------------------------------------------
# evolve_loop — hooks loaded print (line 1081)
# ---------------------------------------------------------------------------

class TestEvolveLoopHooksPrint:
    def test_hooks_loaded_print_fires(self, tmp_path: Path, capsys):
        """evolve_loop prints hook count when load_hooks returns non-empty."""
        (tmp_path / "README.md").write_text("# Test\n")
        (tmp_path / "runs").mkdir()

        fake_hooks = {"on_round_start": "echo start", "on_round_end": "echo end"}

        with patch("evolve.orchestrator.load_hooks", return_value=fake_hooks), \
             patch("evolve.orchestrator._run_rounds"), \
             patch("evolve.orchestrator._ensure_git"):
            evolve_loop(
                tmp_path,
                max_rounds=1,
                check_cmd="pytest",
            )

        captured = capsys.readouterr()
        assert "loaded 2 hook(s)" in captured.out
        # Both hook names appear in the probe message
        assert "on_round_start" in captured.out
        assert "on_round_end" in captured.out


# ---------------------------------------------------------------------------
# _run_rounds — check_file parsing + README audit
# at CONVERGED + CONVERGED rejected when spec is newer than improvements.md
# (covers lines 1538-1539, 1561-1564, 1691-1692, 1698-1699, 1736-1742,
# 1754-1755, 1646)
# ---------------------------------------------------------------------------

class TestRunRoundsIntegrationBranches:
    """Drive _run_rounds through specific branches not covered elsewhere."""

    def _setup(self, tmp_path: Path):
        project_dir = tmp_path / "proj"
        project_dir.mkdir()
        run_dir = project_dir / "runs" / "session"
        run_dir.mkdir(parents=True)
        imp_path = project_dir / "runs" / "improvements.md"
        imp_path.write_text("- [ ] [functional] do something\n")
        return project_dir, run_dir, imp_path

    def test_check_file_parsing_branch(self, tmp_path: Path):
        """check_round_N.txt is read and parsed in _run_rounds (1691-1692)."""
        project_dir, run_dir, imp_path = self._setup(tmp_path)
        ui = MagicMock()

        def mock_monitored(cmd, cwd, ui_, round_num, watchdog_timeout=120):
            convo = run_dir / f"conversation_loop_{round_num}.md"
            convo.write_text("# Round")
            imp_path.write_text("- [x] [functional] do something\n")
            # Seed the check_round_N.txt file so the parsing branch fires
            (run_dir / f"check_round_{round_num}.txt").write_text(
                "Round 1 post-fix check: PASS\n42 passed\n"
            )
            (run_dir / "CONVERGED").write_text("Done")
            return 0, "out", False

        with patch("evolve.orchestrator._run_monitored_subprocess", side_effect=mock_monitored), \
             patch("evolve.orchestrator._generate_evolution_report"), \
             patch("evolve.orchestrator._run_party_mode"), \
             pytest.raises(SystemExit):
            _orch._run_rounds(
                project_dir, run_dir, imp_path, ui,
                start_round=1, max_rounds=1, check_cmd="pytest",
                allow_installs=False, timeout=300, model="claude-opus-4-6",
            )
        # state.json should have been written with tests=42
        state = (run_dir / "state.json").read_text()
        assert "42" in state

    def test_git_log_timeout_gracefully_handled(self, tmp_path: Path):
        """TimeoutExpired in commit-msg check (1538-1539) swallowed."""
        project_dir, run_dir, imp_path = self._setup(tmp_path)
        ui = MagicMock()

        def mock_monitored(cmd, cwd, ui_, round_num, watchdog_timeout=120):
            convo = run_dir / f"conversation_loop_{round_num}.md"
            convo.write_text("# Round")
            imp_path.write_text("- [x] [functional] do something\n")
            (run_dir / "CONVERGED").write_text("Done")
            return 0, "out", False

        real_run = subprocess.run

        def flaky_run(cmd, *args, **kwargs):
            # Specifically raise on the "git log -1 --format=%s" call
            if isinstance(cmd, list) and "log" in cmd and "--format=%s" in cmd:
                raise subprocess.TimeoutExpired(cmd=cmd, timeout=10)
            return real_run(cmd, *args, **kwargs)

        with patch("evolve.orchestrator._run_monitored_subprocess", side_effect=mock_monitored), \
             patch("evolve.orchestrator._generate_evolution_report"), \
             patch("evolve.orchestrator._run_party_mode"), \
             patch("evolve.orchestrator.subprocess.run", side_effect=flaky_run), \
             pytest.raises(SystemExit):
            _orch._run_rounds(
                project_dir, run_dir, imp_path, ui,
                start_round=1, max_rounds=1, check_cmd="pytest",
                allow_installs=False, timeout=300, model="claude-opus-4-6",
            )
        # Got through without crashing
        assert (run_dir / "state.json").is_file()

    def test_memory_wipe_commit_body_timeout(self, tmp_path: Path):
        """TimeoutExpired reading commit body (1561-1564) swallowed → wipe assumed."""
        project_dir, run_dir, imp_path = self._setup(tmp_path)
        ui = MagicMock()

        # Pre-seed memory.md with enough content that a <half shrink is a wipe
        mem = project_dir / "runs" / "memory.md"
        mem.write_text("x" * 1000)

        def mock_monitored(cmd, cwd, ui_, round_num, watchdog_timeout=120):
            convo = run_dir / f"conversation_loop_{round_num}.md"
            convo.write_text("# Round")
            # Shrink memory.md to trigger wipe detection
            mem.write_text("x" * 100)
            imp_path.write_text("- [x] [functional] do something\n")
            (run_dir / "CONVERGED").write_text("Done")
            return 0, "out", False

        real_run = subprocess.run

        def flaky_run(cmd, *args, **kwargs):
            if isinstance(cmd, list) and "log" in cmd and "--format=%B" in cmd:
                raise subprocess.TimeoutExpired(cmd=cmd, timeout=10)
            return real_run(cmd, *args, **kwargs)

        with patch("evolve.orchestrator._run_monitored_subprocess", side_effect=mock_monitored), \
             patch("evolve.orchestrator._generate_evolution_report"), \
             patch("evolve.orchestrator._run_party_mode"), \
             patch("evolve.orchestrator._save_subprocess_diagnostic"), \
             patch("evolve.orchestrator.subprocess.run", side_effect=flaky_run), \
             pytest.raises(SystemExit):
            _orch._run_rounds(
                project_dir, run_dir, imp_path, ui,
                start_round=1, max_rounds=1, check_cmd="pytest",
                allow_installs=False, timeout=300, model="claude-opus-4-6",
            )

    def test_converged_rejected_when_spec_newer(self, tmp_path: Path):
        """CONVERGED is unlinked when spec mtime > improvements mtime (1754-1755)."""
        project_dir, run_dir, imp_path = self._setup(tmp_path)
        ui = MagicMock()

        spec_path = project_dir / "SPEC.md"
        spec_path.write_text("# Spec\n")

        def mock_monitored(cmd, cwd, ui_, round_num, watchdog_timeout=120):
            convo = run_dir / f"conversation_loop_{round_num}.md"
            convo.write_text("# Round")
            # Mark item done and write CONVERGED
            imp_path.write_text("- [x] [functional] done\n")
            (run_dir / "CONVERGED").write_text("Done")
            # Now make the spec NEWER than improvements.md. Set an older mtime
            # on improvements.md and a newer mtime on SPEC.md.
            import os, time as _time
            old = _time.time() - 1000
            new = _time.time()
            os.utime(imp_path, (old, old))
            os.utime(spec_path, (new, new))
            return 0, "out", False

        # When spec is newer, _check_spec_freshness marks items stale —
        # which means "add a next item". So the loop will continue past the
        # rejected CONVERGED. Use max_rounds=1 so it exits at max via SystemExit.
        with patch("evolve.orchestrator._run_monitored_subprocess", side_effect=mock_monitored), \
             patch("evolve.orchestrator._generate_evolution_report"), \
             patch("evolve.orchestrator._run_party_mode"), \
             pytest.raises(SystemExit):
            _orch._run_rounds(
                project_dir, run_dir, imp_path, ui,
                start_round=1, max_rounds=1, check_cmd="pytest",
                allow_installs=False, timeout=300, model="claude-opus-4-6",
                spec="SPEC.md",
            )

    def test_no_progress_diagnostic_written(self, tmp_path: Path):
        """No-progress round (line 1646) triggers _save_subprocess_diagnostic."""
        project_dir, run_dir, imp_path = self._setup(tmp_path)
        ui = MagicMock()

        def mock_monitored(cmd, cwd, ui_, round_num, watchdog_timeout=120):
            # Don't touch improvements.md, don't write COMMIT_MSG — zero progress
            convo = run_dir / f"conversation_loop_{round_num}.md"
            convo.write_text("# Round")
            return 0, "out", False

        save_calls = []

        def spy_save(*args, **kwargs):
            save_calls.append(kwargs.get("reason", ""))

        with patch("evolve.orchestrator._run_monitored_subprocess", side_effect=mock_monitored), \
             patch("evolve.orchestrator._save_subprocess_diagnostic", side_effect=spy_save), \
             patch("evolve.orchestrator._generate_evolution_report"), \
             pytest.raises(SystemExit):
            _orch._run_rounds(
                project_dir, run_dir, imp_path, ui,
                start_round=1, max_rounds=1, check_cmd="pytest",
                allow_installs=False, timeout=300, model="claude-opus-4-6",
            )

        # At least one save call with the "no progress" reason
        assert any("no progress" in r.lower() for r in save_calls)

    def test_spec_freshness_gate_prints_when_stale(self, tmp_path: Path, capsys):
        """Line 1431 prints when spec is newer than improvements.md."""
        project_dir, run_dir, imp_path = self._setup(tmp_path)
        ui = MagicMock()

        spec_path = project_dir / "SPEC.md"
        spec_path.write_text("# Spec\n")

        # spec newer than improvements.md from the very first round
        import os, time as _time
        old = _time.time() - 10000
        new = _time.time()
        os.utime(imp_path, (old, old))
        os.utime(spec_path, (new, new))

        def mock_monitored(cmd, cwd, ui_, round_num, watchdog_timeout=120):
            convo = run_dir / f"conversation_loop_{round_num}.md"
            convo.write_text("# Round")
            imp_path.write_text("- [x] [functional] done\n")
            (run_dir / "CONVERGED").write_text("Done")
            # Keep spec newer than improvements.md
            os.utime(imp_path, (old, old))
            return 0, "out", False

        with patch("evolve.orchestrator._run_monitored_subprocess", side_effect=mock_monitored), \
             patch("evolve.orchestrator._generate_evolution_report"), \
             patch("evolve.orchestrator._run_party_mode"), \
             pytest.raises(SystemExit):
            _orch._run_rounds(
                project_dir, run_dir, imp_path, ui,
                start_round=1, max_rounds=1, check_cmd="pytest",
                allow_installs=False, timeout=300, model="claude-opus-4-6",
                spec="SPEC.md",
            )

        captured = capsys.readouterr()
        assert "spec freshness gate" in captured.out

