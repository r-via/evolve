"""Coverage tests for loop.py — run_single_round, evolve_loop, _run_rounds."""

import subprocess
import sys
import textwrap
from pathlib import Path
from unittest.mock import patch, MagicMock, call

import pytest

from loop import (
    run_single_round,
    evolve_loop,
    _run_rounds,
    _generate_evolution_report,
    _run_party_mode,
    _ensure_git,
    _git_commit,
    _count_checked,
    _count_unchecked,
    _get_current_improvement,
    MAX_DEBUG_RETRIES,
)


def _close_coro(coro):
    """Mock side_effect for asyncio.run that closes the coroutine to prevent warnings."""
    coro.close()


# ---------------------------------------------------------------------------
# run_single_round
# ---------------------------------------------------------------------------

class TestRunSingleRound:
    """Test run_single_round with mocked agent (lines 596-673)."""

    def test_basic_round_with_check(self, tmp_path: Path):
        """Run a single round with check command."""
        runs = tmp_path / "runs"
        runs.mkdir()
        run_dir = runs / "session"
        run_dir.mkdir()
        (tmp_path / "README.md").write_text("# Test")
        (runs / "improvements.md").write_text("- [ ] [functional] do something\n")

        mock_subprocess = MagicMock(returncode=0, stdout="42 passed", stderr="")

        with patch("loop.subprocess.run", return_value=mock_subprocess), \
             patch("agent.asyncio.run", side_effect=_close_coro), \
             patch.dict("sys.modules", {"claude_agent_sdk": MagicMock()}):
            run_single_round(
                tmp_path, round_num=1, check_cmd="pytest",
                timeout=60, run_dir=run_dir, model="claude-opus-4-6",
            )

    def test_round_without_check(self, tmp_path: Path):
        """Run a single round without check command."""
        runs = tmp_path / "runs"
        runs.mkdir()
        run_dir = runs / "session"
        run_dir.mkdir()
        (tmp_path / "README.md").write_text("# Test")

        with patch("loop.subprocess.run", return_value=MagicMock(returncode=0, stdout="", stderr="")), \
             patch("agent.asyncio.run", side_effect=_close_coro), \
             patch.dict("sys.modules", {"claude_agent_sdk": MagicMock()}):
            run_single_round(
                tmp_path, round_num=1,
                timeout=60, run_dir=run_dir,
            )

    def test_round_with_commit_msg_file(self, tmp_path: Path):
        """COMMIT_MSG file is used for git commit."""
        runs = tmp_path / "runs"
        runs.mkdir()
        run_dir = runs / "session"
        run_dir.mkdir()
        (tmp_path / "README.md").write_text("# Test")
        (runs / "improvements.md").write_text("- [ ] [functional] task\n")

        # Pre-create COMMIT_MSG (simulating what agent would do)
        (run_dir / "COMMIT_MSG").write_text("feat(parser): add validation")

        with patch("loop.subprocess.run", return_value=MagicMock(returncode=0, stdout="ok", stderr="")), \
             patch("agent.asyncio.run", side_effect=_close_coro), \
             patch.dict("sys.modules", {"claude_agent_sdk": MagicMock()}):
            run_single_round(
                tmp_path, round_num=1, check_cmd="pytest",
                timeout=60, run_dir=run_dir,
            )

        # COMMIT_MSG should be consumed (deleted)
        assert not (run_dir / "COMMIT_MSG").is_file()

    def test_round_check_timeout(self, tmp_path: Path):
        """Check command timeout is handled gracefully."""
        runs = tmp_path / "runs"
        runs.mkdir()
        run_dir = runs / "session"
        run_dir.mkdir()
        (tmp_path / "README.md").write_text("# Test")

        def mock_run(cmd, **kwargs):
            if kwargs.get("shell"):
                raise subprocess.TimeoutExpired(cmd="pytest", timeout=60)
            return MagicMock(returncode=0, stdout="", stderr="")

        with patch("loop.subprocess.run", side_effect=mock_run), \
             patch("agent.asyncio.run", side_effect=_close_coro), \
             patch.dict("sys.modules", {"claude_agent_sdk": MagicMock()}):
            run_single_round(
                tmp_path, round_num=1, check_cmd="pytest",
                timeout=60, run_dir=run_dir,
            )

    def test_round_generates_fallback_commit_msg(self, tmp_path: Path):
        """When no COMMIT_MSG and improvement changed, generates feat commit msg."""
        runs = tmp_path / "runs"
        runs.mkdir()
        run_dir = runs / "session"
        run_dir.mkdir()
        (tmp_path / "README.md").write_text("# Test")
        (runs / "improvements.md").write_text("- [ ] [functional] original task\n")

        # Mock analyze_and_fix to change the improvement
        original_analyze = None

        def mock_analyze(*args, **kwargs):
            (runs / "improvements.md").write_text(
                "- [x] [functional] original task\n- [ ] [functional] next\n"
            )

        git_calls = []

        def mock_run(cmd, **kwargs):
            if isinstance(cmd, list):
                git_calls.append(cmd)
            if kwargs.get("shell"):
                return MagicMock(returncode=0, stdout="ok", stderr="")
            # For git diff --cached --quiet: return 1 to indicate there ARE changes
            if isinstance(cmd, list) and "diff" in cmd and "--cached" in cmd:
                return MagicMock(returncode=1)
            return MagicMock(returncode=0, stdout="", stderr="")

        # Patch at the point of import inside run_single_round
        import agent as _agent_mod
        with patch("loop.subprocess.run", side_effect=mock_run), \
             patch.object(_agent_mod, "analyze_and_fix", mock_analyze), \
             patch.dict("sys.modules", {"claude_agent_sdk": MagicMock()}):
            run_single_round(
                tmp_path, round_num=1, check_cmd="pytest",
                timeout=60, run_dir=run_dir,
            )

        # Should have committed with feat message containing original task
        commit_calls = [c for c in git_calls if isinstance(c, list) and "commit" in c]
        assert any("original task" in str(c) for c in commit_calls)

    def test_round_sets_model(self, tmp_path: Path):
        """Model is set on agent module."""
        runs = tmp_path / "runs"
        runs.mkdir()
        run_dir = runs / "session"
        run_dir.mkdir()
        (tmp_path / "README.md").write_text("# Test")

        import agent as agent_mod
        original_model = agent_mod.MODEL

        with patch("loop.subprocess.run", return_value=MagicMock(returncode=0, stdout="", stderr="")), \
             patch("agent.asyncio.run", side_effect=_close_coro), \
             patch.dict("sys.modules", {"claude_agent_sdk": MagicMock()}):
            run_single_round(
                tmp_path, round_num=1,
                run_dir=run_dir, model="claude-sonnet-4-20250514",
            )
            assert agent_mod.MODEL == "claude-sonnet-4-20250514"

        agent_mod.MODEL = original_model

    def test_round_check_with_stderr(self, tmp_path: Path):
        """Check command stderr is captured."""
        runs = tmp_path / "runs"
        runs.mkdir()
        run_dir = runs / "session"
        run_dir.mkdir()
        (tmp_path / "README.md").write_text("# Test")

        def mock_run(cmd, **kwargs):
            if kwargs.get("shell"):
                return MagicMock(returncode=1, stdout="3 passed", stderr="DeprecationWarning")
            return MagicMock(returncode=0, stdout="", stderr="")

        with patch("loop.subprocess.run", side_effect=mock_run), \
             patch("agent.asyncio.run", side_effect=_close_coro), \
             patch.dict("sys.modules", {"claude_agent_sdk": MagicMock()}):
            run_single_round(
                tmp_path, round_num=1, check_cmd="pytest",
                timeout=60, run_dir=run_dir,
            )

    def test_round_no_run_dir(self, tmp_path: Path):
        """run_dir defaults to project/runs when None."""
        runs = tmp_path / "runs"
        runs.mkdir()
        (tmp_path / "README.md").write_text("# Test")

        with patch("loop.subprocess.run", return_value=MagicMock(returncode=0, stdout="", stderr="")), \
             patch("agent.asyncio.run", side_effect=_close_coro), \
             patch.dict("sys.modules", {"claude_agent_sdk": MagicMock()}):
            run_single_round(tmp_path, round_num=1, run_dir=None)


