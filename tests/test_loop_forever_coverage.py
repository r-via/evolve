"""Coverage tests for evolve_loop forever-mode integration.

Extracted from test_loop_coverage.py to keep modules under the 500-line cap.
Covers:
- TestEvolveLoopForeverIntegration — full evolve_loop with forever=True
- TestEvolveLoopResumeForeverCombined — evolve_loop with resume=True + forever=True
"""

from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from evolve.application.run_loop import MAX_DEBUG_RETRIES
from evolve.application.run_loop_startup import evolve_loop


class TestEvolveLoopForeverIntegration:
    """End-to-end integration tests for evolve_loop with forever=True.

    These tests exercise the full flow through evolve_loop (not just _run_rounds):
    branch creation, convergence detection, party mode invocation, README_proposal
    adoption, improvements reset, and loop restart.

    Since forever mode sets max_rounds=999999, tests use a side_effect that raises
    SystemExit after the desired number of subprocess calls to avoid infinite loops.
    """

    def _setup_project(self, tmp_path: Path):
        """Create a minimal project directory with README and improvements."""
        project_dir = tmp_path / "proj"
        project_dir.mkdir()
        (project_dir / "README.md").write_text("# My Project\n")
        runs_dir = project_dir / "runs"
        runs_dir.mkdir()
        imp_path = runs_dir / "improvements.md"
        imp_path.write_text("- [ ] [functional] initial improvement\n")
        return project_dir, imp_path

    @staticmethod
    def _extract_run_dir(cmd):
        """Extract --run-dir value from subprocess command args."""
        for i, arg in enumerate(cmd):
            if arg == "--run-dir" and i + 1 < len(cmd):
                return Path(cmd[i + 1])
        return None

    def test_forever_creates_branch_then_converges_restarts_and_exits(self, tmp_path: Path):
        """Full forever flow: branch → converge → party → restart → exit."""
        project_dir, imp_path = self._setup_project(tmp_path)

        call_count = 0

        def mock_monitored(cmd, cwd, ui_, round_num, watchdog_timeout=120):
            nonlocal call_count
            call_count += 1
            run_dir = self._extract_run_dir(cmd)
            if run_dir:
                run_dir.mkdir(parents=True, exist_ok=True)

            if call_count == 1:
                (run_dir / f"conversation_loop_{round_num}.md").write_text("# Round 1 converged")
                (run_dir / "CONVERGED").write_text("All README claims verified")
                imp_path.write_text("- [x] [functional] initial improvement\n")
                return 0, "converged output", False
            else:
                raise SystemExit(42)

        with patch("evolve.application.run_loop._setup_forever_branch") as mock_branch, \
             patch("evolve.application.run_loop._ensure_git"), \
             patch("evolve.application.run_loop._ensure_runs_layout"), \
             patch("evolve.application.run_loop._run_monitored_subprocess", side_effect=mock_monitored), \
             patch("evolve.infrastructure.reporting.generator._generate_evolution_report"), \
             patch("evolve.application.run_loop._run_party_mode") as mock_party, \
             patch("evolve.application.run_loop._forever_restart") as mock_restart, \
             patch("evolve.application.run_loop._git_commit") as mock_commit, \
             pytest.raises(SystemExit) as exc:
            evolve_loop(project_dir, max_rounds=1, forever=True)

        mock_branch.assert_called_once_with(project_dir)
        mock_party.assert_called_once()
        party_args = mock_party.call_args[0]
        assert party_args[0] == project_dir

        mock_restart.assert_called_once()
        restart_args = mock_restart.call_args[0]
        assert restart_args[0] == project_dir
        assert restart_args[2] == imp_path

        mock_commit.assert_called_once()
        assert "forever mode" in mock_commit.call_args[0][1]

        assert exc.value.code == 42

    def test_forever_readme_proposal_adoption_end_to_end(self, tmp_path: Path):
        """Verify that _forever_restart actually adopts README_proposal.md content."""
        project_dir, imp_path = self._setup_project(tmp_path)
        proposed_readme = "# My Evolved Project\n\nNew features here.\n"

        call_count = 0

        def mock_monitored(cmd, cwd, ui_, round_num, watchdog_timeout=120):
            nonlocal call_count
            call_count += 1
            run_dir = self._extract_run_dir(cmd)
            if run_dir:
                run_dir.mkdir(parents=True, exist_ok=True)

            if call_count == 1:
                (run_dir / f"conversation_loop_{round_num}.md").write_text("# Converged")
                (run_dir / "CONVERGED").write_text("Done")
                (run_dir / "README_proposal.md").write_text(proposed_readme)
                imp_path.write_text("- [x] [functional] initial improvement\n")
                return 0, "converged", False
            else:
                raise SystemExit(42)

        with patch("evolve.application.run_loop._setup_forever_branch"), \
             patch("evolve.application.run_loop._ensure_git"), \
             patch("evolve.application.run_loop._ensure_runs_layout"), \
             patch("evolve.application.run_loop._run_monitored_subprocess", side_effect=mock_monitored), \
             patch("evolve.infrastructure.reporting.generator._generate_evolution_report"), \
             patch("evolve.application.run_loop._run_party_mode"), \
             patch("evolve.application.run_loop._git_commit"), \
             pytest.raises(SystemExit):
            evolve_loop(project_dir, max_rounds=1, forever=True)

        assert (project_dir / "README.md").read_text() == proposed_readme
        assert imp_path.read_text() == "# Improvements\n"

    def test_forever_no_readme_proposal_keeps_current_readme(self, tmp_path: Path):
        """When party mode produces no README_proposal.md, current README is preserved."""
        project_dir, imp_path = self._setup_project(tmp_path)
        original_readme = (project_dir / "README.md").read_text()

        call_count = 0

        def mock_monitored(cmd, cwd, ui_, round_num, watchdog_timeout=120):
            nonlocal call_count
            call_count += 1
            run_dir = self._extract_run_dir(cmd)
            if run_dir:
                run_dir.mkdir(parents=True, exist_ok=True)

            if call_count == 1:
                (run_dir / f"conversation_loop_{round_num}.md").write_text("# Converged")
                (run_dir / "CONVERGED").write_text("Done")
                imp_path.write_text("- [x] [functional] initial improvement\n")
                return 0, "converged", False
            else:
                raise SystemExit(42)

        with patch("evolve.application.run_loop._setup_forever_branch"), \
             patch("evolve.application.run_loop._ensure_git"), \
             patch("evolve.application.run_loop._ensure_runs_layout"), \
             patch("evolve.application.run_loop._run_monitored_subprocess", side_effect=mock_monitored), \
             patch("evolve.infrastructure.reporting.generator._generate_evolution_report"), \
             patch("evolve.application.run_loop._run_party_mode"), \
             patch("evolve.application.run_loop._git_commit"), \
             pytest.raises(SystemExit):
            evolve_loop(project_dir, max_rounds=1, forever=True)

        assert (project_dir / "README.md").read_text() == original_readme
        assert imp_path.read_text() == "# Improvements\n"

    def test_forever_failed_round_skips_and_continues(self, tmp_path: Path):
        """In forever mode, failed rounds (exhausted retries) skip to next round."""
        project_dir, imp_path = self._setup_project(tmp_path)

        round_attempts = {}

        def mock_monitored(cmd, cwd, ui_, round_num, watchdog_timeout=120):
            round_attempts.setdefault(round_num, 0)
            round_attempts[round_num] += 1
            run_dir = self._extract_run_dir(cmd)
            if run_dir:
                run_dir.mkdir(parents=True, exist_ok=True)

            if round_num == 1:
                return 0, "stalled output", True
            elif round_num == 2:
                if run_dir:
                    (run_dir / f"conversation_loop_{round_num}.md").write_text("# Round 2 ok")
                return 0, "ok output", False
            else:
                raise SystemExit(42)

        with patch("evolve.application.run_loop._setup_forever_branch"), \
             patch("evolve.application.run_loop._ensure_git"), \
             patch("evolve.application.run_loop._run_monitored_subprocess", side_effect=mock_monitored), \
             patch("evolve.application.run_loop._save_subprocess_diagnostic"), \
             patch("evolve.infrastructure.reporting.generator._generate_evolution_report"), \
             pytest.raises(SystemExit):
            evolve_loop(project_dir, max_rounds=2, forever=True)

        assert round_attempts[1] == MAX_DEBUG_RETRIES + 1

    def test_forever_sets_max_rounds_to_large_value(self, tmp_path: Path):
        """evolve_loop with forever=True internally sets max_rounds to 999999."""
        project_dir, imp_path = self._setup_project(tmp_path)

        with patch("evolve.application.run_loop._setup_forever_branch"), \
             patch("evolve.application.run_loop._ensure_git"), \
             patch("evolve.application.run_loop._run_rounds") as mock_run:
            evolve_loop(project_dir, max_rounds=5, forever=True)

        call_args = mock_run.call_args[0]
        assert call_args[5] == 999999

    def test_forever_new_session_dir_created_on_restart(self, tmp_path: Path):
        """After convergence in forever mode, a new timestamped session dir is created."""
        project_dir, imp_path = self._setup_project(tmp_path)

        call_count = 0

        def mock_monitored(cmd, cwd, ui_, round_num, watchdog_timeout=120):
            nonlocal call_count
            call_count += 1
            run_dir = self._extract_run_dir(cmd)
            if run_dir:
                run_dir.mkdir(parents=True, exist_ok=True)

            if call_count == 1:
                (run_dir / f"conversation_loop_{round_num}.md").write_text("# Converged")
                (run_dir / "CONVERGED").write_text("All done")
                imp_path.write_text("- [x] [functional] initial improvement\n")
                return 0, "converged", False
            else:
                raise SystemExit(42)

        dirs_before = set(
            d.name for d in (project_dir / "runs").iterdir() if d.is_dir()
        )

        with patch("evolve.application.run_loop._setup_forever_branch"), \
             patch("evolve.application.run_loop._ensure_git"), \
             patch("evolve.application.run_loop._ensure_runs_layout"), \
             patch("evolve.application.run_loop._run_monitored_subprocess", side_effect=mock_monitored), \
             patch("evolve.infrastructure.reporting.generator._generate_evolution_report"), \
             patch("evolve.application.run_loop._run_party_mode"), \
             patch("evolve.application.run_loop._forever_restart"), \
             patch("evolve.application.run_loop._git_commit"), \
             pytest.raises(SystemExit):
            evolve_loop(project_dir, max_rounds=1, forever=True)

        dirs_after = set(
            d.name for d in (project_dir / "runs").iterdir() if d.is_dir()
        )
        new_dirs = dirs_after - dirs_before
        assert len(new_dirs) >= 1

    def test_forever_convergence_triggers_report_and_summary(self, tmp_path: Path):
        """Convergence in forever mode generates evolution report and completion summary."""
        project_dir, imp_path = self._setup_project(tmp_path)
        ui = MagicMock()

        call_count = 0

        def mock_monitored(cmd, cwd, ui_, round_num, watchdog_timeout=120):
            nonlocal call_count
            call_count += 1
            run_dir = self._extract_run_dir(cmd)
            if run_dir:
                run_dir.mkdir(parents=True, exist_ok=True)

            if call_count == 1:
                (run_dir / f"conversation_loop_{round_num}.md").write_text("# Converged")
                (run_dir / "CONVERGED").write_text("All done")
                imp_path.write_text("- [x] [functional] initial improvement\n")
                return 0, "converged", False
            else:
                raise SystemExit(42)

        with patch("evolve.application.run_loop._setup_forever_branch"), \
             patch("evolve.application.run_loop._ensure_git"), \
             patch("evolve.application.run_loop._ensure_runs_layout"), \
             patch("evolve.interfaces.tui.get_tui", return_value=ui), \
             patch("evolve.application.run_loop._run_monitored_subprocess", side_effect=mock_monitored), \
             patch("evolve.infrastructure.reporting.generator._generate_evolution_report") as mock_report, \
             patch("evolve.application.run_loop._run_party_mode"), \
             patch("evolve.application.run_loop._forever_restart"), \
             patch("evolve.application.run_loop._git_commit"), \
             pytest.raises(SystemExit):
            evolve_loop(project_dir, max_rounds=1, forever=True)

        mock_report.assert_called_once()
        report_args = mock_report.call_args
        assert report_args[0][0] == project_dir
        assert report_args[1].get("converged") is True

        ui.converged.assert_called_once()
        ui.completion_summary.assert_called_once()
        summary_kwargs = ui.completion_summary.call_args[1]
        assert summary_kwargs["status"] == "CONVERGED"


