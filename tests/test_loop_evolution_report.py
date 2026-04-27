"""Coverage tests split from test_loop_coverage.py:
TestEvolutionReportExtended, TestRunPartyModeExtended, TestForeverRestartInRunRounds.
"""

import subprocess
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from evolve.orchestrator import (
    _generate_evolution_report,
    _run_rounds,
)
from evolve.party import _run_party_mode


# ---------------------------------------------------------------------------
# _generate_evolution_report — additional edge cases
# ---------------------------------------------------------------------------

class TestEvolutionReportExtended:

    def _setup_report_project(self, tmp_path, improvements_text="- [x] done\n"):
        """Create project structure for report tests — shared setup."""
        project_dir = tmp_path / "proj"
        project_dir.mkdir()
        runs_dir = project_dir / "runs"
        runs_dir.mkdir()
        run_dir = runs_dir / "session"
        run_dir.mkdir()
        (runs_dir / "improvements.md").write_text(improvements_text)
        return project_dir, run_dir

    def test_report_with_git_log_match(self, tmp_path: Path):
        """Report uses git log commit messages when available."""
        project_dir, run_dir = self._setup_report_project(tmp_path)

        def mock_run(cmd, **kwargs):
            if "log" in cmd:
                return MagicMock(returncode=0, stdout="abc1234 fix(parser): handle empty input")
            return MagicMock(returncode=1, stdout="")

        with patch("evolve.orchestrator.subprocess.run", side_effect=mock_run):
            _generate_evolution_report(project_dir, run_dir, 10, 1, True)

        report = (run_dir / "evolution_report.md").read_text()
        assert "fix(parser): handle empty input" in report

    def test_report_with_failed_check_counts(self, tmp_path: Path):
        """Report shows failed count in check results with pass/fail format."""
        project_dir, run_dir = self._setup_report_project(tmp_path, "- [ ] pending\n")
        (run_dir / "check_round_1.txt").write_text(
            "Round 1: FAIL\n10 passed, 3 failed\n"
        )

        with patch("evolve.orchestrator.subprocess.run", return_value=MagicMock(returncode=1, stdout="")):
            _generate_evolution_report(project_dir, run_dir, 10, 1, False)

        report = (run_dir / "evolution_report.md").read_text()
        assert "10 passed" in report
        assert "3 failed" in report

    def test_report_truncates_many_files(self, tmp_path: Path):
        """Report truncates file list when >3 files."""
        project_dir, run_dir = self._setup_report_project(tmp_path)
        (run_dir / "conversation_loop_1.md").write_text(
            "Edit → a.py\nEdit → b.py\nEdit → c.py\nEdit → d.py\nEdit → e.py\n"
        )

        with patch("evolve.orchestrator.subprocess.run", return_value=MagicMock(returncode=1, stdout="")):
            _generate_evolution_report(project_dir, run_dir, 10, 1, True)

        report = (run_dir / "evolution_report.md").read_text()
        assert "(+" in report

    def test_report_git_timeout(self, tmp_path: Path):
        """Git log timeout is handled gracefully."""
        project_dir, run_dir = self._setup_report_project(tmp_path, "# Improvements\n")

        def mock_run(cmd, **kwargs):
            if "log" in cmd:
                raise subprocess.TimeoutExpired(cmd=cmd, timeout=10)
            return MagicMock(returncode=1, stdout="")

        with patch("evolve.orchestrator.subprocess.run", side_effect=mock_run):
            _generate_evolution_report(project_dir, run_dir, 10, 1, False)

        report = (run_dir / "evolution_report.md").read_text()
        assert "round 1" in report

    def test_report_missing_improvements_file(self, tmp_path: Path):
        """Report handles missing improvements.md gracefully (0 checked, 0 unchecked)."""
        project_dir = tmp_path / "proj"
        project_dir.mkdir()
        runs_dir = project_dir / "runs"
        runs_dir.mkdir()
        run_dir = runs_dir / "session"
        run_dir.mkdir()
        # No improvements.md file

        with patch("evolve.orchestrator.subprocess.run", return_value=MagicMock(returncode=1, stdout="")):
            _generate_evolution_report(project_dir, run_dir, 10, 1, True)

        report = (run_dir / "evolution_report.md").read_text()
        assert "0 improvements completed" in report
        assert "0 bugs fixed" in report
        # No "remaining" line when unchecked is 0
        assert "remaining" not in report

    def test_report_check_fail_no_passed_count(self, tmp_path: Path):
        """Check results with FAIL and no passed count shows FAIL."""
        project_dir, run_dir = self._setup_report_project(tmp_path)
        (run_dir / "check_round_1.txt").write_text("Round 1: FAIL\nERROR: compilation failed\n")

        with patch("evolve.orchestrator.subprocess.run", return_value=MagicMock(returncode=1, stdout="")):
            _generate_evolution_report(project_dir, run_dir, 10, 1, False)

        report = (run_dir / "evolution_report.md").read_text()
        assert "FAIL" in report

    def test_report_check_pass_no_count(self, tmp_path: Path):
        """Check results with PASS but no numeric count shows PASS."""
        project_dir, run_dir = self._setup_report_project(tmp_path)
        (run_dir / "check_round_1.txt").write_text("PASS\nAll good\n")

        with patch("evolve.orchestrator.subprocess.run", return_value=MagicMock(returncode=1, stdout="")):
            _generate_evolution_report(project_dir, run_dir, 10, 1, True)

        report = (run_dir / "evolution_report.md").read_text()
        assert "PASS" in report

    def test_report_multiple_rounds_mixed_actions(self, tmp_path: Path):
        """Report correctly counts bugs_fixed and improvements_done across mixed rounds."""
        project_dir, run_dir = self._setup_report_project(
            tmp_path, "- [x] a\n- [x] b\n- [x] c\n"
        )
        (run_dir / "conversation_loop_1.md").write_text("fix(cli): crash on startup\n")
        (run_dir / "conversation_loop_2.md").write_text("feat(api): add endpoint\n")
        (run_dir / "conversation_loop_3.md").write_text("refactor(core): simplify logic\n")

        with patch("evolve.orchestrator.subprocess.run", return_value=MagicMock(returncode=1, stdout="")):
            _generate_evolution_report(project_dir, run_dir, 10, 3, True)

        report = (run_dir / "evolution_report.md").read_text()
        assert "1 bugs fixed" in report
        # refactor doesn't count as fix or feat — only 1 of each
        assert "fix(cli): crash on startup" in report
        assert "feat(api): add endpoint" in report
        assert "refactor(core): simplify logic" in report

    def test_report_partial_round_data(self, tmp_path: Path):
        """Report handles rounds where only some have check files or conversation logs."""
        project_dir, run_dir = self._setup_report_project(tmp_path)
        # Round 1 has check but no conversation
        (run_dir / "check_round_1.txt").write_text("PASS\n10 passed\n")
        # Round 2 has conversation but no check
        (run_dir / "conversation_loop_2.md").write_text("feat(ui): add button\nEdit → ui.py\n")
        # Round 3 has neither

        with patch("evolve.orchestrator.subprocess.run", return_value=MagicMock(returncode=1, stdout="")):
            _generate_evolution_report(project_dir, run_dir, 10, 3, False)

        report = (run_dir / "evolution_report.md").read_text()
        assert "3/10" in report
        # All 3 rounds should appear in timeline
        assert "| 1 |" in report
        assert "| 2 |" in report
        assert "| 3 |" in report
        assert "10 passed" in report
        assert "ui.py" in report
        # Round 3 has no data — falls back to "round 3"
        assert "round 3" in report

    def test_report_project_and_session_name(self, tmp_path: Path):
        """Report header includes correct project and session names."""
        project_dir = tmp_path / "my-cool-project"
        project_dir.mkdir()
        runs_dir = project_dir / "runs"
        runs_dir.mkdir()
        run_dir = runs_dir / "20260325_120000"
        run_dir.mkdir()
        (runs_dir / "improvements.md").write_text("# Improvements\n")

        with patch("evolve.orchestrator.subprocess.run", return_value=MagicMock(returncode=1, stdout="")):
            _generate_evolution_report(project_dir, run_dir, 5, 0, False)

        report = (run_dir / "evolution_report.md").read_text()
        assert "**Project:** my-cool-project" in report
        assert "**Session:** 20260325_120000" in report

    def test_report_git_file_not_found(self, tmp_path: Path):
        """Git FileNotFoundError is handled gracefully (no git installed)."""
        project_dir, run_dir = self._setup_report_project(tmp_path)
        (run_dir / "conversation_loop_1.md").write_text("some text, no commit pattern\n")

        def mock_run(cmd, **kwargs):
            if "log" in cmd:
                raise FileNotFoundError("git not found")
            return MagicMock(returncode=1, stdout="")

        with patch("evolve.orchestrator.subprocess.run", side_effect=mock_run):
            _generate_evolution_report(project_dir, run_dir, 10, 1, True)

        report = (run_dir / "evolution_report.md").read_text()
        # Falls back to "round 1" since no commit pattern in convo
        assert "round 1" in report

    def test_report_empty_improvements(self, tmp_path: Path):
        """Report with empty improvements.md (no checkboxes) shows 0 counts."""
        project_dir, run_dir = self._setup_report_project(tmp_path, "# Improvements\n")

        with patch("evolve.orchestrator.subprocess.run", return_value=MagicMock(returncode=1, stdout="")):
            _generate_evolution_report(project_dir, run_dir, 10, 0, False)

        report = (run_dir / "evolution_report.md").read_text()
        assert "0 improvements completed" in report
        assert "0 bugs fixed" in report
        assert "0 files modified" in report
        assert "remaining" not in report

    def test_report_action_truncated_at_70_chars(self, tmp_path: Path):
        """Long commit messages are truncated to 70 characters in the timeline."""
        project_dir, run_dir = self._setup_report_project(tmp_path)
        long_msg = "feat(parser): " + "x" * 80
        (run_dir / "conversation_loop_1.md").write_text(long_msg + "\n")

        with patch("evolve.orchestrator.subprocess.run", return_value=MagicMock(returncode=1, stdout="")):
            _generate_evolution_report(project_dir, run_dir, 10, 1, True)

        report = (run_dir / "evolution_report.md").read_text()
        # Find the timeline row - action should be at most 70 chars
        for line in report.splitlines():
            if line.startswith("| 1 |"):
                # Extract action between first and second pipe after round
                parts = line.split("|")
                action = parts[2].strip()
                assert len(action) <= 70
                break