# ---------------------------------------------------------------------------
# evolve_loop — setup and resume paths
# ---------------------------------------------------------------------------

class TestEvolveLoop:
    """Test evolve_loop entry point (lines 233-285)."""

    def test_fresh_start_creates_run_dir(self, tmp_path: Path):
        """Fresh start creates timestamped run directory."""
        (tmp_path / "README.md").write_text("# Test")
        (tmp_path / "runs").mkdir()

        with patch("loop._ensure_git"), \
             patch("loop._run_rounds") as mock_run:
            evolve_loop(tmp_path, max_rounds=5)

        mock_run.assert_called_once()
        args = mock_run.call_args
        assert args[0][1].parent == tmp_path / "runs"

    def test_resume_detects_last_round(self, tmp_path: Path):
        """Resume finds the latest session and detects last round."""
        (tmp_path / "README.md").write_text("# Test")
        runs = tmp_path / "runs"
        session = runs / "20260101_000000"
        session.mkdir(parents=True)
        (session / "conversation_loop_1.md").write_text("r1")
        (session / "conversation_loop_2.md").write_text("r2")
        (runs / "improvements.md").write_text("# Improvements\n")

        with patch("loop._ensure_git"), \
             patch("loop._run_rounds") as mock_run:
            evolve_loop(tmp_path, max_rounds=10, resume=True)

        mock_run.assert_called_once()
        args = mock_run.call_args
        assert args[0][4] == 3  # start_round

    def test_resume_no_sessions_starts_fresh(self, tmp_path: Path):
        """Resume with no sessions starts a fresh session."""
        (tmp_path / "README.md").write_text("# Test")
        (tmp_path / "runs").mkdir()

        with patch("loop._ensure_git"), \
             patch("loop._run_rounds") as mock_run:
            evolve_loop(tmp_path, max_rounds=5, resume=True)

        mock_run.assert_called_once()
        args = mock_run.call_args
        assert args[0][4] == 1

    def test_forever_mode_creates_branch(self, tmp_path: Path):
        """Forever mode calls _setup_forever_branch and sets high max_rounds."""
        (tmp_path / "README.md").write_text("# Test")
        (tmp_path / "runs").mkdir()

        with patch("loop._ensure_git"), \
             patch("loop._setup_forever_branch") as mock_branch, \
             patch("loop._run_rounds") as mock_run:
            evolve_loop(tmp_path, max_rounds=10, forever=True)

        mock_branch.assert_called_once_with(tmp_path)
        mock_run.assert_called_once()
        args = mock_run.call_args
        assert args[0][5] == 999999

    def test_resume_no_runs_dir(self, tmp_path: Path):
        """Resume when runs/ doesn't exist starts fresh."""
        (tmp_path / "README.md").write_text("# Test")

        with patch("loop._ensure_git"), \
             patch("loop._run_rounds") as mock_run:
            evolve_loop(tmp_path, max_rounds=5, resume=True)

        mock_run.assert_called_once()

    def test_resume_session_no_convos(self, tmp_path: Path):
        """Resume with session but no conversation logs starts from round 1."""
        (tmp_path / "README.md").write_text("# Test")
        runs = tmp_path / "runs"
        session = runs / "20260101_000000"
        session.mkdir(parents=True)
        (runs / "improvements.md").write_text("# Improvements\n")

        with patch("loop._ensure_git"), \
             patch("loop._run_rounds") as mock_run:
            evolve_loop(tmp_path, max_rounds=5, resume=True)

        mock_run.assert_called_once()
        args = mock_run.call_args
        assert args[0][4] == 1


