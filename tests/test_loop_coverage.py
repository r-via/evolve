import evolve.infrastructure.claude_sdk.runtime as _rt_mod
"""Coverage tests for loop.py — run_single_round, evolve_loop, _run_rounds."""

import subprocess
import sys
import textwrap
from pathlib import Path
from unittest.mock import patch, MagicMock, call

import pytest

from evolve.infrastructure.git.adapter import (
    _ensure_git,
    _git_commit,
)
from evolve.application.run_loop import (
    MAX_DEBUG_RETRIES,
    _MEMORY_COMPACTION_MARKER,
    _MEMORY_WIPE_THRESHOLD,
    _run_rounds,
)
from evolve.application.run_loop_startup import evolve_loop
from evolve.application.run_round import run_single_round
from evolve.infrastructure.reporting.generator import _generate_evolution_report
from evolve.infrastructure.claude_sdk.party import _run_party_mode
from evolve.infrastructure.filesystem.improvement_parser import (
    _count_checked,
    _count_unchecked,
    _get_current_improvement,
)
from evolve.infrastructure.filesystem.state_manager import _parse_restart_required


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

        with patch("evolve.application.run_loop.subprocess.run", return_value=mock_subprocess), \
             patch("evolve.infrastructure.claude_sdk.runtime.asyncio.run", side_effect=_close_coro), \
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

        with patch("evolve.application.run_loop.subprocess.run", return_value=MagicMock(returncode=0, stdout="", stderr="")), \
             patch("evolve.infrastructure.claude_sdk.runtime.asyncio.run", side_effect=_close_coro), \
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

        with patch("evolve.application.run_loop.subprocess.run", return_value=MagicMock(returncode=0, stdout="ok", stderr="")), \
             patch("evolve.infrastructure.claude_sdk.runtime.asyncio.run", side_effect=_close_coro), \
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

        with patch("evolve.application.run_loop.subprocess.run", side_effect=mock_run), \
             patch("evolve.infrastructure.claude_sdk.runtime.asyncio.run", side_effect=_close_coro), \
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
        import evolve.infrastructure.claude_sdk.agent as _agent_mod
        with patch("evolve.application.run_loop.subprocess.run", side_effect=mock_run), \
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

        import evolve.infrastructure.claude_sdk.runtime as agent_mod
        original_model = _rt_mod.MODEL

        with patch("evolve.application.run_loop.subprocess.run", return_value=MagicMock(returncode=0, stdout="", stderr="")), \
             patch("evolve.infrastructure.claude_sdk.runtime.asyncio.run", side_effect=_close_coro), \
             patch.dict("sys.modules", {"claude_agent_sdk": MagicMock()}):
            run_single_round(
                tmp_path, round_num=1,
                run_dir=run_dir, model="claude-sonnet-4-20250514",
            )
            assert _rt_mod.MODEL == "claude-sonnet-4-20250514"

        _rt_mod.MODEL = original_model

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

        with patch("evolve.application.run_loop.subprocess.run", side_effect=mock_run), \
             patch("evolve.infrastructure.claude_sdk.runtime.asyncio.run", side_effect=_close_coro), \
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

        with patch("evolve.application.run_loop.subprocess.run", return_value=MagicMock(returncode=0, stdout="", stderr="")), \
             patch("evolve.infrastructure.claude_sdk.runtime.asyncio.run", side_effect=_close_coro), \
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

        with patch("evolve.application.run_loop._ensure_git"), \
             patch("evolve.application.run_loop._ensure_runs_layout"), \
             patch("evolve.application.run_loop._run_rounds") as mock_run:
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

        with patch("evolve.application.run_loop._ensure_git"), \
             patch("evolve.application.run_loop._run_rounds") as mock_run:
            evolve_loop(tmp_path, max_rounds=10, resume=True)

        mock_run.assert_called_once()
        args = mock_run.call_args
        assert args[0][4] == 3  # start_round

    def test_resume_no_sessions_starts_fresh(self, tmp_path: Path):
        """Resume with no sessions starts a fresh session."""
        (tmp_path / "README.md").write_text("# Test")
        (tmp_path / "runs").mkdir()

        with patch("evolve.application.run_loop._ensure_git"), \
             patch("evolve.application.run_loop._run_rounds") as mock_run:
            evolve_loop(tmp_path, max_rounds=5, resume=True)

        mock_run.assert_called_once()
        args = mock_run.call_args
        assert args[0][4] == 1

    def test_forever_mode_creates_branch(self, tmp_path: Path):
        """Forever mode calls _setup_forever_branch and sets high max_rounds."""
        (tmp_path / "README.md").write_text("# Test")
        (tmp_path / "runs").mkdir()

        with patch("evolve.application.run_loop._ensure_git"), \
             patch("evolve.application.run_loop._setup_forever_branch") as mock_branch, \
             patch("evolve.application.run_loop._run_rounds") as mock_run:
            evolve_loop(tmp_path, max_rounds=10, forever=True)

        mock_branch.assert_called_once_with(tmp_path)
        mock_run.assert_called_once()
        args = mock_run.call_args
        assert args[0][5] == 999999

    def test_resume_no_runs_dir(self, tmp_path: Path):
        """Resume when runs/ doesn't exist starts fresh."""
        (tmp_path / "README.md").write_text("# Test")

        with patch("evolve.application.run_loop._ensure_git"), \
             patch("evolve.application.run_loop._run_rounds") as mock_run:
            evolve_loop(tmp_path, max_rounds=5, resume=True)

        mock_run.assert_called_once()

    def test_resume_session_no_convos(self, tmp_path: Path):
        """Resume with session but no conversation logs starts from round 1."""
        (tmp_path / "README.md").write_text("# Test")
        runs = tmp_path / "runs"
        session = runs / "20260101_000000"
        session.mkdir(parents=True)
        (runs / "improvements.md").write_text("# Improvements\n")

        with patch("evolve.application.run_loop._ensure_git"), \
             patch("evolve.application.run_loop._run_rounds") as mock_run:
            evolve_loop(tmp_path, max_rounds=5, resume=True)

        mock_run.assert_called_once()
        args = mock_run.call_args
        assert args[0][4] == 1