# ---------------------------------------------------------------------------
# _run_party_mode — workflow and agent loading paths
# ---------------------------------------------------------------------------

class TestRunPartyModeExtended:
    def test_empty_agents_dir(self, tmp_path: Path):
        """Party mode skips when agents dir has no .md files."""
        agents = tmp_path / "agents"
        agents.mkdir()

        run_dir = tmp_path / "runs" / "session"
        run_dir.mkdir(parents=True)

        ui = MagicMock()
        _run_party_mode(tmp_path, run_dir, ui)
        ui.warn.assert_called_with("No agent personas found — skipping party mode")

    def test_workflow_loading_sdk_missing(self, tmp_path: Path):
        """Party mode loads workflow but falls back when SDK missing."""
        agents = tmp_path / "agents"
        agents.mkdir()
        (agents / "dev.md").write_text("# Dev Agent")

        run_dir = tmp_path / "runs" / "session"
        run_dir.mkdir(parents=True)
        (tmp_path / "README.md").write_text("# Test")
        (tmp_path / "runs" / "improvements.md").write_text("- [x] done\n")
        (tmp_path / "runs" / "memory.md").write_text("# Memory\n")
        (run_dir / "CONVERGED").write_text("All done")

        ui = MagicMock()

        real_import = __import__

        def mock_import(name, *args, **kwargs):
            if name == "claude_agent_sdk":
                raise ImportError("mocked")
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=mock_import):
            _run_party_mode(tmp_path, run_dir, ui)

        ui.warn.assert_called()