# ---------------------------------------------------------------------------
# _run_rounds — convergence, max rounds, debug retries
# ---------------------------------------------------------------------------

class TestRunRounds:
    """Test _run_rounds orchestration logic (lines 414-569)."""

    def _setup_project(self, tmp_path):
        project_dir = tmp_path / "proj"
        project_dir.mkdir()
        run_dir = project_dir / "runs" / "session"
        run_dir.mkdir(parents=True)
        imp_path = project_dir / "runs" / "improvements.md"
        imp_path.write_text("- [ ] [functional] do something\n")
        return project_dir, run_dir, imp_path

    def test_convergence_exits_0(self, tmp_path: Path):
        """When CONVERGED is written, exits with code 0."""
        project_dir, run_dir, imp_path = self._setup_project(tmp_path)
        ui = MagicMock()

        def mock_monitored(cmd, cwd, ui_, round_num, watchdog_timeout=120):
            convo = run_dir / f"conversation_loop_{round_num}.md"
            convo.write_text("# Round conversation")
            (run_dir / "CONVERGED").write_text("All done")
            imp_path.write_text("- [x] [functional] do something\n")
            return 0, "output", False

        with patch("loop._run_monitored_subprocess", side_effect=mock_monitored), \
             patch("loop._generate_evolution_report"), \
             patch("loop._run_party_mode"), \
             pytest.raises(SystemExit) as exc:
            _run_rounds(
                project_dir, run_dir, imp_path, ui,
                start_round=1, max_rounds=10, check_cmd="pytest",
                yolo=False, timeout=300, model="claude-opus-4-6",
            )
        assert exc.value.code == 0

    def test_max_rounds_exits_1(self, tmp_path: Path):
        """When max rounds reached without convergence, exits with code 1."""
        project_dir, run_dir, imp_path = self._setup_project(tmp_path)
        ui = MagicMock()

        def mock_monitored(cmd, cwd, ui_, round_num, watchdog_timeout=120):
            convo = run_dir / f"conversation_loop_{round_num}.md"
            convo.write_text("# Round conversation")
            return 0, "output", False

        with patch("loop._run_monitored_subprocess", side_effect=mock_monitored), \
             patch("loop._generate_evolution_report"), \
             pytest.raises(SystemExit) as exc:
            _run_rounds(
                project_dir, run_dir, imp_path, ui,
                start_round=1, max_rounds=2, check_cmd="pytest",
                yolo=False, timeout=300, model="claude-opus-4-6",
            )
        assert exc.value.code == 1

    def test_stalled_round_exhausts_retries(self, tmp_path: Path):
        """Stalled subprocess exhausting retries exits with code 2."""
        project_dir, run_dir, imp_path = self._setup_project(tmp_path)
        ui = MagicMock()

        def mock_monitored(cmd, cwd, ui_, round_num, watchdog_timeout=120):
            return 0, "output", True  # always stalls

        with patch("loop._run_monitored_subprocess", side_effect=mock_monitored), \
             patch("loop._save_subprocess_diagnostic"), \
             patch("loop._generate_evolution_report"), \
             pytest.raises(SystemExit) as exc:
            _run_rounds(
                project_dir, run_dir, imp_path, ui,
                start_round=1, max_rounds=1, check_cmd="pytest",
                yolo=False, timeout=300, model="claude-opus-4-6",
            )
        assert exc.value.code == 2

    def test_crashed_round_retries_then_succeeds(self, tmp_path: Path):
        """Crashed subprocess recovers on retry."""
        project_dir, run_dir, imp_path = self._setup_project(tmp_path)
        ui = MagicMock()

        call_count = 0

        def mock_monitored(cmd, cwd, ui_, round_num, watchdog_timeout=120):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return 1, "crash output", False
            convo = run_dir / f"conversation_loop_{round_num}.md"
            convo.write_text("# Recovered")
            return 0, "output", False

        with patch("loop._run_monitored_subprocess", side_effect=mock_monitored), \
             patch("loop._save_subprocess_diagnostic"), \
             patch("loop._generate_evolution_report"), \
             pytest.raises(SystemExit):
            _run_rounds(
                project_dir, run_dir, imp_path, ui,
                start_round=1, max_rounds=1, check_cmd="pytest",
                yolo=False, timeout=300, model="claude-opus-4-6",
            )

    def test_no_progress_triggers_retry(self, tmp_path: Path):
        """Subprocess succeeds but makes no progress triggers retry."""
        project_dir, run_dir, imp_path = self._setup_project(tmp_path)
        ui = MagicMock()

        def mock_monitored(cmd, cwd, ui_, round_num, watchdog_timeout=120):
            return 0, "output", False

        with patch("loop._run_monitored_subprocess", side_effect=mock_monitored), \
             patch("loop._save_subprocess_diagnostic"), \
             patch("loop._generate_evolution_report"), \
             pytest.raises(SystemExit) as exc:
            _run_rounds(
                project_dir, run_dir, imp_path, ui,
                start_round=1, max_rounds=1, check_cmd=None,
                yolo=False, timeout=300, model="claude-opus-4-6",
            )
        assert exc.value.code == 2

    def test_forever_mode_skips_failed_round(self, tmp_path: Path):
        """In forever mode, exhausted retries skip to next round."""
        project_dir, run_dir, imp_path = self._setup_project(tmp_path)
        ui = MagicMock()

        round_attempts = {}

        def mock_monitored(cmd, cwd, ui_, round_num, watchdog_timeout=120):
            round_attempts.setdefault(round_num, 0)
            round_attempts[round_num] += 1
            if round_num == 1:
                return 0, "output", True  # always stalls
            convo = run_dir / f"conversation_loop_{round_num}.md"
            convo.write_text("# Round 2")
            return 0, "output", False

        with patch("loop._run_monitored_subprocess", side_effect=mock_monitored), \
             patch("loop._save_subprocess_diagnostic"), \
             patch("loop._generate_evolution_report"), \
             pytest.raises(SystemExit):
            _run_rounds(
                project_dir, run_dir, imp_path, ui,
                start_round=1, max_rounds=2, check_cmd=None,
                yolo=False, timeout=300, model="claude-opus-4-6",
                forever=True,
            )

        assert round_attempts[1] == MAX_DEBUG_RETRIES + 1

    def test_blocked_improvements_exit(self, tmp_path: Path):
        """All remaining items blocked exits with code 1."""
        project_dir, run_dir, imp_path = self._setup_project(tmp_path)
        imp_path.write_text("- [ ] [functional] [needs-package] blocked\n")
        ui = MagicMock()

        with pytest.raises(SystemExit) as exc:
            _run_rounds(
                project_dir, run_dir, imp_path, ui,
                start_round=1, max_rounds=10, check_cmd=None,
                yolo=False, timeout=300, model="claude-opus-4-6",
            )
        assert exc.value.code == 1
        ui.blocked_message.assert_called_once()

    def test_error_log_cleanup_on_success(self, tmp_path: Path):
        """Diagnostic file is cleaned up after a successful round."""
        project_dir, run_dir, imp_path = self._setup_project(tmp_path)
        ui = MagicMock()

        error_log = run_dir / "subprocess_error_round_1.txt"
        error_log.write_text("previous error")

        def mock_monitored(cmd, cwd, ui_, round_num, watchdog_timeout=120):
            convo = run_dir / f"conversation_loop_{round_num}.md"
            convo.write_text("# Round conversation")
            return 0, "output", False

        with patch("loop._run_monitored_subprocess", side_effect=mock_monitored), \
             patch("loop._generate_evolution_report"), \
             pytest.raises(SystemExit):
            _run_rounds(
                project_dir, run_dir, imp_path, ui,
                start_round=1, max_rounds=1, check_cmd=None,
                yolo=False, timeout=300, model="claude-opus-4-6",
            )

        assert not error_log.is_file()

    def test_yolo_flag_passed_to_subprocess(self, tmp_path: Path):
        """--yolo flag is included in subprocess command."""
        project_dir, run_dir, imp_path = self._setup_project(tmp_path)
        ui = MagicMock()

        captured_cmd = None

        def mock_monitored(cmd, cwd, ui_, round_num, watchdog_timeout=120):
            nonlocal captured_cmd
            captured_cmd = cmd
            convo = run_dir / f"conversation_loop_{round_num}.md"
            convo.write_text("# Round")
            return 0, "output", False

        with patch("loop._run_monitored_subprocess", side_effect=mock_monitored), \
             patch("loop._generate_evolution_report"), \
             pytest.raises(SystemExit):
            _run_rounds(
                project_dir, run_dir, imp_path, ui,
                start_round=1, max_rounds=1, check_cmd="pytest",
                yolo=True, timeout=300, model="claude-opus-4-6",
            )

        assert "--yolo" in captured_cmd
        assert "--check" in captured_cmd

    def test_no_current_improvement_initial_analysis(self, tmp_path: Path):
        """When no improvements exist yet, shows 'initial analysis'."""
        project_dir, run_dir, imp_path = self._setup_project(tmp_path)
        imp_path.write_text("# Improvements\n")  # empty
        ui = MagicMock()

        def mock_monitored(cmd, cwd, ui_, round_num, watchdog_timeout=120):
            convo = run_dir / f"conversation_loop_{round_num}.md"
            convo.write_text("# Round")
            return 0, "output", False

        with patch("loop._run_monitored_subprocess", side_effect=mock_monitored), \
             patch("loop._generate_evolution_report"), \
             pytest.raises(SystemExit):
            _run_rounds(
                project_dir, run_dir, imp_path, ui,
                start_round=1, max_rounds=1, check_cmd=None,
                yolo=False, timeout=300, model="claude-opus-4-6",
            )

        ui.round_header.assert_called()


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

        with patch("loop.subprocess.run", side_effect=mock_run):
            _generate_evolution_report(project_dir, run_dir, 10, 1, True)

        report = (run_dir / "evolution_report.md").read_text()
        assert "fix(parser): handle empty input" in report

    def test_report_with_failed_check_counts(self, tmp_path: Path):
        """Report shows failed count in check results with pass/fail format."""
        project_dir, run_dir = self._setup_report_project(tmp_path, "- [ ] pending\n")
        (run_dir / "check_round_1.txt").write_text(
            "Round 1: FAIL\n10 passed, 3 failed\n"
        )

        with patch("loop.subprocess.run", return_value=MagicMock(returncode=1, stdout="")):
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

        with patch("loop.subprocess.run", return_value=MagicMock(returncode=1, stdout="")):
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

        with patch("loop.subprocess.run", side_effect=mock_run):
            _generate_evolution_report(project_dir, run_dir, 10, 1, False)

        report = (run_dir / "evolution_report.md").read_text()
        assert "round 1" in report


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
            # Second call (after restart): just complete normally, no convergence
            # The run_dir changes on restart, so conversation goes to new dir
            # Just create a conversation file wherever the cwd points
            for d in Path(cwd).glob("runs/*/"):
                if d.is_dir() and d.name != "session":
                    (d / f"conversation_loop_{round_num}.md").write_text("# Round after restart")
                    break
            return 0, "output", False

        with patch("loop._run_monitored_subprocess", side_effect=mock_monitored), \
             patch("loop._generate_evolution_report"), \
             patch("loop._run_party_mode"), \
             patch("loop._forever_restart") as mock_restart, \
             patch("loop._git_commit") as mock_commit, \
             pytest.raises(SystemExit) as exc:
            _run_rounds(
                project_dir, run_dir, imp_path, ui,
                start_round=1, max_rounds=1, check_cmd="pytest",
                yolo=False, timeout=300, model="claude-opus-4-6",
                forever=True,
            )

        # _forever_restart should have been called
        mock_restart.assert_called_once_with(project_dir, run_dir, imp_path, ui)
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

        with patch("loop._run_monitored_subprocess", side_effect=mock_monitored), \
             patch("loop._generate_evolution_report"), \
             patch("loop._run_party_mode"), \
             patch("loop._forever_restart"), \
             patch("loop._git_commit"), \
             pytest.raises(SystemExit):
            _run_rounds(
                project_dir, run_dir, imp_path, ui,
                start_round=1, max_rounds=1, check_cmd=None,
                yolo=False, timeout=300, model="claude-opus-4-6",
                forever=True,
            )

        new_dirs_after = set(d.name for d in (project_dir / "runs").iterdir() if d.is_dir())
        new_session_dirs = new_dirs_after - new_dirs_before
        # A new timestamped session directory should have been created
        assert len(new_session_dirs) == 1