# ---------------------------------------------------------------------------
# Integration tests for evolve_loop with --resume and --forever combined
# ---------------------------------------------------------------------------

class TestEvolveLoopResumeForeverCombined:
    """Tests for evolve_loop with both resume=True and forever=True."""

    def _setup_project_with_session(self, tmp_path: Path, num_convos: int = 3):
        """Create a project with an existing session and conversation logs."""
        project_dir = tmp_path / "proj"
        project_dir.mkdir()
        (project_dir / "README.md").write_text("# My Project\n")
        runs_dir = project_dir / "runs"
        runs_dir.mkdir()
        imp_path = runs_dir / "improvements.md"
        imp_path.write_text("- [x] [functional] done\n- [ ] [functional] pending\n")

        session = runs_dir / "20260101_120000"
        session.mkdir()
        for i in range(1, num_convos + 1):
            (session / f"conversation_loop_{i}.md").write_text(f"round {i}")

        return project_dir, session

    def test_resume_forever_calls_setup_branch_and_resumes(self, tmp_path: Path):
        """Resume + forever: sets up branch, detects last round, passes forever to _run_rounds."""
        project_dir, session = self._setup_project_with_session(tmp_path, num_convos=5)

        with patch("evolve.application.run_loop._ensure_git"), \
             patch("evolve.application.run_loop._setup_forever_branch") as mock_branch, \
             patch("evolve.application.run_loop._run_rounds") as mock_run:
            evolve_loop(project_dir, max_rounds=10, resume=True, forever=True)

        mock_branch.assert_called_once_with(project_dir)
        mock_run.assert_called_once()
        args = mock_run.call_args
        assert args[0][4] == 6, f"Expected start_round=6, got {args[0][4]}"
        assert args[0][5] == 999999, f"Expected max_rounds=999999, got {args[0][5]}"
        assert args[1].get("forever") is True

    def test_resume_forever_no_sessions_starts_fresh(self, tmp_path: Path):
        """Resume + forever with no existing sessions starts a fresh session."""
        project_dir = tmp_path / "proj"
        project_dir.mkdir()
        (project_dir / "README.md").write_text("# Test\n")
        (project_dir / "runs").mkdir()

        with patch("evolve.application.run_loop._ensure_git"), \
             patch("evolve.application.run_loop._setup_forever_branch") as mock_branch, \
             patch("evolve.application.run_loop._run_rounds") as mock_run:
            evolve_loop(project_dir, max_rounds=10, resume=True, forever=True)

        mock_branch.assert_called_once_with(project_dir)
        mock_run.assert_called_once()
        args = mock_run.call_args
        assert args[0][4] == 1
        assert args[0][5] == 999999
        assert args[1].get("forever") is True

    def test_resume_forever_session_no_convos_starts_round_1(self, tmp_path: Path):
        """Resume + forever with session but no conversation logs starts from round 1."""
        project_dir, session = self._setup_project_with_session(tmp_path, num_convos=0)

        with patch("evolve.application.run_loop._ensure_git"), \
             patch("evolve.application.run_loop._setup_forever_branch"), \
             patch("evolve.application.run_loop._run_rounds") as mock_run:
            evolve_loop(project_dir, max_rounds=10, resume=True, forever=True)

        mock_run.assert_called_once()
        args = mock_run.call_args
        assert args[0][4] == 1
        assert args[0][5] == 999999

    def test_resume_forever_uses_correct_session_dir(self, tmp_path: Path):
        """Resume + forever reuses the most recent session directory."""
        project_dir = tmp_path / "proj"
        project_dir.mkdir()
        (project_dir / "README.md").write_text("# Test\n")
        runs_dir = project_dir / "runs"
        runs_dir.mkdir()
        (runs_dir / "improvements.md").write_text("# Improvements\n")

        old_session = runs_dir / "20260101_100000"
        old_session.mkdir()
        (old_session / "conversation_loop_1.md").write_text("r1")

        new_session = runs_dir / "20260201_100000"
        new_session.mkdir()
        (new_session / "conversation_loop_1.md").write_text("r1")
        (new_session / "conversation_loop_2.md").write_text("r2")

        with patch("evolve.application.run_loop._ensure_git"), \
             patch("evolve.application.run_loop._ensure_runs_layout"), \
             patch("evolve.application.run_loop._setup_forever_branch"), \
             patch("evolve.application.run_loop._run_rounds") as mock_run:
            evolve_loop(project_dir, max_rounds=10, resume=True, forever=True)

        mock_run.assert_called_once()
        args = mock_run.call_args
        assert args[0][1] == new_session
        assert args[0][4] == 3

    def test_resume_forever_convergence_triggers_restart(self, tmp_path: Path):
        """Resume + forever: when CONVERGED is written, _forever_restart is called."""
        project_dir, session = self._setup_project_with_session(tmp_path, num_convos=2)
        imp_path = project_dir / "runs" / "improvements.md"

        call_count = 0

        def mock_monitored(cmd, cwd, ui_, round_num, watchdog_timeout=120):
            nonlocal call_count
            call_count += 1
            run_dir = None
            for i, arg in enumerate(cmd):
                if arg == "--run-dir" and i + 1 < len(cmd):
                    run_dir = Path(cmd[i + 1])
                    break
            if run_dir:
                run_dir.mkdir(parents=True, exist_ok=True)

            if call_count == 1:
                (run_dir / f"conversation_loop_{round_num}.md").write_text("converged")
                (run_dir / "CONVERGED").write_text("done")
                imp_path.write_text("- [x] [functional] done\n")
                return 0, "converged output", False
            else:
                raise SystemExit(42)

        with patch("evolve.application.run_loop._ensure_git"), \
             patch("evolve.application.run_loop._ensure_runs_layout"), \
             patch("evolve.application.run_loop._setup_forever_branch"), \
             patch("evolve.application.run_loop._run_monitored_subprocess", side_effect=mock_monitored), \
             patch("evolve.application.run_loop._git_commit"), \
             patch("evolve.application.run_loop._run_party_mode") as mock_party, \
             patch("evolve.application.run_loop._forever_restart") as mock_restart, \
             patch("evolve.infrastructure.reporting.generator._generate_evolution_report"), \
             patch("evolve.application.run_loop.fire_hook"), \
             pytest.raises(SystemExit) as exc:
            evolve_loop(project_dir, max_rounds=10, resume=True, forever=True)

        mock_party.assert_called_once()
        mock_restart.assert_called_once()
        assert exc.value.code == 42

    def test_resume_forever_no_runs_dir_starts_fresh(self, tmp_path: Path):
        """Resume + forever without runs/ dir creates fresh session."""
        project_dir = tmp_path / "proj"
        project_dir.mkdir()
        (project_dir / "README.md").write_text("# Test\n")

        with patch("evolve.application.run_loop._ensure_git"), \
             patch("evolve.application.run_loop._setup_forever_branch") as mock_branch, \
             patch("evolve.application.run_loop._run_rounds") as mock_run:
            evolve_loop(project_dir, max_rounds=10, resume=True, forever=True)

        mock_branch.assert_called_once()
        mock_run.assert_called_once()
        args = mock_run.call_args
        assert args[0][4] == 1
        assert args[0][5] == 999999
        assert args[1].get("forever") is True