# ---------------------------------------------------------------------------
# _forever_restart path in _run_rounds (lines 620-639)
# ---------------------------------------------------------------------------

class TestForeverRestartInRunRounds:
    """Test the forever-mode restart path inside _run_rounds."""

    def _setup_project(self, tmp_path):
        project_dir = tmp_path / "proj"
        project_dir.mkdir()
        run_dir = project_dir / "runs" / "session"
        run_dir.mkdir(parents=True)
        imp_path = project_dir / "runs" / "improvements.md"
        imp_path.write_text("- [ ] [functional] do something\n")
        (project_dir / "README.md").write_text("# Test")
        return project_dir, run_dir, imp_path

    def test_forever_restarts_after_convergence(self, tmp_path: Path):
        """When forever=True and CONVERGED, _forever_restart is called and loop restarts."""
        project_dir, run_dir, imp_path = self._setup_project(tmp_path)
        ui = MagicMock()

        call_count = 0

        def mock_monitored(cmd, cwd, ui_, round_num, watchdog_timeout=120):
            nonlocal call_count
            call_count += 1
            convo = Path(cwd) / "runs" / "session" / f"conversation_loop_{round_num}.md"
            # First call: converge
            if call_count == 1:
                convo.parent.mkdir(parents=True, exist_ok=True)
                convo.write_text("# Round 1")
                (run_dir / "CONVERGED").write_text("All done")
                imp_path.write_text("- [x] [functional] do something\n")
                return 0, "output", False
            # Second iteration (after restart): write conversation into the
            # new run_dir AND mark the next task done so the zero-progress
            # circuit breaker doesn't fire on three identical "imp unchanged"
            # attempts.
            for d in Path(cwd).glob("runs/*/"):
                if d.is_dir() and d.name != "session":
                    (d / f"conversation_loop_{round_num}.md").write_text("# Round after restart")
                    break
            imp_path.write_text("- [x] [functional] next task\n")
            return 0, "output", False

        # Real _forever_restart resets improvements.md so the next cycle has
        # unchecked backlog items to work on.  The mock must mimic that to
        # avoid the second iteration tripping the zero-progress circuit
        # breaker on the identical "all done" state.
        def mock_restart_side(*args, **kwargs):
            imp_path.write_text("- [ ] [functional] next task\n")

        with patch("evolve.orchestrator._run_monitored_subprocess", side_effect=mock_monitored), \
             patch("evolve.orchestrator._is_self_evolving", return_value=True), \
             patch("evolve.orchestrator._generate_evolution_report"), \
             patch("evolve.orchestrator._run_party_mode"), \
             patch("evolve.orchestrator._forever_restart", side_effect=mock_restart_side) as mock_restart, \
             patch("evolve.orchestrator._git_commit") as mock_commit, \
             pytest.raises(SystemExit) as exc:
            _run_rounds(
                project_dir, run_dir, imp_path, ui,
                start_round=1, max_rounds=1, check_cmd="pytest",
                allow_installs=False, timeout=300, model="claude-opus-4-6",
                forever=True,
            )

        # _forever_restart should have been called
        mock_restart.assert_called_once_with(project_dir, run_dir, imp_path, ui, spec=None)
        # _git_commit called for the forever restart
        mock_commit.assert_called_once()
        assert "forever mode" in mock_commit.call_args[0][1]
        # ui.run_dir_info called with new session dir
        ui.run_dir_info.assert_called_once()
        # Second iteration hits max rounds and exits with 1
        assert exc.value.code == 1

    def test_forever_creates_new_session_dir(self, tmp_path: Path):
        """Forever restart creates a new timestamped session directory."""
        project_dir, run_dir, imp_path = self._setup_project(tmp_path)
        ui = MagicMock()

        call_count = 0

        def mock_monitored(cmd, cwd, ui_, round_num, watchdog_timeout=120):
            nonlocal call_count
            call_count += 1
            convo = run_dir / f"conversation_loop_{round_num}.md"
            if call_count == 1:
                convo.write_text("# Round 1")
                (run_dir / "CONVERGED").write_text("All done")
                imp_path.write_text("- [x] [functional] do something\n")
                return 0, "output", False
            # On second call, create conversation in whatever run_dir was passed
            return 0, "output", False

        new_dirs_before = set(d.name for d in (project_dir / "runs").iterdir() if d.is_dir())

        with patch("evolve.orchestrator._run_monitored_subprocess", side_effect=mock_monitored), \
             patch("evolve.orchestrator._is_self_evolving", return_value=True), \
             patch("evolve.orchestrator._generate_evolution_report"), \
             patch("evolve.orchestrator._run_party_mode"), \
             patch("evolve.orchestrator._forever_restart"), \
             patch("evolve.orchestrator._git_commit"), \
             pytest.raises(SystemExit):
            _run_rounds(
                project_dir, run_dir, imp_path, ui,
                start_round=1, max_rounds=1, check_cmd=None,
                allow_installs=False, timeout=300, model="claude-opus-4-6",
                forever=True,
            )

        new_dirs_after = set(d.name for d in (project_dir / "runs").iterdir() if d.is_dir())
        new_session_dirs = new_dirs_after - new_dirs_before
        # A new timestamped session directory should have been created
        assert len(new_session_dirs) == 1