# ---------------------------------------------------------------------------
# Party mode result handling (lines 987-993 of _run_party_mode)
# ---------------------------------------------------------------------------

def _setup_party_project(tmp_path):
    """Shared helper: set up a project with agents and context for party mode tests."""
    agents = tmp_path / "agents"
    agents.mkdir()
    (agents / "dev.md").write_text("# Dev Agent")
    run_dir = tmp_path / "runs" / "session"
    run_dir.mkdir(parents=True)
    (tmp_path / "README.md").write_text("# Test Project")
    (tmp_path / "runs" / "improvements.md").write_text("- [x] done\n")
    (tmp_path / "runs" / "memory.md").write_text("# Memory\n")
    (run_dir / "CONVERGED").write_text("All done")
    return run_dir


def _run_party_with_mock(tmp_path, run_dir, ui, asyncio_side_effect):
    """Shared helper: run _run_party_mode with mocked asyncio.run and agent."""
    import asyncio as _asyncio
    import agent as agent_mod

    with patch.object(agent_mod, 'run_claude_agent', return_value=MagicMock()), \
         patch.object(_asyncio, 'run', side_effect=asyncio_side_effect):
        _run_party_mode(tmp_path, run_dir, ui)


class TestPartyModeResultHandling:
    """Test the end of _run_party_mode where it checks for output files."""

    def test_both_files_produced(self, tmp_path: Path):
        """party_results called with both paths when both files exist."""
        run_dir = _setup_party_project(tmp_path)
        ui = MagicMock()

        def mock_asyncio_run(coro):
            coro.close()
            (run_dir / "party_report.md").write_text("# Party Report\n")
            (run_dir / "README_proposal.md").write_text("# New README\n")

        _run_party_with_mock(tmp_path, run_dir, ui, mock_asyncio_run)

        ui.party_results.assert_called_once_with(
            str(run_dir / "README_proposal.md"),
            str(run_dir / "party_report.md"),
        )

    def test_no_files_produced(self, tmp_path: Path):
        """party_results called with None when no files produced."""
        run_dir = _setup_party_project(tmp_path)
        ui = MagicMock()

        def mock_asyncio_run(coro):
            coro.close()

        _run_party_with_mock(tmp_path, run_dir, ui, mock_asyncio_run)

        ui.party_results.assert_called_once_with(None, None)

    def test_only_report_produced(self, tmp_path: Path):
        """party_results called with report only when proposal missing."""
        run_dir = _setup_party_project(tmp_path)
        ui = MagicMock()

        def mock_asyncio_run(coro):
            coro.close()
            (run_dir / "party_report.md").write_text("# Report\n")

        _run_party_with_mock(tmp_path, run_dir, ui, mock_asyncio_run)

        ui.party_results.assert_called_once_with(
            None,
            str(run_dir / "party_report.md"),
        )

    def test_only_proposal_produced(self, tmp_path: Path):
        """party_results called with proposal only when report missing."""
        run_dir = _setup_party_project(tmp_path)
        ui = MagicMock()

        def mock_asyncio_run(coro):
            coro.close()
            (run_dir / "README_proposal.md").write_text("# Proposal\n")

        _run_party_with_mock(tmp_path, run_dir, ui, mock_asyncio_run)

        ui.party_results.assert_called_once_with(
            str(run_dir / "README_proposal.md"),
            None,
        )


