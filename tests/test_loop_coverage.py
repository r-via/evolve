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
    def test_report_with_git_log_match(self, tmp_path: Path):
        """Report uses git log commit messages when available."""
        project_dir = tmp_path / "proj"
        project_dir.mkdir()
        runs_dir = project_dir / "runs"
        runs_dir.mkdir()
        run_dir = runs_dir / "session"
        run_dir.mkdir()
        (runs_dir / "improvements.md").write_text("- [x] done\n")

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
        project_dir = tmp_path / "proj"
        project_dir.mkdir()
        runs_dir = project_dir / "runs"
        runs_dir.mkdir()
        run_dir = runs_dir / "session"
        run_dir.mkdir()
        (runs_dir / "improvements.md").write_text("- [ ] pending\n")
        # Use pytest-style format that the parser recognizes
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
        project_dir = tmp_path / "proj"
        project_dir.mkdir()
        runs_dir = project_dir / "runs"
        runs_dir.mkdir()
        run_dir = runs_dir / "session"
        run_dir.mkdir()
        (runs_dir / "improvements.md").write_text("- [x] done\n")
        (run_dir / "conversation_loop_1.md").write_text(
            "Edit → a.py\nEdit → b.py\nEdit → c.py\nEdit → d.py\nEdit → e.py\n"
        )

        with patch("loop.subprocess.run", return_value=MagicMock(returncode=1, stdout="")):
            _generate_evolution_report(project_dir, run_dir, 10, 1, True)

        report = (run_dir / "evolution_report.md").read_text()
        assert "(+" in report

    def test_report_git_timeout(self, tmp_path: Path):
        """Git log timeout is handled gracefully."""
        project_dir = tmp_path / "proj"
        project_dir.mkdir()
        runs_dir = project_dir / "runs"
        runs_dir.mkdir()
        run_dir = runs_dir / "session"
        run_dir.mkdir()
        (runs_dir / "improvements.md").write_text("# Improvements\n")

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