# ---------------------------------------------------------------------------
# _run_rounds — convergence, max rounds, debug retries
# ---------------------------------------------------------------------------

class TestRunRounds:
    """Test _run_rounds orchestration logic (lines 414-569)."""

    def setup_method(self):
        """Fresh UI mock for each test — avoids per-test MagicMock() boilerplate."""
        self.ui = MagicMock()

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
        ui = self.ui

        def mock_monitored(cmd, cwd, ui_, round_num, watchdog_timeout=120):
            convo = run_dir / f"conversation_loop_{round_num}.md"
            convo.write_text("# Round conversation")
            (run_dir / "CONVERGED").write_text("All done")
            imp_path.write_text("- [x] [functional] do something\n")
            return 0, "output", False

        with patch("evolve.application.run_loop._run_monitored_subprocess", side_effect=mock_monitored), \
             patch("evolve.application.run_loop._is_self_evolving", return_value=True), \
             patch("evolve.infrastructure.reporting.generator._generate_evolution_report"), \
             patch("evolve.application.run_loop._run_party_mode"), \
             pytest.raises(SystemExit) as exc:
            _run_rounds(
                project_dir, run_dir, imp_path, ui,
                start_round=1, max_rounds=10, check_cmd="pytest",
                allow_installs=False, timeout=300, model="claude-opus-4-6",
            )
        assert exc.value.code == 0

    def test_max_rounds_exits_1(self, tmp_path: Path):
        """When max rounds reached without convergence, exits with code 1."""
        project_dir, run_dir, imp_path = self._setup_project(tmp_path)
        ui = self.ui

        def mock_monitored(cmd, cwd, ui_, round_num, watchdog_timeout=120):
            convo = run_dir / f"conversation_loop_{round_num}.md"
            convo.write_text("# Round conversation")
            # Simulate progress by adding a new unchecked item each round
            existing = imp_path.read_text()
            imp_path.write_text(existing + f"- [ ] [functional] new item {round_num}\n")
            return 0, "output", False

        with patch("evolve.application.run_loop._run_monitored_subprocess", side_effect=mock_monitored), \
             patch("evolve.application.run_loop._is_self_evolving", return_value=True), \
             patch("evolve.infrastructure.reporting.generator._generate_evolution_report"), \
             pytest.raises(SystemExit) as exc:
            _run_rounds(
                project_dir, run_dir, imp_path, ui,
                start_round=1, max_rounds=2, check_cmd="pytest",
                allow_installs=False, timeout=300, model="claude-opus-4-6",
            )
        assert exc.value.code == 1

    def test_stalled_round_exhausts_retries(self, tmp_path: Path):
        """Three identical stalls trip the circuit breaker → exit 4
        (SPEC § "Circuit breakers": homogeneous failures fire exit 4
        for deterministic-loop detection, not exit 2)."""
        project_dir, run_dir, imp_path = self._setup_project(tmp_path)
        ui = self.ui

        def mock_monitored(cmd, cwd, ui_, round_num, watchdog_timeout=120):
            return 0, "output", True  # always stalls

        with patch("evolve.application.run_loop._run_monitored_subprocess", side_effect=mock_monitored), \
             patch("evolve.application.run_loop._is_self_evolving", return_value=True), \
             patch("evolve.application.run_loop._save_subprocess_diagnostic"), \
             patch("evolve.infrastructure.reporting.generator._generate_evolution_report"), \
             pytest.raises(SystemExit) as exc:
            _run_rounds(
                project_dir, run_dir, imp_path, ui,
                start_round=1, max_rounds=1, check_cmd="pytest",
                allow_installs=False, timeout=300, model="claude-opus-4-6",
            )
        assert exc.value.code == 4  # circuit breaker — identical signatures across attempts

    def test_crashed_round_retries_then_succeeds(self, tmp_path: Path):
        """Crashed subprocess recovers on retry."""
        project_dir, run_dir, imp_path = self._setup_project(tmp_path)
        ui = self.ui

        call_count = 0

        def mock_monitored(cmd, cwd, ui_, round_num, watchdog_timeout=120):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return 1, "crash output", False
            convo = run_dir / f"conversation_loop_{round_num}.md"
            convo.write_text("# Recovered")
            return 0, "output", False

        with patch("evolve.application.run_loop._run_monitored_subprocess", side_effect=mock_monitored), \
             patch("evolve.application.run_loop._is_self_evolving", return_value=True), \
             patch("evolve.application.run_loop._save_subprocess_diagnostic"), \
             patch("evolve.infrastructure.reporting.generator._generate_evolution_report"), \
             pytest.raises(SystemExit):
            _run_rounds(
                project_dir, run_dir, imp_path, ui,
                start_round=1, max_rounds=1, check_cmd="pytest",
                allow_installs=False, timeout=300, model="claude-opus-4-6",
            )

    def test_no_progress_triggers_retry(self, tmp_path: Path):
        """Subprocess succeeds but makes no progress triggers retry."""
        project_dir, run_dir, imp_path = self._setup_project(tmp_path)
        ui = self.ui

        def mock_monitored(cmd, cwd, ui_, round_num, watchdog_timeout=120):
            return 0, "output", False

        with patch("evolve.application.run_loop._run_monitored_subprocess", side_effect=mock_monitored), \
             patch("evolve.application.run_loop._is_self_evolving", return_value=True), \
             patch("evolve.application.run_loop._save_subprocess_diagnostic"), \
             patch("evolve.infrastructure.reporting.generator._generate_evolution_report"), \
             pytest.raises(SystemExit) as exc:
            _run_rounds(
                project_dir, run_dir, imp_path, ui,
                start_round=1, max_rounds=1, check_cmd=None,
                allow_installs=False, timeout=300, model="claude-opus-4-6",
            )
        assert exc.value.code == 4  # circuit breaker — identical signatures across attempts