# ---------------------------------------------------------------------------
# Party mode retry paths (lines 965-985 of _run_party_mode)
# ---------------------------------------------------------------------------

class TestPartyModeRetryPaths:
    """Test the retry/error-handling logic inside _run_party_mode agent execution."""

    def test_benign_runtime_error_breaks_loop(self, tmp_path: Path):
        """Benign RuntimeError (cancel scope) should be treated as success."""
        run_dir = _setup_party_project(tmp_path)
        ui = MagicMock()
        import asyncio as _asyncio
        import agent as agent_mod

        call_count = 0

        def mock_asyncio_run(coro):
            nonlocal call_count
            call_count += 1
            coro.close()
            raise RuntimeError("cancel scope blah blah")

        with patch.object(agent_mod, 'run_claude_agent', return_value=MagicMock()), \
             patch.object(_asyncio, 'run', side_effect=mock_asyncio_run):
            _run_party_mode(tmp_path, run_dir, ui)

        # Should only be called once — benign error breaks the retry loop
        assert call_count == 1
        # Should NOT warn — benign errors are not failures
        ui.warn.assert_not_called()
        # party_results should still be called (post-loop code runs)
        ui.party_results.assert_called_once()

    def test_rate_limit_retries_with_sleep(self, tmp_path: Path):
        """Rate limit error should trigger retry with sleep."""
        run_dir = _setup_party_project(tmp_path)
        ui = MagicMock()
        import asyncio as _asyncio
        import agent as agent_mod

        call_count = 0

        def mock_asyncio_run(coro):
            nonlocal call_count
            call_count += 1
            coro.close()
            if call_count == 1:
                raise Exception("rate_limit exceeded")
            # Second call succeeds

        with patch.object(agent_mod, 'run_claude_agent', return_value=MagicMock()), \
             patch.object(_asyncio, 'run', side_effect=mock_asyncio_run), \
             patch("time.sleep") as mock_sleep:
            _run_party_mode(tmp_path, run_dir, ui)

        # Should have been called twice (first fails with rate limit, second succeeds)
        assert call_count == 2
        # Sleep should have been called with 60 (60 * attempt=1)
        mock_sleep.assert_called_once_with(60)
        # sdk_rate_limited UI callback should have been called
        ui.sdk_rate_limited.assert_called_once_with(60, 1, 5)
        ui.warn.assert_not_called()

    def test_non_retryable_exception_warns_and_returns(self, tmp_path: Path):
        """Non-retryable, non-benign exception should warn and return early."""
        run_dir = _setup_party_project(tmp_path)
        ui = MagicMock()
        import asyncio as _asyncio
        import agent as agent_mod

        def mock_asyncio_run(coro):
            coro.close()
            raise ValueError("something unexpected broke")

        with patch.object(agent_mod, 'run_claude_agent', return_value=MagicMock()), \
             patch.object(_asyncio, 'run', side_effect=mock_asyncio_run):
            _run_party_mode(tmp_path, run_dir, ui)

        ui.warn.assert_called_once_with("Party mode failed (something unexpected broke)")
        # party_results should NOT be called — function returns early
        ui.party_results.assert_not_called()

    def test_import_error_skips_party_mode(self, tmp_path: Path):
        """ImportError from missing claude-agent-sdk should warn and return."""
        run_dir = self._setup_party(tmp_path)
        ui = MagicMock()

        with patch("builtins.__import__", side_effect=_make_import_error_for_agent):
            _run_party_mode(tmp_path, run_dir, ui)

        ui.warn.assert_called_once_with("claude-agent-sdk not installed — skipping party mode")
        ui.party_results.assert_not_called()


def _make_import_error_for_agent(name, *args, **kwargs):
    """Simulate ImportError only for the 'agent' module import inside _run_party_mode."""
    if name == "agent":
        raise ImportError("No module named 'agent'")
    return original_import(name, *args, **kwargs)


original_import = __builtins__.__import__ if hasattr(__builtins__, '__import__') else __import__


# ---------------------------------------------------------------------------
# evolve_loop — auto-detect check command (lines 307-310)
# ---------------------------------------------------------------------------

class TestEvolveLoopAutoDetect:
    """Test that evolve_loop auto-detects check command when none provided."""

    def test_auto_detect_sets_check_cmd(self, tmp_path: Path):
        """When check_cmd is None and auto-detect finds a tool, lines 307-310 execute."""
        (tmp_path / "README.md").write_text("# Test")
        (tmp_path / "runs").mkdir()

        with patch("loop._auto_detect_check", return_value="pytest") as mock_detect, \
             patch("loop._ensure_git"), \
             patch("loop._run_rounds") as mock_run, \
             patch("loop.get_tui") as mock_get_tui:
            evolve_loop(tmp_path, max_rounds=5, check_cmd=None)

        mock_detect.assert_called_once_with(tmp_path)
        # get_tui called for early UI message
        mock_get_tui.assert_called()
        # check_cmd should have been passed to _run_rounds as "pytest"
        mock_run.assert_called_once()
        args = mock_run.call_args
        assert args[0][6] == "pytest"  # check_cmd is the 7th positional arg


# ---------------------------------------------------------------------------
# _run_party_mode — agent persona read error (lines 887-888)
# ---------------------------------------------------------------------------

class TestPartyModeAgentReadError:
    """Test _run_party_mode when an agent persona file raises an error on read."""

    def test_agent_file_read_error_skipped(self, tmp_path: Path):
        """Agent persona file that raises OSError is skipped (lines 887-888)."""
        agents = tmp_path / "agents"
        agents.mkdir()
        # Create one good and one bad agent file
        (agents / "good.md").write_text("# Good Agent")
        bad_file = agents / "bad.md"
        bad_file.write_text("# Bad Agent")

        run_dir = tmp_path / "runs" / "session"
        run_dir.mkdir(parents=True)
        (tmp_path / "README.md").write_text("# Test")
        (tmp_path / "runs" / "improvements.md").write_text("- [x] done\n")
        (tmp_path / "runs" / "memory.md").write_text("# Memory\n")
        (run_dir / "CONVERGED").write_text("All done")

        ui = MagicMock()

        original_read_text = Path.read_text

        def patched_read_text(self_path, *args, **kwargs):
            if self_path.name == "bad.md" and "agents" in str(self_path):
                raise OSError("Permission denied")
            return original_read_text(self_path, *args, **kwargs)

        real_import = __import__

        def mock_import(name, *args, **kwargs):
            if name == "claude_agent_sdk":
                raise ImportError("mocked")
            return real_import(name, *args, **kwargs)

        with patch.object(Path, "read_text", patched_read_text), \
             patch("builtins.__import__", side_effect=mock_import):
            _run_party_mode(tmp_path, run_dir, ui)

        # Should still proceed (the good agent was loaded) — but SDK missing so it warns
        ui.warn.assert_called()


# ---------------------------------------------------------------------------
# _run_party_mode — workflow fallback to project dir (line 895)
# and step file read error (lines 906-907)
# ---------------------------------------------------------------------------

class TestPartyModeWorkflowFallback:
    """Test workflow directory fallback and step file read errors."""

    def test_workflow_falls_back_to_project_dir(self, tmp_path: Path):
        """When evolve's own wf_dir doesn't exist, falls back to project_dir (line 895)."""
        agents = tmp_path / "agents"
        agents.mkdir()
        (agents / "dev.md").write_text("# Dev Agent")

        # Create workflow in the project dir (not in evolve package dir)
        wf_dir = tmp_path / "workflows" / "party-mode"
        wf_dir.mkdir(parents=True)
        (wf_dir / "workflow.md").write_text("# Custom Workflow")

        run_dir = tmp_path / "runs" / "session"
        run_dir.mkdir(parents=True)
        (tmp_path / "README.md").write_text("# Test")
        (tmp_path / "runs" / "improvements.md").write_text("- [x] done\n")
        (tmp_path / "runs" / "memory.md").write_text("# Memory\n")
        (run_dir / "CONVERGED").write_text("All done")

        ui = MagicMock()

        # Patch the evolve package's workflow dir to not exist so it falls back
        import loop as loop_mod
        real_parent = Path(loop_mod.__file__).parent

        real_is_dir = Path.is_dir

        def patched_is_dir(self_path):
            # Make the evolve package's wf_dir appear to not exist
            if str(self_path) == str(real_parent / "workflows" / "party-mode"):
                return False
            return real_is_dir(self_path)

        real_import = __import__

        def mock_import(name, *args, **kwargs):
            if name == "claude_agent_sdk":
                raise ImportError("mocked")
            return real_import(name, *args, **kwargs)

        with patch.object(Path, "is_dir", patched_is_dir), \
             patch("builtins.__import__", side_effect=mock_import):
            _run_party_mode(tmp_path, run_dir, ui)

        # Should warn about missing SDK but have loaded the workflow from project dir
        ui.warn.assert_called()

    def test_step_file_read_error_skipped(self, tmp_path: Path):
        """Step file that raises OSError is skipped (lines 906-907)."""
        agents = tmp_path / "agents"
        agents.mkdir()
        (agents / "dev.md").write_text("# Dev Agent")

        # Create workflow with steps dir containing a bad file
        import loop as loop_mod
        wf_dir = Path(loop_mod.__file__).parent / "workflows" / "party-mode"
        # We'll use project-level workflow dir to control file contents
        proj_wf_dir = tmp_path / "workflows" / "party-mode" / "steps"
        proj_wf_dir.mkdir(parents=True)
        (proj_wf_dir.parent / "workflow.md").write_text("# Workflow")
        (proj_wf_dir / "step-01.md").write_text("# Step 1")
        bad_step = proj_wf_dir / "step-02.md"
        bad_step.write_text("# Step 2 — will fail")

        run_dir = tmp_path / "runs" / "session"
        run_dir.mkdir(parents=True)
        (tmp_path / "README.md").write_text("# Test")
        (tmp_path / "runs" / "improvements.md").write_text("- [x] done\n")
        (tmp_path / "runs" / "memory.md").write_text("# Memory\n")
        (run_dir / "CONVERGED").write_text("All done")

        ui = MagicMock()

        real_parent = Path(loop_mod.__file__).parent
        real_is_dir = Path.is_dir

        def patched_is_dir(self_path):
            if str(self_path) == str(real_parent / "workflows" / "party-mode"):
                return False
            return real_is_dir(self_path)

        original_read_text = Path.read_text

        def patched_read_text(self_path, *args, **kwargs):
            if self_path.name == "step-02.md" and "steps" in str(self_path):
                raise OSError("Permission denied")
            return original_read_text(self_path, *args, **kwargs)

        real_import = __import__

        def mock_import(name, *args, **kwargs):
            if name == "claude_agent_sdk":
                raise ImportError("mocked")
            return real_import(name, *args, **kwargs)

        with patch.object(Path, "is_dir", patched_is_dir), \
             patch.object(Path, "read_text", patched_read_text), \
             patch("builtins.__import__", side_effect=mock_import):
            _run_party_mode(tmp_path, run_dir, ui)

        ui.warn.assert_called()


# ---------------------------------------------------------------------------
# _forever_restart — CONVERGED file exists (line 1030)
# ---------------------------------------------------------------------------

class TestForeverRestartConvergedFile:
    """Test _forever_restart when CONVERGED file exists in run_dir."""

    def test_converged_file_preserved(self, tmp_path: Path):
        """CONVERGED file is left in place (line 1030 — pass branch)."""
        from loop import _forever_restart

        run_dir = tmp_path / "runs" / "session1"
        run_dir.mkdir(parents=True)
        improvements = tmp_path / "runs" / "improvements.md"
        improvements.write_text("# Improvements\n- [x] done\n")

        # Create both README_proposal.md and CONVERGED
        (run_dir / "README_proposal.md").write_text("# New README\n")
        (tmp_path / "README.md").write_text("# Old README\n")
        converged = run_dir / "CONVERGED"
        converged.write_text("All done — fully converged")

        ui = MagicMock()
        _forever_restart(tmp_path, run_dir, improvements, ui)

        # CONVERGED file should still exist (preserved, not deleted)
        assert converged.is_file()
        assert converged.read_text() == "All done — fully converged"
        # improvements reset
        assert improvements.read_text() == "# Improvements\n"
        # README adopted
        assert (tmp_path / "README.md").read_text() == "# New README\n"
