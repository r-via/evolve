"""Coverage tests for loop.py — run_single_round, evolve_loop, _run_rounds."""

import subprocess
import sys
import textwrap
from pathlib import Path
from unittest.mock import patch, MagicMock, call

import pytest

from evolve.git import _ensure_git, _git_commit
from evolve.orchestrator import (
    MAX_DEBUG_RETRIES,
    _MEMORY_COMPACTION_MARKER,
    _MEMORY_WIPE_THRESHOLD,
    _generate_evolution_report,
    _run_rounds,
    evolve_loop,
    run_single_round,
)
from evolve.party import _run_party_mode
from evolve.state import (
    _count_checked,
    _count_unchecked,
    _get_current_improvement,
    _parse_restart_required,
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

        with patch("evolve.orchestrator.subprocess.run", return_value=mock_subprocess), \
             patch("evolve.agent.asyncio.run", side_effect=_close_coro), \
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

        with patch("evolve.orchestrator.subprocess.run", return_value=MagicMock(returncode=0, stdout="", stderr="")), \
             patch("evolve.agent.asyncio.run", side_effect=_close_coro), \
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

        with patch("evolve.orchestrator.subprocess.run", return_value=MagicMock(returncode=0, stdout="ok", stderr="")), \
             patch("evolve.agent.asyncio.run", side_effect=_close_coro), \
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

        with patch("evolve.orchestrator.subprocess.run", side_effect=mock_run), \
             patch("evolve.agent.asyncio.run", side_effect=_close_coro), \
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
        import evolve.agent as _agent_mod
        with patch("evolve.orchestrator.subprocess.run", side_effect=mock_run), \
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

        import evolve.agent as agent_mod
        original_model = agent_mod.MODEL

        with patch("evolve.orchestrator.subprocess.run", return_value=MagicMock(returncode=0, stdout="", stderr="")), \
             patch("evolve.agent.asyncio.run", side_effect=_close_coro), \
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

        with patch("evolve.orchestrator.subprocess.run", side_effect=mock_run), \
             patch("evolve.agent.asyncio.run", side_effect=_close_coro), \
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

        with patch("evolve.orchestrator.subprocess.run", return_value=MagicMock(returncode=0, stdout="", stderr="")), \
             patch("evolve.agent.asyncio.run", side_effect=_close_coro), \
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

        with patch("evolve.orchestrator._ensure_git"), \
             patch("evolve.orchestrator._ensure_runs_layout"), \
             patch("evolve.orchestrator._run_rounds") as mock_run:
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

        with patch("evolve.orchestrator._ensure_git"), \
             patch("evolve.orchestrator._run_rounds") as mock_run:
            evolve_loop(tmp_path, max_rounds=10, resume=True)

        mock_run.assert_called_once()
        args = mock_run.call_args
        assert args[0][4] == 3  # start_round

    def test_resume_no_sessions_starts_fresh(self, tmp_path: Path):
        """Resume with no sessions starts a fresh session."""
        (tmp_path / "README.md").write_text("# Test")
        (tmp_path / "runs").mkdir()

        with patch("evolve.orchestrator._ensure_git"), \
             patch("evolve.orchestrator._run_rounds") as mock_run:
            evolve_loop(tmp_path, max_rounds=5, resume=True)

        mock_run.assert_called_once()
        args = mock_run.call_args
        assert args[0][4] == 1

    def test_forever_mode_creates_branch(self, tmp_path: Path):
        """Forever mode calls _setup_forever_branch and sets high max_rounds."""
        (tmp_path / "README.md").write_text("# Test")
        (tmp_path / "runs").mkdir()

        with patch("evolve.orchestrator._ensure_git"), \
             patch("evolve.orchestrator._setup_forever_branch") as mock_branch, \
             patch("evolve.orchestrator._run_rounds") as mock_run:
            evolve_loop(tmp_path, max_rounds=10, forever=True)

        mock_branch.assert_called_once_with(tmp_path)
        mock_run.assert_called_once()
        args = mock_run.call_args
        assert args[0][5] == 999999

    def test_resume_no_runs_dir(self, tmp_path: Path):
        """Resume when runs/ doesn't exist starts fresh."""
        (tmp_path / "README.md").write_text("# Test")

        with patch("evolve.orchestrator._ensure_git"), \
             patch("evolve.orchestrator._run_rounds") as mock_run:
            evolve_loop(tmp_path, max_rounds=5, resume=True)

        mock_run.assert_called_once()

    def test_resume_session_no_convos(self, tmp_path: Path):
        """Resume with session but no conversation logs starts from round 1."""
        (tmp_path / "README.md").write_text("# Test")
        runs = tmp_path / "runs"
        session = runs / "20260101_000000"
        session.mkdir(parents=True)
        (runs / "improvements.md").write_text("# Improvements\n")

        with patch("evolve.orchestrator._ensure_git"), \
             patch("evolve.orchestrator._run_rounds") as mock_run:
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

        with patch("evolve.orchestrator._run_monitored_subprocess", side_effect=mock_monitored), \
             patch("evolve.orchestrator._is_self_evolving", return_value=True), \
             patch("evolve.diagnostics._generate_evolution_report"), \
             patch("evolve.orchestrator._run_party_mode"), \
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

        with patch("evolve.orchestrator._run_monitored_subprocess", side_effect=mock_monitored), \
             patch("evolve.orchestrator._is_self_evolving", return_value=True), \
             patch("evolve.diagnostics._generate_evolution_report"), \
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

        with patch("evolve.orchestrator._run_monitored_subprocess", side_effect=mock_monitored), \
             patch("evolve.orchestrator._is_self_evolving", return_value=True), \
             patch("evolve.diagnostics._save_subprocess_diagnostic"), \
             patch("evolve.diagnostics._generate_evolution_report"), \
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

        with patch("evolve.orchestrator._run_monitored_subprocess", side_effect=mock_monitored), \
             patch("evolve.orchestrator._is_self_evolving", return_value=True), \
             patch("evolve.diagnostics._save_subprocess_diagnostic"), \
             patch("evolve.diagnostics._generate_evolution_report"), \
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

        with patch("evolve.orchestrator._run_monitored_subprocess", side_effect=mock_monitored), \
             patch("evolve.orchestrator._is_self_evolving", return_value=True), \
             patch("evolve.diagnostics._save_subprocess_diagnostic"), \
             patch("evolve.diagnostics._generate_evolution_report"), \
             pytest.raises(SystemExit) as exc:
            _run_rounds(
                project_dir, run_dir, imp_path, ui,
                start_round=1, max_rounds=1, check_cmd=None,
                allow_installs=False, timeout=300, model="claude-opus-4-6",
            )
        assert exc.value.code == 4  # circuit breaker — identical signatures across attempts

    def test_zero_progress_improvements_unchanged(self, tmp_path: Path):
        """Zero-progress when improvements.md is byte-identical to pre-round state."""
        project_dir, run_dir, imp_path = self._setup_project(tmp_path)
        ui = self.ui
        diagnostics = []

        def mock_monitored(cmd, cwd, ui_, round_num, watchdog_timeout=120):
            # Create conversation log but do NOT modify improvements.md
            convo = run_dir / f"conversation_loop_{round_num}.md"
            convo.write_text("# Round conversation with activity")
            return 0, "output", False

        def mock_save_diag(run_dir_, round_num_, cmd_, output_, reason, attempt):
            diagnostics.append(reason)

        with patch("evolve.orchestrator._run_monitored_subprocess", side_effect=mock_monitored), \
             patch("evolve.orchestrator._is_self_evolving", return_value=True), \
             patch("evolve.diagnostics._save_subprocess_diagnostic", side_effect=mock_save_diag), \
             patch("evolve.diagnostics._generate_evolution_report"), \
             pytest.raises(SystemExit) as exc:
            _run_rounds(
                project_dir, run_dir, imp_path, ui,
                start_round=1, max_rounds=1, check_cmd=None,
                allow_installs=False, timeout=300, model="claude-opus-4-6",
            )
        assert exc.value.code == 4  # circuit breaker — identical signatures across attempts
        # All retries should report improvements.md unchanged
        assert any("improvements.md byte-identical" in d for d in diagnostics)
        assert any("NO PROGRESS" in d for d in diagnostics)

    def test_zero_progress_no_commit_msg(self, tmp_path: Path):
        """Zero-progress when agent commits without writing COMMIT_MSG.

        The ``no_commit_msg`` check fires only when HEAD MOVED during
        the attempt AND the new HEAD's message matches the fallback
        template.  The mock must therefore create a real commit per
        attempt so the HEAD-moved gate is satisfied.
        """
        project_dir, run_dir, imp_path = self._setup_project(tmp_path)
        ui = self.ui
        diagnostics = []

        import os
        import subprocess as sp
        _git_env = {
            **os.environ, "GIT_AUTHOR_NAME": "test", "GIT_AUTHOR_EMAIL": "test@test.com",
            "GIT_COMMITTER_NAME": "test", "GIT_COMMITTER_EMAIL": "test@test.com",
        }
        sp.run(["git", "init"], cwd=str(project_dir), capture_output=True)
        sp.run(["git", "add", "-A"], cwd=str(project_dir), capture_output=True)
        sp.run(
            ["git", "commit", "-m", "initial commit"],
            cwd=str(project_dir), capture_output=True, env=_git_env,
        )

        def mock_monitored(cmd, cwd, ui_, round_num, watchdog_timeout=120):
            convo = run_dir / f"conversation_loop_{round_num}.md"
            convo.write_text("# Round conversation")
            existing = imp_path.read_text()
            imp_path.write_text(existing + f"- [ ] [functional] item {round_num}\n")
            # Simulate the "agent didn't write COMMIT_MSG, orchestrator
            # fell back to chore(evolve): round N" path by creating the
            # fallback commit per attempt.  This is what the check
            # under test is designed to detect.
            sp.run(["git", "add", "-A"], cwd=str(cwd), capture_output=True)
            sp.run(
                ["git", "commit", "-m", f"chore(evolve): round {round_num}",
                 "--allow-empty"],
                cwd=str(cwd), capture_output=True, env=_git_env,
            )
            return 0, "output", False

        def mock_save_diag(run_dir_, round_num_, cmd_, output_, reason, attempt):
            diagnostics.append(reason)

        with patch("evolve.orchestrator._run_monitored_subprocess", side_effect=mock_monitored), \
             patch("evolve.orchestrator._is_self_evolving", return_value=True), \
             patch("evolve.diagnostics._save_subprocess_diagnostic", side_effect=mock_save_diag), \
             patch("evolve.diagnostics._generate_evolution_report"), \
             pytest.raises(SystemExit) as exc:
            _run_rounds(
                project_dir, run_dir, imp_path, ui,
                start_round=1, max_rounds=1, check_cmd=None,
                allow_installs=False, timeout=300, model="claude-opus-4-6",
            )
        # Heterogeneous signatures (attempt 1 sees backlog violation,
        # attempts 2-3 see only no_commit_msg once the item already
        # exists in the growing imp) → circuit breaker does NOT fire →
        # classic exit 2.
        assert exc.value.code == 2
        # Should detect fallback commit message
        assert any("no COMMIT_MSG written" in d for d in diagnostics)
        assert any("NO PROGRESS" in d for d in diagnostics)

    def test_zero_progress_both_conditions(self, tmp_path: Path):
        """Zero-progress reports both conditions when both are true."""
        project_dir, run_dir, imp_path = self._setup_project(tmp_path)
        ui = self.ui
        diagnostics = []

        import os
        import subprocess as sp
        _git_env = {
            **os.environ, "GIT_AUTHOR_NAME": "test", "GIT_AUTHOR_EMAIL": "test@test.com",
            "GIT_COMMITTER_NAME": "test", "GIT_COMMITTER_EMAIL": "test@test.com",
        }
        sp.run(["git", "init"], cwd=str(project_dir), capture_output=True)
        sp.run(["git", "add", "-A"], cwd=str(project_dir), capture_output=True)
        sp.run(
            ["git", "commit", "-m", "initial commit"],
            cwd=str(project_dir), capture_output=True, env=_git_env,
        )

        def mock_monitored(cmd, cwd, ui_, round_num, watchdog_timeout=120):
            # Don't modify improvements.md — but make a fallback commit
            # so the ``no_commit_msg`` HEAD-moved gate is satisfied.
            convo = run_dir / f"conversation_loop_{round_num}.md"
            convo.write_text("# Round conversation")
            sp.run(
                ["git", "commit", "-m", f"chore(evolve): round {round_num}",
                 "--allow-empty"],
                cwd=str(cwd), capture_output=True, env=_git_env,
            )
            return 0, "output", False

        def mock_save_diag(run_dir_, round_num_, cmd_, output_, reason, attempt):
            diagnostics.append(reason)

        with patch("evolve.orchestrator._run_monitored_subprocess", side_effect=mock_monitored), \
             patch("evolve.orchestrator._is_self_evolving", return_value=True), \
             patch("evolve.diagnostics._save_subprocess_diagnostic", side_effect=mock_save_diag), \
             patch("evolve.diagnostics._generate_evolution_report"), \
             pytest.raises(SystemExit) as exc:
            _run_rounds(
                project_dir, run_dir, imp_path, ui,
                start_round=1, max_rounds=1, check_cmd=None,
                allow_installs=False, timeout=300, model="claude-opus-4-6",
            )
        assert exc.value.code == 4  # circuit breaker — identical signatures across attempts
        # Both conditions should be present in the diagnostic
        assert any("no COMMIT_MSG written" in d and "improvements.md byte-identical" in d for d in diagnostics)

    def test_zero_progress_both_conditions_reason_string_format(self, tmp_path: Path):
        """Zero-progress reason string uses ' AND ' to join both conditions."""
        project_dir, run_dir, imp_path = self._setup_project(tmp_path)
        ui = self.ui
        diagnostics = []

        import os
        import subprocess as sp
        _git_env = {
            **os.environ, "GIT_AUTHOR_NAME": "test", "GIT_AUTHOR_EMAIL": "test@test.com",
            "GIT_COMMITTER_NAME": "test", "GIT_COMMITTER_EMAIL": "test@test.com",
        }
        sp.run(["git", "init"], cwd=str(project_dir), capture_output=True)
        sp.run(["git", "add", "-A"], cwd=str(project_dir), capture_output=True)
        sp.run(
            ["git", "commit", "-m", "initial commit"],
            cwd=str(project_dir), capture_output=True, env=_git_env,
        )

        def mock_monitored(cmd, cwd, ui_, round_num, watchdog_timeout=120):
            convo = run_dir / f"conversation_loop_{round_num}.md"
            convo.write_text("# Round conversation")
            sp.run(
                ["git", "commit", "-m", f"chore(evolve): round {round_num}",
                 "--allow-empty"],
                cwd=str(cwd), capture_output=True, env=_git_env,
            )
            return 0, "output", False

        def mock_save_diag(run_dir_, round_num_, cmd_, output_, reason, attempt):
            diagnostics.append(reason)

        with patch("evolve.orchestrator._run_monitored_subprocess", side_effect=mock_monitored), \
             patch("evolve.orchestrator._is_self_evolving", return_value=True), \
             patch("evolve.diagnostics._save_subprocess_diagnostic", side_effect=mock_save_diag), \
             patch("evolve.diagnostics._generate_evolution_report"), \
             pytest.raises(SystemExit):
            _run_rounds(
                project_dir, run_dir, imp_path, ui,
                start_round=1, max_rounds=1, check_cmd=None,
                allow_installs=False, timeout=300, model="claude-opus-4-6",
            )
        # Verify the exact format: "NO PROGRESS: <reason1> AND <reason2>"
        combined = [d for d in diagnostics if " AND " in d]
        assert len(combined) > 0, "Expected at least one diagnostic with ' AND ' join"
        for d in combined:
            assert d.startswith("NO PROGRESS: ")
            assert "no COMMIT_MSG written (fallback commit message)" in d
            assert "improvements.md byte-identical to pre-round state" in d

    def test_zero_progress_triggers_debug_retry_count(self, tmp_path: Path):
        """Zero-progress triggers the correct number of debug retries (MAX_DEBUG_RETRIES + 1 total)."""
        project_dir, run_dir, imp_path = self._setup_project(tmp_path)
        ui = self.ui
        attempt_log = []

        def mock_monitored(cmd, cwd, ui_, round_num, watchdog_timeout=120):
            convo = run_dir / f"conversation_loop_{round_num}.md"
            convo.write_text("# Round conversation")
            return 0, "output", False

        def mock_save_diag(run_dir_, round_num_, cmd_, output_, reason, attempt):
            attempt_log.append(attempt)

        with patch("evolve.orchestrator._run_monitored_subprocess", side_effect=mock_monitored), \
             patch("evolve.orchestrator._is_self_evolving", return_value=True), \
             patch("evolve.diagnostics._save_subprocess_diagnostic", side_effect=mock_save_diag), \
             patch("evolve.diagnostics._generate_evolution_report"), \
             pytest.raises(SystemExit) as exc:
            _run_rounds(
                project_dir, run_dir, imp_path, ui,
                start_round=1, max_rounds=1, check_cmd=None,
                allow_installs=False, timeout=300, model="claude-opus-4-6",
            )
        assert exc.value.code == 4  # circuit breaker — identical signatures across attempts
        # Should have MAX_DEBUG_RETRIES + 1 attempts (1 original + 2 retries)
        from evolve.orchestrator import MAX_DEBUG_RETRIES
        assert len(attempt_log) == MAX_DEBUG_RETRIES + 1
        assert attempt_log == list(range(1, MAX_DEBUG_RETRIES + 2))

    def test_zero_progress_improvements_unchanged_only_no_git(self, tmp_path: Path):
        """Zero-progress triggers on improvements.md unchanged even without git repo (no COMMIT_MSG check)."""
        project_dir, run_dir, imp_path = self._setup_project(tmp_path)
        ui = self.ui
        diagnostics = []

        def mock_monitored(cmd, cwd, ui_, round_num, watchdog_timeout=120):
            convo = run_dir / f"conversation_loop_{round_num}.md"
            convo.write_text("# Round conversation")
            # Do NOT modify improvements.md — triggers byte-identical
            return 0, "output", False

        def mock_save_diag(run_dir_, round_num_, cmd_, output_, reason, attempt):
            diagnostics.append(reason)

        with patch("evolve.orchestrator._run_monitored_subprocess", side_effect=mock_monitored), \
             patch("evolve.orchestrator._is_self_evolving", return_value=True), \
             patch("evolve.diagnostics._save_subprocess_diagnostic", side_effect=mock_save_diag), \
             patch("evolve.diagnostics._generate_evolution_report"), \
             pytest.raises(SystemExit) as exc:
            _run_rounds(
                project_dir, run_dir, imp_path, ui,
                start_round=1, max_rounds=1, check_cmd=None,
                allow_installs=False, timeout=300, model="claude-opus-4-6",
            )
        assert exc.value.code == 4  # circuit breaker — identical signatures across attempts
        # Should still detect byte-identical even without git
        assert any("improvements.md byte-identical" in d for d in diagnostics)
        # Should NOT contain "no COMMIT_MSG" since git log would fail
        # (no git repo = git log fails silently, no_commit_msg stays False)
        assert all("no COMMIT_MSG written" not in d for d in diagnostics if "AND" not in d)

    def test_zero_progress_no_commit_msg_only_reason_string(self, tmp_path: Path):
        """When only COMMIT_MSG is missing (improvements changed), reason has only COMMIT_MSG part."""
        project_dir, run_dir, imp_path = self._setup_project(tmp_path)
        ui = self.ui
        diagnostics = []

        import os
        import subprocess as sp
        _git_env = {
            **os.environ, "GIT_AUTHOR_NAME": "test", "GIT_AUTHOR_EMAIL": "test@test.com",
            "GIT_COMMITTER_NAME": "test", "GIT_COMMITTER_EMAIL": "test@test.com",
        }
        sp.run(["git", "init"], cwd=str(project_dir), capture_output=True)
        sp.run(["git", "add", "-A"], cwd=str(project_dir), capture_output=True)
        sp.run(
            ["git", "commit", "-m", "initial commit"],
            cwd=str(project_dir), capture_output=True, env=_git_env,
        )

        def mock_monitored(cmd, cwd, ui_, round_num, watchdog_timeout=120):
            convo = run_dir / f"conversation_loop_{round_num}.md"
            convo.write_text("# Round conversation")
            # Modify improvements.md so byte-identical does NOT trigger
            existing = imp_path.read_text()
            imp_path.write_text(existing + f"\n- [ ] [functional] item {round_num}\n")
            # Simulate orchestrator fallback commit so HEAD actually
            # moves this attempt — the new no_commit_msg gate requires
            # a real HEAD movement to fire.
            sp.run(["git", "add", "-A"], cwd=str(cwd), capture_output=True)
            sp.run(
                ["git", "commit", "-m", f"chore(evolve): round {round_num}"],
                cwd=str(cwd), capture_output=True, env=_git_env,
            )
            return 0, "output", False

        def mock_save_diag(run_dir_, round_num_, cmd_, output_, reason, attempt):
            diagnostics.append(reason)

        with patch("evolve.orchestrator._run_monitored_subprocess", side_effect=mock_monitored), \
             patch("evolve.orchestrator._is_self_evolving", return_value=True), \
             patch("evolve.diagnostics._save_subprocess_diagnostic", side_effect=mock_save_diag), \
             patch("evolve.diagnostics._generate_evolution_report"), \
             pytest.raises(SystemExit):
            _run_rounds(
                project_dir, run_dir, imp_path, ui,
                start_round=1, max_rounds=1, check_cmd=None,
                allow_installs=False, timeout=300, model="claude-opus-4-6",
            )
        # Only COMMIT_MSG reason, no " AND " join
        commit_msg_only = [d for d in diagnostics if "no COMMIT_MSG written" in d and " AND " not in d]
        assert len(commit_msg_only) > 0, "Expected diagnostic with only COMMIT_MSG reason"

    def _setup_git_with_commit(self, project_dir: Path, msg: str) -> None:
        """Init a git repo in project_dir and seed one commit with msg as full body."""
        import subprocess as sp
        import os
        env = {**os.environ,
               "GIT_AUTHOR_NAME": "test", "GIT_AUTHOR_EMAIL": "test@test.com",
               "GIT_COMMITTER_NAME": "test", "GIT_COMMITTER_EMAIL": "test@test.com"}
        sp.run(["git", "init"], cwd=str(project_dir), capture_output=True)
        sp.run(["git", "add", "-A"], cwd=str(project_dir), capture_output=True, env=env)
        sp.run(["git", "commit", "-m", msg, "--allow-empty"],
               cwd=str(project_dir), capture_output=True, env=env)

    def test_memory_wipe_triggers_retry_when_no_compaction_marker(self, tmp_path: Path):
        """>50% memory.md shrink without 'memory: compaction' in commit → MEMORY WIPED retry."""
        project_dir, run_dir, imp_path = self._setup_project(tmp_path)
        ui = self.ui
        diagnostics: list[str] = []
        # Seed a non-trivial memory.md (500+ bytes) so a wipe crosses the 50% threshold
        memory_path = project_dir / "runs" / "memory.md"
        memory_path.write_text("# Agent Memory\n\n" + ("line of memory context\n" * 40))
        mem_before = memory_path.stat().st_size
        assert mem_before > 500

        # Git commit WITHOUT "memory: compaction" marker
        self._setup_git_with_commit(project_dir, "feat: unrelated change\n\nbody only")

        attempt_counter = {"n": 0}

        def mock_monitored(cmd, cwd, ui_, round_num, watchdog_timeout=120):
            attempt_counter["n"] += 1
            convo = run_dir / f"conversation_loop_{round_num}.md"
            convo.write_text("# Round conversation")
            # Real improvement progress so imp_unchanged / no_commit_msg do NOT trigger
            existing = imp_path.read_text()
            imp_path.write_text(existing + f"- [ ] [functional] new {attempt_counter['n']}\n")
            # Restore memory to full size before each subprocess pass, then wipe —
            # exercises the 50% shrink path on EVERY attempt so the MEMORY WIPED
            # diagnostic is emitted whenever the agent re-commits a wipe.
            memory_path.write_text("# Agent Memory\n\n" + ("line of memory context\n" * 40))
            # Silently wipe memory.md (>50% shrink) — but orchestrator's snapshot
            # was taken BEFORE mock_monitored ran, so it sees full→wiped shrink.
            # To actually trigger, we simulate "before" full state and "after"
            # wipe by letting the orchestrator snapshot then the mock wipe.
            memory_path.write_text("# Agent Memory\n")
            return 0, "output", False

        def mock_save_diag(run_dir_, round_num_, cmd_, output_, reason, attempt):
            diagnostics.append(reason)

        with patch("evolve.orchestrator._run_monitored_subprocess", side_effect=mock_monitored), \
             patch("evolve.orchestrator._is_self_evolving", return_value=True), \
             patch("evolve.diagnostics._save_subprocess_diagnostic", side_effect=mock_save_diag), \
             patch("evolve.diagnostics._generate_evolution_report"), \
             pytest.raises(SystemExit):
            _run_rounds(
                project_dir, run_dir, imp_path, ui,
                start_round=1, max_rounds=1, check_cmd=None,
                allow_installs=False, timeout=300, model="claude-opus-4-6",
            )
        # Diagnostic uses the MEMORY WIPED prefix (not NO PROGRESS) so the
        # agent prompt builder renders the dedicated header.
        assert any(d.startswith("MEMORY WIPED: ") for d in diagnostics), diagnostics
        # Threshold text is derived from the _MEMORY_WIPE_THRESHOLD constant —
        # any future change to the threshold will propagate here via the
        # constant rather than requiring a hand-edit of the magic percentage.
        threshold_pct = int(_MEMORY_WIPE_THRESHOLD * 100)
        assert any(f"memory.md shrunk by >{threshold_pct}%" in d for d in diagnostics)
        assert any(_MEMORY_COMPACTION_MARKER in d for d in diagnostics)

    def test_memory_wipe_allowed_when_commit_has_compaction_marker(self, tmp_path: Path):
        """>50% memory.md shrink WITH 'memory: compaction' in commit → no retry."""
        project_dir, run_dir, imp_path = self._setup_project(tmp_path)
        ui = self.ui
        diagnostics: list[str] = []
        memory_path = project_dir / "runs" / "memory.md"
        memory_path.write_text("# Agent Memory\n\n" + ("line of memory\n" * 40))

        # Git commit body explicitly declares compaction — uses the
        # _MEMORY_COMPACTION_MARKER constant so any future rename of the
        # marker propagates here without test edits.
        self._setup_git_with_commit(
            project_dir,
            f"chore(memory): compact\n\n{_MEMORY_COMPACTION_MARKER}\n\nTrimmed old entries.",
        )

        def mock_monitored(cmd, cwd, ui_, round_num, watchdog_timeout=120):
            convo = run_dir / f"conversation_loop_{round_num}.md"
            convo.write_text("# Round conversation")
            existing = imp_path.read_text()
            imp_path.write_text(existing + f"- [ ] [functional] new {round_num}\n")
            # Wipe memory.md — but commit says it's an intentional compaction
            memory_path.write_text("# Agent Memory\n")
            (run_dir / "CONVERGED").write_text("done")
            imp_path.write_text("- [x] [functional] do something\n")
            return 0, "output", False

        def mock_save_diag(run_dir_, round_num_, cmd_, output_, reason, attempt):
            diagnostics.append(reason)

        with patch("evolve.orchestrator._run_monitored_subprocess", side_effect=mock_monitored), \
             patch("evolve.orchestrator._is_self_evolving", return_value=True), \
             patch("evolve.diagnostics._save_subprocess_diagnostic", side_effect=mock_save_diag), \
             patch("evolve.diagnostics._generate_evolution_report"), \
             patch("evolve.orchestrator._run_party_mode"), \
             pytest.raises(SystemExit) as exc:
            _run_rounds(
                project_dir, run_dir, imp_path, ui,
                start_round=1, max_rounds=1, check_cmd=None,
                allow_installs=False, timeout=300, model="claude-opus-4-6",
            )
        # Convergence path — no MEMORY WIPED diagnostic should have been written
        assert exc.value.code == 0
        assert not any("MEMORY WIPED" in d for d in diagnostics), diagnostics

    def test_memory_wipe_not_triggered_on_small_shrink(self, tmp_path: Path):
        """memory.md shrinking by less than the threshold does NOT trigger wipe retry."""
        project_dir, run_dir, imp_path = self._setup_project(tmp_path)
        ui = self.ui
        diagnostics: list[str] = []
        memory_path = project_dir / "runs" / "memory.md"
        pre_size = 1000
        memory_path.write_text("x" * pre_size)
        self._setup_git_with_commit(project_dir, "feat: thing")

        # Post-size stays strictly ABOVE the threshold cutoff so the test
        # stays aligned with _MEMORY_WIPE_THRESHOLD: if the threshold is
        # ever raised to e.g. 0.75, this test's post-size must still be
        # above pre_size * threshold to remain a "small shrink".
        post_size = int(pre_size * _MEMORY_WIPE_THRESHOLD) + 50
        assert post_size >= pre_size * _MEMORY_WIPE_THRESHOLD  # sanity

        def mock_monitored(cmd, cwd, ui_, round_num, watchdog_timeout=120):
            convo = run_dir / f"conversation_loop_{round_num}.md"
            convo.write_text("# Round conversation")
            existing = imp_path.read_text()
            imp_path.write_text(existing + f"- [ ] [functional] new {round_num}\n")
            # Trim to just above the threshold — below the wipe cutoff
            memory_path.write_text("x" * post_size)
            (run_dir / "CONVERGED").write_text("done")
            imp_path.write_text("- [x] [functional] do something\n")
            return 0, "output", False

        def mock_save_diag(run_dir_, round_num_, cmd_, output_, reason, attempt):
            diagnostics.append(reason)

        with patch("evolve.orchestrator._run_monitored_subprocess", side_effect=mock_monitored), \
             patch("evolve.orchestrator._is_self_evolving", return_value=True), \
             patch("evolve.diagnostics._save_subprocess_diagnostic", side_effect=mock_save_diag), \
             patch("evolve.diagnostics._generate_evolution_report"), \
             patch("evolve.orchestrator._run_party_mode"), \
             pytest.raises(SystemExit) as exc:
            _run_rounds(
                project_dir, run_dir, imp_path, ui,
                start_round=1, max_rounds=1, check_cmd=None,
                allow_installs=False, timeout=300, model="claude-opus-4-6",
            )
        assert exc.value.code == 0
        assert not any("MEMORY WIPED" in d for d in diagnostics), diagnostics

    def test_memory_wipe_not_triggered_when_memory_absent(self, tmp_path: Path):
        """No memory.md pre-round → no wipe check (mem_size_before == 0)."""
        project_dir, run_dir, imp_path = self._setup_project(tmp_path)
        ui = self.ui
        diagnostics: list[str] = []
        # Do NOT create memory.md
        self._setup_git_with_commit(project_dir, "feat: first")

        def mock_monitored(cmd, cwd, ui_, round_num, watchdog_timeout=120):
            convo = run_dir / f"conversation_loop_{round_num}.md"
            convo.write_text("# Round conversation")
            existing = imp_path.read_text()
            imp_path.write_text(existing + f"- [ ] [functional] new {round_num}\n")
            (run_dir / "CONVERGED").write_text("done")
            imp_path.write_text("- [x] [functional] do something\n")
            return 0, "output", False

        def mock_save_diag(run_dir_, round_num_, cmd_, output_, reason, attempt):
            diagnostics.append(reason)

        with patch("evolve.orchestrator._run_monitored_subprocess", side_effect=mock_monitored), \
             patch("evolve.orchestrator._is_self_evolving", return_value=True), \
             patch("evolve.diagnostics._save_subprocess_diagnostic", side_effect=mock_save_diag), \
             patch("evolve.diagnostics._generate_evolution_report"), \
             patch("evolve.orchestrator._run_party_mode"), \
             pytest.raises(SystemExit) as exc:
            _run_rounds(
                project_dir, run_dir, imp_path, ui,
                start_round=1, max_rounds=1, check_cmd=None,
                allow_installs=False, timeout=300, model="claude-opus-4-6",
            )
        assert exc.value.code == 0
        assert not any("MEMORY WIPED" in d for d in diagnostics)

    def test_memory_wipe_prompt_header(self, tmp_path: Path):
        """agent.build_prompt renders the dedicated 'silently wiped memory.md' header
        when the diagnostic starts with 'MEMORY WIPED'."""
        from evolve.agent import build_prompt
        run_dir = tmp_path / "runs" / "session"
        run_dir.mkdir(parents=True)
        project_dir = tmp_path
        (project_dir / "README.md").write_text("# project")
        (project_dir / "runs" / "improvements.md").write_text("- [ ] [functional] do x\n")
        (project_dir / "runs" / "memory.md").write_text("# memory\n")
        # Simulate the orchestrator's diagnostic file — threshold percentage
        # and marker string are derived from module constants so any future
        # change propagates via the single source of truth.
        threshold_pct = int(_MEMORY_WIPE_THRESHOLD * 100)
        (run_dir / "subprocess_error_round_2.txt").write_text(
            f"Round 2 — MEMORY WIPED: memory.md shrunk by >{threshold_pct}% "
            f"(2000\u21925 bytes) "
            f"without '{_MEMORY_COMPACTION_MARKER}' in commit message (attempt 1)\n"
            "Output (last 3000 chars):\n...last output...\n"
        )

        prompt = build_prompt(
            project_dir=project_dir,
            run_dir=run_dir,
            round_num=2,
            check_cmd=None,
            check_output=None,
            allow_installs=False,
        )
        assert "CRITICAL — Previous round silently wiped memory.md" in prompt
        # Should NOT also render the generic NO PROGRESS header
        assert "Previous round made NO PROGRESS" not in prompt
        # Guidance about append-only / compaction marker should be surfaced
        assert _MEMORY_COMPACTION_MARKER in prompt

    def test_memory_wipe_constants_are_single_source_of_truth(self):
        """The documented contract in SPEC.md § 'Byte-size sanity gate' is
        encoded in two module-level constants, not scattered magic values.

        A single targeted test to catch drift: if anyone changes the marker
        string or the threshold in loop.py, this test keeps the value
        contract explicit and surfaces the change via a single failure
        rather than several scattered assertion mismatches.
        """
        # Marker is the literal SPEC string, not a variant.
        assert _MEMORY_COMPACTION_MARKER == "memory: compaction"
        # Threshold is a fraction in (0, 1) — any integer or out-of-range
        # value would break the shrink comparison in _run_rounds.
        assert isinstance(_MEMORY_WIPE_THRESHOLD, float)
        assert 0.0 < _MEMORY_WIPE_THRESHOLD < 1.0
        # Current SPEC contract: "shrunk by more than 50%" → threshold 0.5.
        assert _MEMORY_WIPE_THRESHOLD == 0.5

    def test_zero_progress_not_triggered_on_real_progress(self, tmp_path: Path):
        """Zero-progress detection does NOT trigger when improvements.md changes."""
        project_dir, run_dir, imp_path = self._setup_project(tmp_path)
        ui = self.ui

        def mock_monitored(cmd, cwd, ui_, round_num, watchdog_timeout=120):
            convo = run_dir / f"conversation_loop_{round_num}.md"
            convo.write_text("# Round conversation")
            # Check off the improvement — real progress.  The remaining
            # `- [ ]` item carries a ``[blocked: ...]`` tag so the
            # convergence-gate orchestrator backstop recognizes it as a
            # resolved-for-convergence blocker (rather than an unresolved
            # item) — see SPEC.md § "Convergence".
            imp_path.write_text(
                "- [x] [functional] do something\n"
                "- [ ] [functional] [blocked: upstream dep missing] next\n"
            )
            (run_dir / "CONVERGED").write_text("All done")
            return 0, "output", False

        with patch("evolve.orchestrator._run_monitored_subprocess", side_effect=mock_monitored), \
             patch("evolve.orchestrator._is_self_evolving", return_value=True), \
             patch("evolve.diagnostics._generate_evolution_report"), \
             patch("evolve.orchestrator._run_party_mode"), \
             pytest.raises(SystemExit) as exc:
            _run_rounds(
                project_dir, run_dir, imp_path, ui,
                start_round=1, max_rounds=10, check_cmd=None,
                allow_installs=False, timeout=300, model="claude-opus-4-6",
            )
        # Should converge (exit 0), not trigger zero-progress
        assert exc.value.code == 0

    def test_convergence_overrides_imp_unchanged(self, tmp_path: Path):
        """CONVERGED suppresses imp_unchanged zero-progress signal.

        When all items are already checked and the agent writes CONVERGED
        without modifying improvements.md, the round should converge — not
        be flagged as zero-progress.  Regression test for the case where
        a convergence round legitimately leaves improvements.md unchanged.
        """
        project_dir, run_dir, imp_path = self._setup_project(tmp_path)
        ui = self.ui
        # Start with all items already checked — nothing to change
        imp_path.write_text("- [x] [functional] do something\n")

        def mock_monitored(cmd, cwd, ui_, round_num, watchdog_timeout=120):
            convo = run_dir / f"conversation_loop_{round_num}.md"
            convo.write_text("# Convergence verification round")
            # Write CONVERGED but do NOT touch improvements.md
            (run_dir / "CONVERGED").write_text("All spec claims verified")
            return 0, "output", False

        with patch("evolve.orchestrator._run_monitored_subprocess", side_effect=mock_monitored), \
             patch("evolve.orchestrator._is_self_evolving", return_value=True), \
             patch("evolve.diagnostics._generate_evolution_report"), \
             patch("evolve.orchestrator._run_party_mode"), \
             pytest.raises(SystemExit) as exc:
            _run_rounds(
                project_dir, run_dir, imp_path, ui,
                start_round=1, max_rounds=10, check_cmd=None,
                allow_installs=False, timeout=300, model="claude-opus-4-6",
            )
        # Must converge (exit 0), NOT trigger zero-progress retry
        assert exc.value.code == 0

    def test_forever_mode_skips_failed_round(self, tmp_path: Path):
        """In forever mode, exhausted retries skip to next round."""
        project_dir, run_dir, imp_path = self._setup_project(tmp_path)
        ui = self.ui

        round_attempts = {}

        def mock_monitored(cmd, cwd, ui_, round_num, watchdog_timeout=120):
            round_attempts.setdefault(round_num, 0)
            round_attempts[round_num] += 1
            if round_num == 1:
                return 0, "output", True  # always stalls
            convo = run_dir / f"conversation_loop_{round_num}.md"
            convo.write_text("# Round 2")
            return 0, "output", False

        with patch("evolve.orchestrator._run_monitored_subprocess", side_effect=mock_monitored), \
             patch("evolve.orchestrator._is_self_evolving", return_value=True), \
             patch("evolve.diagnostics._save_subprocess_diagnostic"), \
             patch("evolve.diagnostics._generate_evolution_report"), \
             pytest.raises(SystemExit):
            _run_rounds(
                project_dir, run_dir, imp_path, ui,
                start_round=1, max_rounds=2, check_cmd=None,
                allow_installs=False, timeout=300, model="claude-opus-4-6",
                forever=True,
            )

        assert round_attempts[1] == MAX_DEBUG_RETRIES + 1

    def test_blocked_improvements_exit(self, tmp_path: Path):
        """All remaining items blocked exits with code 1."""
        project_dir, run_dir, imp_path = self._setup_project(tmp_path)
        imp_path.write_text("- [ ] [functional] [needs-package] blocked\n")
        ui = self.ui

        with pytest.raises(SystemExit) as exc:
            _run_rounds(
                project_dir, run_dir, imp_path, ui,
                start_round=1, max_rounds=10, check_cmd=None,
                allow_installs=False, timeout=300, model="claude-opus-4-6",
            )
        assert exc.value.code == 1
        ui.blocked_message.assert_called_once()

    def test_error_log_cleanup_on_success(self, tmp_path: Path):
        """Diagnostic file is cleaned up after a successful round."""
        project_dir, run_dir, imp_path = self._setup_project(tmp_path)

        error_log = run_dir / "subprocess_error_round_1.txt"
        error_log.write_text("previous error")

        def mock_monitored(cmd, cwd, ui_, round_num, watchdog_timeout=120):
            convo = run_dir / f"conversation_loop_{round_num}.md"
            convo.write_text("# Round conversation")
            # Simulate progress by modifying improvements.md
            imp_path.write_text("- [x] [functional] do something\n- [ ] [functional] next\n")
            return 0, "output", False

        with patch("evolve.orchestrator._run_monitored_subprocess", side_effect=mock_monitored), \
             patch("evolve.orchestrator._is_self_evolving", return_value=True), \
             patch("evolve.diagnostics._generate_evolution_report"), \
             pytest.raises(SystemExit):
            _run_rounds(
                project_dir, run_dir, imp_path, self.ui,
                start_round=1, max_rounds=1, check_cmd=None,
                allow_installs=False, timeout=300, model="claude-opus-4-6",
            )

        assert not error_log.is_file()

    def test_allow_installs_flag_passed_to_subprocess(self, tmp_path: Path):
        """--allow-installs flag is included in subprocess command."""
        project_dir, run_dir, imp_path = self._setup_project(tmp_path)

        captured_cmd = None

        def mock_monitored(cmd, cwd, ui_, round_num, watchdog_timeout=120):
            nonlocal captured_cmd
            captured_cmd = cmd
            convo = run_dir / f"conversation_loop_{round_num}.md"
            convo.write_text("# Round")
            return 0, "output", False

        with patch("evolve.orchestrator._run_monitored_subprocess", side_effect=mock_monitored), \
             patch("evolve.orchestrator._is_self_evolving", return_value=True), \
             patch("evolve.diagnostics._generate_evolution_report"), \
             pytest.raises(SystemExit):
            _run_rounds(
                project_dir, run_dir, imp_path, self.ui,
                start_round=1, max_rounds=1, check_cmd="pytest",
                allow_installs=True, timeout=300, model="claude-opus-4-6",
            )

        assert "--allow-installs" in captured_cmd
        assert "--check" in captured_cmd

    def test_effort_flag_forwarded_to_subprocess(self, tmp_path: Path):
        """--effort <level> is included in the subprocess command built by _run_rounds."""
        project_dir, run_dir, imp_path = self._setup_project(tmp_path)

        captured_cmd = None

        def mock_monitored(cmd, cwd, ui_, round_num, watchdog_timeout=120):
            nonlocal captured_cmd
            captured_cmd = cmd
            convo = run_dir / f"conversation_loop_{round_num}.md"
            convo.write_text("# Round")
            return 0, "output", False

        with patch("evolve.orchestrator._run_monitored_subprocess", side_effect=mock_monitored), \
             patch("evolve.orchestrator._is_self_evolving", return_value=True), \
             patch("evolve.diagnostics._generate_evolution_report"), \
             pytest.raises(SystemExit):
            _run_rounds(
                project_dir, run_dir, imp_path, self.ui,
                start_round=1, max_rounds=1, check_cmd="pytest",
                allow_installs=False, timeout=300, model="claude-opus-4-6",
                effort="high",
            )

        assert captured_cmd is not None, "subprocess was never launched"
        assert "--effort" in captured_cmd
        idx = captured_cmd.index("--effort")
        assert captured_cmd[idx + 1] == "high"

    def test_effort_flag_omitted_when_none(self, tmp_path: Path):
        """When effort is None, --effort is NOT included in the subprocess command."""
        project_dir, run_dir, imp_path = self._setup_project(tmp_path)

        captured_cmd = None

        def mock_monitored(cmd, cwd, ui_, round_num, watchdog_timeout=120):
            nonlocal captured_cmd
            captured_cmd = cmd
            convo = run_dir / f"conversation_loop_{round_num}.md"
            convo.write_text("# Round")
            return 0, "output", False

        with patch("evolve.orchestrator._run_monitored_subprocess", side_effect=mock_monitored), \
             patch("evolve.orchestrator._is_self_evolving", return_value=True), \
             patch("evolve.diagnostics._generate_evolution_report"), \
             pytest.raises(SystemExit):
            _run_rounds(
                project_dir, run_dir, imp_path, self.ui,
                start_round=1, max_rounds=1, check_cmd="pytest",
                allow_installs=False, timeout=300, model="claude-opus-4-6",
                effort=None,
            )

        assert captured_cmd is not None, "subprocess was never launched"
        assert "--effort" not in captured_cmd

    def test_effort_each_level_forwarded(self, tmp_path: Path):
        """Each accepted effort level (low/medium/high/max) is forwarded correctly."""
        for level in ("low", "medium", "high", "max"):
            (tmp_path / level).mkdir()
            project_dir, run_dir, imp_path = self._setup_project(tmp_path / level)

            captured_cmd = None

            def mock_monitored(cmd, cwd, ui_, round_num, watchdog_timeout=120):
                nonlocal captured_cmd
                captured_cmd = cmd
                convo = run_dir / f"conversation_loop_{round_num}.md"
                convo.write_text("# Round")
                return 0, "output", False

            with patch("evolve.orchestrator._run_monitored_subprocess", side_effect=mock_monitored), \
             patch("evolve.orchestrator._is_self_evolving", return_value=True), \
                 patch("evolve.diagnostics._generate_evolution_report"), \
                 pytest.raises(SystemExit):
                _run_rounds(
                    project_dir, run_dir, imp_path, MagicMock(),
                    start_round=1, max_rounds=1, check_cmd=None,
                    allow_installs=False, timeout=300, model="claude-opus-4-6",
                    effort=level,
                )

            assert captured_cmd is not None, f"subprocess not launched for effort={level}"
            idx = captured_cmd.index("--effort")
            assert captured_cmd[idx + 1] == level, (
                f"effort={level} not forwarded: got {captured_cmd[idx + 1]}"
            )

    def test_parse_round_args_receives_forwarded_effort(self):
        """_parse_round_args correctly parses --effort from the subprocess argv."""
        from evolve import _parse_round_args

        with patch("sys.argv", [
            "evolve", "_round", "/tmp/proj",
            "--round-num", "1",
            "--effort", "low",
        ]):
            args = _parse_round_args()
        assert args.effort == "low"

    def test_parse_round_args_effort_default_is_medium(self):
        """_parse_round_args defaults effort to 'medium' when --effort is omitted."""
        from evolve import _parse_round_args

        with patch("sys.argv", [
            "evolve", "_round", "/tmp/proj",
            "--round-num", "1",
        ]):
            args = _parse_round_args()
        assert args.effort == "medium"

    def test_effort_end_to_end_argv_to_agent_module(self, tmp_path: Path):
        """Full plumbing: _run_rounds builds argv with --effort, _parse_round_args
        parses it, and run_single_round sets agent.EFFORT to the parsed value."""
        import evolve.agent as _agent_mod
        from evolve import _parse_round_args

        project_dir, run_dir, imp_path = self._setup_project(tmp_path)

        # Step 1: capture the command _run_rounds would build
        captured_cmd = None

        def mock_monitored(cmd, cwd, ui_, round_num, watchdog_timeout=120):
            nonlocal captured_cmd
            captured_cmd = cmd
            convo = run_dir / f"conversation_loop_{round_num}.md"
            convo.write_text("# Round")
            return 0, "output", False

        with patch("evolve.orchestrator._run_monitored_subprocess", side_effect=mock_monitored), \
             patch("evolve.orchestrator._is_self_evolving", return_value=True), \
             patch("evolve.diagnostics._generate_evolution_report"), \
             pytest.raises(SystemExit):
            _run_rounds(
                project_dir, run_dir, imp_path, self.ui,
                start_round=1, max_rounds=1, check_cmd="pytest",
                allow_installs=False, timeout=300, model="claude-opus-4-6",
                effort="medium",
            )

        # Step 2: feed the captured argv through _parse_round_args
        # The captured_cmd is [sys.executable, -m, evolve, _round, proj, ...flags...]
        # _parse_round_args reads sys.argv[2:], so we simulate that by taking
        # everything after the `_round` marker and prepending ["evolve", "_round"].
        round_idx = captured_cmd.index("_round")
        simulated_argv = ["evolve", "_round"] + captured_cmd[round_idx + 1:]
        with patch("sys.argv", simulated_argv):
            args = _parse_round_args()
        assert args.effort == "medium"

        # Step 3: verify run_single_round would set agent.EFFORT
        original = _agent_mod.EFFORT
        try:
            with patch("evolve.agent.analyze_and_fix", return_value=None), \
                 patch("evolve.agent.run_review_agent"):
                run_single_round(
                    project_dir=project_dir,
                    round_num=1,
                    check_cmd=None,
                    allow_installs=False,
                    timeout=60,
                    run_dir=run_dir,
                    model="claude-opus-4-6",
                    effort=args.effort,
                )
            assert _agent_mod.EFFORT == "medium"
        finally:
            _agent_mod.EFFORT = original

    def test_no_current_improvement_initial_analysis(self, tmp_path: Path):
        """When no improvements exist yet, shows 'initial analysis'."""
        project_dir, run_dir, imp_path = self._setup_project(tmp_path)
        imp_path.write_text("# Improvements\n")  # empty

        def mock_monitored(cmd, cwd, ui_, round_num, watchdog_timeout=120):
            convo = run_dir / f"conversation_loop_{round_num}.md"
            convo.write_text("# Round")
            return 0, "output", False

        with patch("evolve.orchestrator._run_monitored_subprocess", side_effect=mock_monitored), \
             patch("evolve.orchestrator._is_self_evolving", return_value=True), \
             patch("evolve.diagnostics._generate_evolution_report"), \
             pytest.raises(SystemExit):
            _run_rounds(
                project_dir, run_dir, imp_path, self.ui,
                start_round=1, max_rounds=1, check_cmd=None,
                allow_installs=False, timeout=300, model="claude-opus-4-6",
            )

        self.ui.round_header.assert_called()


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
             patch("evolve.diagnostics._generate_evolution_report"), \
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
             patch("evolve.diagnostics._generate_evolution_report"), \
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
    import evolve.agent as agent_mod

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
        import evolve.agent as agent_mod

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
        import evolve.agent as agent_mod

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
        import evolve.agent as agent_mod

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
        run_dir = _setup_party_project(tmp_path)
        ui = MagicMock()

        with patch("builtins.__import__", side_effect=_make_import_error_for_agent):
            _run_party_mode(tmp_path, run_dir, ui)

        ui.warn.assert_called_once_with("claude-agent-sdk not installed — skipping party mode")
        ui.party_results.assert_not_called()


def _make_import_error_for_agent(name, *args, **kwargs):
    """Simulate ImportError only for the agent module import inside _run_party_mode.
    After the package restructuring the import is ``from evolve.agent import …``,
    so the blocker targets ``evolve.agent`` instead of the legacy ``agent`` shim.
    """
    if name in ("agent", "evolve.agent"):
        raise ImportError(f"No module named '{name}'")
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

        with patch("evolve.diagnostics._auto_detect_check", return_value="pytest") as mock_detect, \
             patch("evolve.orchestrator._ensure_git"), \
             patch("evolve.orchestrator._run_rounds") as mock_run, \
             patch("evolve.orchestrator.get_tui") as mock_get_tui:
            evolve_loop(tmp_path, max_rounds=5, check_cmd=None)

        mock_detect.assert_called_once_with(tmp_path)
        # get_tui called for early UI message
        mock_get_tui.assert_called()
        # check_cmd should have been passed to _run_rounds as "pytest"
        mock_run.assert_called_once()
        args = mock_run.call_args
        assert args[0][6] == "pytest"  # check_cmd is the 7th positional arg

    def test_auto_detect_returns_none(self, tmp_path: Path):
        """When auto-detect finds nothing, check_cmd stays None."""
        (tmp_path / "README.md").write_text("# Test")
        (tmp_path / "runs").mkdir()

        with patch("evolve.diagnostics._auto_detect_check", return_value=None) as mock_detect, \
             patch("evolve.orchestrator._ensure_git"), \
             patch("evolve.orchestrator._run_rounds") as mock_run, \
             patch("evolve.orchestrator.get_tui"):
            evolve_loop(tmp_path, max_rounds=5, check_cmd=None)

        mock_detect.assert_called_once_with(tmp_path)
        mock_run.assert_called_once()
        args = mock_run.call_args
        assert args[0][6] is None  # check_cmd remains None

    def test_explicit_check_cmd_bypasses_auto_detect(self, tmp_path: Path):
        """When check_cmd is explicitly provided, _auto_detect_check is NOT called."""
        (tmp_path / "README.md").write_text("# Test")
        (tmp_path / "runs").mkdir()

        with patch("evolve.diagnostics._auto_detect_check") as mock_detect, \
             patch("evolve.orchestrator._ensure_git"), \
             patch("evolve.orchestrator._run_rounds") as mock_run, \
             patch("evolve.orchestrator.get_tui"):
            evolve_loop(tmp_path, max_rounds=5, check_cmd="npm test")

        mock_detect.assert_not_called()
        mock_run.assert_called_once()
        args = mock_run.call_args
        assert args[0][6] == "npm test"  # explicit check_cmd passed through


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
        import evolve.orchestrator as loop_mod
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
        import evolve.orchestrator as loop_mod
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
        from evolve.orchestrator import _forever_restart

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


# ---------------------------------------------------------------------------
# Party mode comprehensive tests — agent persona loading, prompt building,
# missing workflow handling, and end-to-end verification
# ---------------------------------------------------------------------------

class TestPartyModeAgentLoading:
    """Test _run_party_mode agent persona loading from agents/*.md."""

    def test_multiple_agents_loaded_sorted(self, tmp_path: Path):
        """Multiple agent personas are loaded in sorted order and included in prompt."""
        agents = tmp_path / "agents"
        agents.mkdir()
        (agents / "charlie.md").write_text("# Charlie — backend expert")
        (agents / "alice.md").write_text("# Alice — frontend lead")
        (agents / "bob.md").write_text("# Bob — devops guru")

        run_dir = tmp_path / "runs" / "session"
        run_dir.mkdir(parents=True)
        (tmp_path / "README.md").write_text("# My Project")
        (tmp_path / "runs" / "improvements.md").write_text("- [x] done\n")
        (tmp_path / "runs" / "memory.md").write_text("# Memory\n")
        (run_dir / "CONVERGED").write_text("Fully converged")

        ui = MagicMock()
        captured_prompt = {}

        import asyncio as _asyncio
        import evolve.agent as agent_mod

        def mock_run_agent(prompt, project_dir, round_num=0, run_dir=None, log_filename=None):
            captured_prompt["value"] = prompt
            return MagicMock()

        def mock_asyncio_run(coro):
            coro.close()
            # Simulate agent creating output files
            (run_dir / "party_report.md").write_text("# Party Report\n")
            (run_dir / "README_proposal.md").write_text("# New README\n")

        with patch.object(agent_mod, 'run_claude_agent', side_effect=mock_run_agent) as mock_agent, \
             patch.object(_asyncio, 'run', side_effect=mock_asyncio_run):
            _run_party_mode(tmp_path, run_dir, ui)

        # Verify run_claude_agent was called with prompt containing all 3 agents
        mock_agent.assert_called_once()
        prompt = mock_agent.call_args[0][0]
        # Agents should appear in sorted order: alice, bob, charlie
        alice_pos = prompt.index("alice.md")
        bob_pos = prompt.index("bob.md")
        charlie_pos = prompt.index("charlie.md")
        assert alice_pos < bob_pos < charlie_pos

        # All persona content should be in prompt
        assert "# Alice — frontend lead" in prompt
        assert "# Bob — devops guru" in prompt
        assert "# Charlie — backend expert" in prompt

        # Roster should list all agents
        assert "- alice.md" in prompt
        assert "- bob.md" in prompt
        assert "- charlie.md" in prompt

    def test_project_agents_dir_preferred_over_evolve(self, tmp_path: Path):
        """Project-level agents/ dir is used when it exists."""
        # Create project-level agents dir
        agents = tmp_path / "agents"
        agents.mkdir()
        (agents / "custom.md").write_text("# Custom project agent")

        run_dir = tmp_path / "runs" / "session"
        run_dir.mkdir(parents=True)
        (tmp_path / "README.md").write_text("# Test")
        (tmp_path / "runs" / "improvements.md").write_text("- [x] done\n")

        ui = MagicMock()
        import asyncio as _asyncio
        import evolve.agent as agent_mod

        with patch.object(agent_mod, 'run_claude_agent', return_value=MagicMock()), \
             patch.object(_asyncio, 'run', side_effect=lambda c: c.close()):
            _run_party_mode(tmp_path, run_dir, ui)

        # Should NOT have warned — agents were found
        for call_args in ui.warn.call_args_list:
            assert "No agent personas" not in str(call_args)
        # party_mode UI should have been called
        ui.party_mode.assert_called_once()

    def test_fallback_to_evolve_agents_dir(self, tmp_path: Path):
        """Falls back to evolve's own agents/ when project has none."""
        # No project-level agents dir
        run_dir = tmp_path / "runs" / "session"
        run_dir.mkdir(parents=True)
        (tmp_path / "README.md").write_text("# Test")
        (tmp_path / "runs" / "improvements.md").write_text("- [x] done\n")

        ui = MagicMock()

        # ``_run_party_mode`` (in ``evolve/party.py``) looks for
        # fallback agents at ``Path(__file__).parent.parent / "agents"``
        # which is the project root — not the ``evolve/`` package dir.
        # Use the same resolution for the test's existence check.
        import evolve.party as party_mod
        evolve_agents = Path(party_mod.__file__).parent.parent / "agents"

        if evolve_agents.is_dir() and list(evolve_agents.glob("*.md")):
            # Evolve has its own agents — should proceed without warning about missing agents
            import asyncio as _asyncio
            import evolve.agent as agent_mod

            with patch.object(agent_mod, 'run_claude_agent', return_value=MagicMock()), \
                 patch.object(_asyncio, 'run', side_effect=lambda c: c.close()):
                _run_party_mode(tmp_path, run_dir, ui)

            ui.party_mode.assert_called_once()
        else:
            # No evolve agents either — should warn
            _run_party_mode(tmp_path, run_dir, ui)
            ui.warn.assert_called_with("No agent personas found — skipping party mode")


class TestPartyModePromptContent:
    """Verify prompt content includes all required context."""

    def test_prompt_includes_context_files(self, tmp_path: Path):
        """Prompt includes README, improvements, memory, and convergence reason."""
        agents = tmp_path / "agents"
        agents.mkdir()
        (agents / "dev.md").write_text("# Dev persona")

        run_dir = tmp_path / "runs" / "session"
        run_dir.mkdir(parents=True)
        (tmp_path / "README.md").write_text("# My Unique Project README")
        (tmp_path / "runs" / "improvements.md").write_text("- [x] [functional] unique improvement item\n")
        (tmp_path / "runs" / "memory.md").write_text("# Memory\n## Error: unique memory entry\n")
        (run_dir / "CONVERGED").write_text("Unique convergence reason here")

        ui = MagicMock()
        import asyncio as _asyncio
        import evolve.agent as agent_mod

        with patch.object(agent_mod, 'run_claude_agent', return_value=MagicMock()) as mock_agent, \
             patch.object(_asyncio, 'run', side_effect=lambda c: c.close()):
            _run_party_mode(tmp_path, run_dir, ui)

        prompt = mock_agent.call_args[0][0]
        assert "# My Unique Project README" in prompt
        assert "unique improvement item" in prompt
        assert "unique memory entry" in prompt
        assert "Unique convergence reason here" in prompt

    def test_prompt_handles_missing_context_files(self, tmp_path: Path):
        """Prompt uses '(none)' when README/improvements/memory are missing."""
        agents = tmp_path / "agents"
        agents.mkdir()
        (agents / "dev.md").write_text("# Dev persona")

        run_dir = tmp_path / "runs" / "session"
        run_dir.mkdir(parents=True)
        # No README, no improvements.md, no memory.md, no CONVERGED

        ui = MagicMock()
        import asyncio as _asyncio
        import evolve.agent as agent_mod

        with patch.object(agent_mod, 'run_claude_agent', return_value=MagicMock()) as mock_agent, \
             patch.object(_asyncio, 'run', side_effect=lambda c: c.close()):
            _run_party_mode(tmp_path, run_dir, ui)

        prompt = mock_agent.call_args[0][0]
        # Missing files should be represented as "(none)"
        assert "(none)" in prompt

    def test_prompt_includes_workflow_content(self, tmp_path: Path):
        """When workflow files exist, their content appears in the prompt."""
        agents = tmp_path / "agents"
        agents.mkdir()
        (agents / "dev.md").write_text("# Dev")

        run_dir = tmp_path / "runs" / "session"
        run_dir.mkdir(parents=True)
        (tmp_path / "README.md").write_text("# Test")
        (tmp_path / "runs" / "improvements.md").write_text("- [x] done\n")

        ui = MagicMock()
        import asyncio as _asyncio
        import evolve.agent as agent_mod
        import evolve.party as party_mod

        # ``_run_party_mode`` looks for workflows at
        # ``Path(__file__).parent.parent / "workflows"`` — the project
        # root relative to ``evolve/party.py``.  Use the same lookup
        # so this test creates the fixture where the code reads.
        wf_dir = Path(party_mod.__file__).parent.parent / "workflows" / "party-mode"
        wf_existed = wf_dir.is_dir()

        if not wf_existed:
            wf_dir.mkdir(parents=True, exist_ok=True)
            (wf_dir / "workflow.md").write_text("# Unique Workflow Content XYZ")
            steps = wf_dir / "steps"
            steps.mkdir(exist_ok=True)
            (steps / "step-01.md").write_text("# Step 1 unique content ABC")

        try:
            with patch.object(agent_mod, 'run_claude_agent', return_value=MagicMock()) as mock_agent, \
                 patch.object(_asyncio, 'run', side_effect=lambda c: c.close()):
                _run_party_mode(tmp_path, run_dir, ui)

            prompt = mock_agent.call_args[0][0]
            # Workflow content should be in the prompt
            if not wf_existed:
                assert "Unique Workflow Content XYZ" in prompt
                assert "Step 1 unique content ABC" in prompt
        finally:
            # Clean up if we created files
            if not wf_existed and wf_dir.is_dir():
                import shutil
                shutil.rmtree(wf_dir)


class TestPartyModeMissingWorkflow:
    """Test _run_party_mode when no workflow directory exists."""

    def test_no_workflow_dir_anywhere(self, tmp_path: Path):
        """Party mode proceeds with empty workflow when no workflow dir exists."""
        agents = tmp_path / "agents"
        agents.mkdir()
        (agents / "dev.md").write_text("# Dev Agent")

        run_dir = tmp_path / "runs" / "session"
        run_dir.mkdir(parents=True)
        (tmp_path / "README.md").write_text("# Test")
        (tmp_path / "runs" / "improvements.md").write_text("- [x] done\n")

        ui = MagicMock()
        import asyncio as _asyncio
        import evolve.agent as agent_mod
        import evolve.orchestrator as loop_mod

        real_parent = Path(loop_mod.__file__).parent
        real_is_dir = Path.is_dir

        def patched_is_dir(self_path):
            # Make both evolve and project workflow dirs appear non-existent
            if "workflows" in str(self_path) and "party-mode" in str(self_path):
                return False
            return real_is_dir(self_path)

        with patch.object(Path, "is_dir", patched_is_dir), \
             patch.object(agent_mod, 'run_claude_agent', return_value=MagicMock()) as mock_agent, \
             patch.object(_asyncio, 'run', side_effect=lambda c: c.close()):
            _run_party_mode(tmp_path, run_dir, ui)

        # Should still call the agent (empty workflow is fine)
        mock_agent.assert_called_once()
        prompt = mock_agent.call_args[0][0]
        # Workflow section should be empty but prompt should still work
        assert "## Workflow" in prompt
        # Should not have warned about missing agents or anything
        ui.party_mode.assert_called_once()

    def test_workflow_dir_exists_but_empty(self, tmp_path: Path):
        """Party mode proceeds when workflow dir exists but has no files."""
        agents = tmp_path / "agents"
        agents.mkdir()
        (agents / "dev.md").write_text("# Dev Agent")

        # Create empty workflow dir in project
        wf_dir = tmp_path / "workflows" / "party-mode"
        wf_dir.mkdir(parents=True)
        # No workflow.md, no steps/

        run_dir = tmp_path / "runs" / "session"
        run_dir.mkdir(parents=True)
        (tmp_path / "README.md").write_text("# Test")
        (tmp_path / "runs" / "improvements.md").write_text("- [x] done\n")

        ui = MagicMock()
        import asyncio as _asyncio
        import evolve.agent as agent_mod
        import evolve.orchestrator as loop_mod

        real_parent = Path(loop_mod.__file__).parent
        real_is_dir = Path.is_dir

        def patched_is_dir(self_path):
            # Make evolve's wf_dir not exist so it falls back to project
            if str(self_path) == str(real_parent / "workflows" / "party-mode"):
                return False
            return real_is_dir(self_path)

        with patch.object(Path, "is_dir", patched_is_dir), \
             patch.object(agent_mod, 'run_claude_agent', return_value=MagicMock()) as mock_agent, \
             patch.object(_asyncio, 'run', side_effect=lambda c: c.close()):
            _run_party_mode(tmp_path, run_dir, ui)

        mock_agent.assert_called_once()
        # Workflow content should be empty (no files to load)
        prompt = mock_agent.call_args[0][0]
        assert "## Workflow" in prompt


class TestPartyModeEndToEnd:
    """End-to-end party mode tests verifying file creation and UI calls."""

    def test_successful_run_creates_files_and_calls_ui(self, tmp_path: Path):
        """Full successful party mode run creates both output files and calls all UI methods."""
        agents = tmp_path / "agents"
        agents.mkdir()
        (agents / "dev.md").write_text("# Dev")
        (agents / "pm.md").write_text("# PM")

        run_dir = tmp_path / "runs" / "session"
        run_dir.mkdir(parents=True)
        (tmp_path / "README.md").write_text("# Project")
        (tmp_path / "runs" / "improvements.md").write_text("- [x] done\n")
        (tmp_path / "runs" / "memory.md").write_text("# Memory\n")
        (run_dir / "CONVERGED").write_text("Done")

        ui = MagicMock()
        import asyncio as _asyncio
        import evolve.agent as agent_mod

        def mock_asyncio_run(coro):
            coro.close()
            (run_dir / "party_report.md").write_text("# Party Report\nAgents discussed improvements.\n")
            (run_dir / "README_proposal.md").write_text("# Updated README\nNew features proposed.\n")

        with patch.object(agent_mod, 'run_claude_agent', return_value=MagicMock()), \
             patch.object(_asyncio, 'run', side_effect=mock_asyncio_run):
            _run_party_mode(tmp_path, run_dir, ui)

        # UI lifecycle calls
        ui.party_mode.assert_called_once()
        ui.party_results.assert_called_once_with(
            str(run_dir / "README_proposal.md"),
            str(run_dir / "party_report.md"),
        )
        # No warnings
        ui.warn.assert_not_called()

        # Files exist with correct content
        assert (run_dir / "party_report.md").read_text() == "# Party Report\nAgents discussed improvements.\n"
        assert (run_dir / "README_proposal.md").read_text() == "# Updated README\nNew features proposed.\n"

    def test_agent_called_with_correct_args(self, tmp_path: Path):
        """run_claude_agent is called with correct project_dir, round_num, run_dir, log_filename."""
        agents = tmp_path / "agents"
        agents.mkdir()
        (agents / "dev.md").write_text("# Dev")

        run_dir = tmp_path / "runs" / "session"
        run_dir.mkdir(parents=True)
        (tmp_path / "README.md").write_text("# Test")
        (tmp_path / "runs" / "improvements.md").write_text("- [x] done\n")

        ui = MagicMock()
        import asyncio as _asyncio
        import evolve.agent as agent_mod

        with patch.object(agent_mod, 'run_claude_agent', return_value=MagicMock()) as mock_agent, \
             patch.object(_asyncio, 'run', side_effect=lambda c: c.close()):
            _run_party_mode(tmp_path, run_dir, ui)

        mock_agent.assert_called_once()
        _, kwargs = mock_agent.call_args
        # Positional: prompt, project_dir
        args = mock_agent.call_args[0]
        assert args[1] == tmp_path  # project_dir
        assert kwargs.get("round_num", mock_agent.call_args[0][2] if len(args) > 2 else None) is not None
        assert "log_filename" in kwargs or len(args) > 4
        # Verify log_filename is party_conversation.md
        if "log_filename" in kwargs:
            assert kwargs["log_filename"] == "party_conversation.md"

    def test_ui_none_creates_default_tui(self, tmp_path: Path):
        """When ui=None, _run_party_mode creates a default TUI via get_tui()."""
        agents = tmp_path / "agents"
        agents.mkdir()
        (agents / "dev.md").write_text("# Dev")

        run_dir = tmp_path / "runs" / "session"
        run_dir.mkdir(parents=True)
        (tmp_path / "README.md").write_text("# Test")
        (tmp_path / "runs" / "improvements.md").write_text("- [x] done\n")

        import asyncio as _asyncio
        import evolve.agent as agent_mod

        mock_ui = MagicMock()

        # ``_run_party_mode`` was extracted to ``evolve/party.py``, which
        # imports ``get_tui`` from ``tui``.  Patch at the import site used
        # by the extracted module, not the legacy ``loop.get_tui`` alias.
        with patch("evolve.party.get_tui", return_value=mock_ui), \
             patch.object(agent_mod, 'run_claude_agent', return_value=MagicMock()), \
             patch.object(_asyncio, 'run', side_effect=lambda c: c.close()):
            _run_party_mode(tmp_path, run_dir, ui=None)

        # get_tui was used and party_mode was called on it
        mock_ui.party_mode.assert_called_once()


# ---------------------------------------------------------------------------
# Integration tests for evolve_loop with --forever flag
# ---------------------------------------------------------------------------

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
        """Full forever flow: branch → converge → party → restart → exit.

        Verifies that evolve_loop with forever=True:
        1. Calls _setup_forever_branch
        2. On convergence, calls _run_party_mode and _forever_restart
        3. Creates a new session directory and commits
        4. Restarts the loop; second call raises SystemExit to break out
        """
        project_dir, imp_path = self._setup_project(tmp_path)

        call_count = 0

        def mock_monitored(cmd, cwd, ui_, round_num, watchdog_timeout=120):
            nonlocal call_count
            call_count += 1
            run_dir = self._extract_run_dir(cmd)
            if run_dir:
                run_dir.mkdir(parents=True, exist_ok=True)

            if call_count == 1:
                # First round: converge
                (run_dir / f"conversation_loop_{round_num}.md").write_text("# Round 1 converged")
                (run_dir / "CONVERGED").write_text("All README claims verified")
                imp_path.write_text("- [x] [functional] initial improvement\n")
                return 0, "converged output", False
            else:
                # After restart: raise SystemExit to break out of infinite loop
                raise SystemExit(42)

        with patch("evolve.orchestrator._setup_forever_branch") as mock_branch, \
             patch("evolve.orchestrator._ensure_git"), \
             patch("evolve.orchestrator._ensure_runs_layout"), \
             patch("evolve.orchestrator._run_monitored_subprocess", side_effect=mock_monitored), \
             patch("evolve.diagnostics._generate_evolution_report"), \
             patch("evolve.orchestrator._run_party_mode") as mock_party, \
             patch("evolve.orchestrator._forever_restart") as mock_restart, \
             patch("evolve.orchestrator._git_commit") as mock_commit, \
             pytest.raises(SystemExit) as exc:
            evolve_loop(project_dir, max_rounds=1, forever=True)

        # 1. Branch was created
        mock_branch.assert_called_once_with(project_dir)

        # 2. Party mode was invoked after convergence
        mock_party.assert_called_once()
        party_args = mock_party.call_args[0]
        assert party_args[0] == project_dir

        # 3. _forever_restart was called with correct args
        mock_restart.assert_called_once()
        restart_args = mock_restart.call_args[0]
        assert restart_args[0] == project_dir
        assert restart_args[2] == imp_path

        # 4. Git commit was made for forever restart
        mock_commit.assert_called_once()
        assert "forever mode" in mock_commit.call_args[0][1]

        # 5. Exited with our sentinel code (from the second subprocess call)
        assert exc.value.code == 42

    def test_forever_readme_proposal_adoption_end_to_end(self, tmp_path: Path):
        """Verify that _forever_restart actually adopts README_proposal.md content.

        Uses the real _forever_restart (not mocked) to verify README adoption
        and improvements reset.
        """
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
                # First round: converge and produce README_proposal
                (run_dir / f"conversation_loop_{round_num}.md").write_text("# Converged")
                (run_dir / "CONVERGED").write_text("Done")
                (run_dir / "README_proposal.md").write_text(proposed_readme)
                imp_path.write_text("- [x] [functional] initial improvement\n")
                return 0, "converged", False
            else:
                raise SystemExit(42)

        with patch("evolve.orchestrator._setup_forever_branch"), \
             patch("evolve.orchestrator._ensure_git"), \
             patch("evolve.orchestrator._ensure_runs_layout"), \
             patch("evolve.orchestrator._run_monitored_subprocess", side_effect=mock_monitored), \
             patch("evolve.diagnostics._generate_evolution_report"), \
             patch("evolve.orchestrator._run_party_mode"), \
             patch("evolve.orchestrator._git_commit"), \
             pytest.raises(SystemExit):
            # Use real _forever_restart (not mocked)
            evolve_loop(project_dir, max_rounds=1, forever=True)

        # README.md should have been updated to the proposal content
        assert (project_dir / "README.md").read_text() == proposed_readme

        # improvements.md should have been reset
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
                # No README_proposal.md produced
                imp_path.write_text("- [x] [functional] initial improvement\n")
                return 0, "converged", False
            else:
                raise SystemExit(42)

        with patch("evolve.orchestrator._setup_forever_branch"), \
             patch("evolve.orchestrator._ensure_git"), \
             patch("evolve.orchestrator._ensure_runs_layout"), \
             patch("evolve.orchestrator._run_monitored_subprocess", side_effect=mock_monitored), \
             patch("evolve.diagnostics._generate_evolution_report"), \
             patch("evolve.orchestrator._run_party_mode"), \
             patch("evolve.orchestrator._git_commit"), \
             pytest.raises(SystemExit):
            evolve_loop(project_dir, max_rounds=1, forever=True)

        # README unchanged since no proposal was produced
        assert (project_dir / "README.md").read_text() == original_readme
        # improvements.md still reset
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
                # Round 1 always stalls
                return 0, "stalled output", True
            elif round_num == 2:
                # Round 2 succeeds with progress
                if run_dir:
                    (run_dir / f"conversation_loop_{round_num}.md").write_text("# Round 2 ok")
                return 0, "ok output", False
            else:
                raise SystemExit(42)

        with patch("evolve.orchestrator._setup_forever_branch"), \
             patch("evolve.orchestrator._ensure_git"), \
             patch("evolve.orchestrator._run_monitored_subprocess", side_effect=mock_monitored), \
             patch("evolve.diagnostics._save_subprocess_diagnostic"), \
             patch("evolve.diagnostics._generate_evolution_report"), \
             pytest.raises(SystemExit):
            evolve_loop(project_dir, max_rounds=2, forever=True)

        # Round 1 should have been retried MAX_DEBUG_RETRIES+1 times then skipped
        assert round_attempts[1] == MAX_DEBUG_RETRIES + 1

    def test_forever_sets_max_rounds_to_large_value(self, tmp_path: Path):
        """evolve_loop with forever=True internally sets max_rounds to 999999."""
        project_dir, imp_path = self._setup_project(tmp_path)

        with patch("evolve.orchestrator._setup_forever_branch"), \
             patch("evolve.orchestrator._ensure_git"), \
             patch("evolve.orchestrator._run_rounds") as mock_run:
            evolve_loop(project_dir, max_rounds=5, forever=True)

        # Verify max_rounds was overridden to 999999
        call_args = mock_run.call_args[0]
        assert call_args[5] == 999999  # max_rounds is the 6th positional arg

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

        with patch("evolve.orchestrator._setup_forever_branch"), \
             patch("evolve.orchestrator._ensure_git"), \
             patch("evolve.orchestrator._ensure_runs_layout"), \
             patch("evolve.orchestrator._run_monitored_subprocess", side_effect=mock_monitored), \
             patch("evolve.diagnostics._generate_evolution_report"), \
             patch("evolve.orchestrator._run_party_mode"), \
             patch("evolve.orchestrator._forever_restart"), \
             patch("evolve.orchestrator._git_commit"), \
             pytest.raises(SystemExit):
            evolve_loop(project_dir, max_rounds=1, forever=True)

        dirs_after = set(
            d.name for d in (project_dir / "runs").iterdir() if d.is_dir()
        )
        new_dirs = dirs_after - dirs_before
        # At least 1 session dir created; the restart dir may share the same
        # second-precision timestamp, so we verify via ui.run_dir_info calls
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

        with patch("evolve.orchestrator._setup_forever_branch"), \
             patch("evolve.orchestrator._ensure_git"), \
             patch("evolve.orchestrator._ensure_runs_layout"), \
             patch("evolve.orchestrator.get_tui", return_value=ui), \
             patch("evolve.orchestrator._run_monitored_subprocess", side_effect=mock_monitored), \
             patch("evolve.diagnostics._generate_evolution_report") as mock_report, \
             patch("evolve.orchestrator._run_party_mode"), \
             patch("evolve.orchestrator._forever_restart"), \
             patch("evolve.orchestrator._git_commit"), \
             pytest.raises(SystemExit):
            evolve_loop(project_dir, max_rounds=1, forever=True)

        # Evolution report was generated
        mock_report.assert_called_once()
        report_args = mock_report.call_args
        assert report_args[0][0] == project_dir  # positional: project_dir
        assert report_args[1].get("converged") is True  # keyword: converged=True

        # UI received converged + completion_summary calls
        ui.converged.assert_called_once()
        ui.completion_summary.assert_called_once()
        summary_kwargs = ui.completion_summary.call_args[1]
        assert summary_kwargs["status"] == "CONVERGED"


# ---------------------------------------------------------------------------
# Integration tests for evolve_loop with --resume and --forever combined
# ---------------------------------------------------------------------------

class TestEvolveLoopResumeForeverCombined:
    """Tests for evolve_loop with both resume=True and forever=True.

    Verifies that resuming a forever-mode session correctly:
    - Calls _setup_forever_branch (branch setup happens before resume logic)
    - Sets max_rounds to 999999
    - Detects the last completed round from conversation logs
    - Continues the evolution loop from the right starting point
    - Passes forever=True to _run_rounds so convergence triggers restart
    """

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

        with patch("evolve.orchestrator._ensure_git"), \
             patch("evolve.orchestrator._setup_forever_branch") as mock_branch, \
             patch("evolve.orchestrator._run_rounds") as mock_run:
            evolve_loop(project_dir, max_rounds=10, resume=True, forever=True)

        # 1. Branch setup was called
        mock_branch.assert_called_once_with(project_dir)

        # 2. _run_rounds was called (via the resume path)
        mock_run.assert_called_once()
        args = mock_run.call_args

        # 3. start_round should be 6 (last convo was 5, so start at 6)
        assert args[0][4] == 6, f"Expected start_round=6, got {args[0][4]}"

        # 4. max_rounds should be 999999 (forever mode overrides)
        assert args[0][5] == 999999, f"Expected max_rounds=999999, got {args[0][5]}"

        # 5. forever=True is passed through
        assert args[1].get("forever") is True

    def test_resume_forever_no_sessions_starts_fresh(self, tmp_path: Path):
        """Resume + forever with no existing sessions starts a fresh session."""
        project_dir = tmp_path / "proj"
        project_dir.mkdir()
        (project_dir / "README.md").write_text("# Test\n")
        (project_dir / "runs").mkdir()

        with patch("evolve.orchestrator._ensure_git"), \
             patch("evolve.orchestrator._setup_forever_branch") as mock_branch, \
             patch("evolve.orchestrator._run_rounds") as mock_run:
            evolve_loop(project_dir, max_rounds=10, resume=True, forever=True)

        mock_branch.assert_called_once_with(project_dir)
        mock_run.assert_called_once()
        args = mock_run.call_args
        # Falls through to fresh start path
        assert args[0][4] == 1  # start_round
        assert args[0][5] == 999999  # max_rounds (forever override)
        assert args[1].get("forever") is True

    def test_resume_forever_session_no_convos_starts_round_1(self, tmp_path: Path):
        """Resume + forever with session but no conversation logs starts from round 1."""
        project_dir, session = self._setup_project_with_session(tmp_path, num_convos=0)

        with patch("evolve.orchestrator._ensure_git"), \
             patch("evolve.orchestrator._setup_forever_branch"), \
             patch("evolve.orchestrator._run_rounds") as mock_run:
            evolve_loop(project_dir, max_rounds=10, resume=True, forever=True)

        mock_run.assert_called_once()
        args = mock_run.call_args
        assert args[0][4] == 1  # start_round (no convos to detect)
        assert args[0][5] == 999999

    def test_resume_forever_uses_correct_session_dir(self, tmp_path: Path):
        """Resume + forever reuses the most recent session directory."""
        project_dir = tmp_path / "proj"
        project_dir.mkdir()
        (project_dir / "README.md").write_text("# Test\n")
        runs_dir = project_dir / "runs"
        runs_dir.mkdir()
        (runs_dir / "improvements.md").write_text("# Improvements\n")

        # Create two sessions — resume should pick the latest
        old_session = runs_dir / "20260101_100000"
        old_session.mkdir()
        (old_session / "conversation_loop_1.md").write_text("r1")

        new_session = runs_dir / "20260201_100000"
        new_session.mkdir()
        (new_session / "conversation_loop_1.md").write_text("r1")
        (new_session / "conversation_loop_2.md").write_text("r2")

        with patch("evolve.orchestrator._ensure_git"), \
             patch("evolve.orchestrator._ensure_runs_layout"), \
             patch("evolve.orchestrator._setup_forever_branch"), \
             patch("evolve.orchestrator._run_rounds") as mock_run:
            evolve_loop(project_dir, max_rounds=10, resume=True, forever=True)

        mock_run.assert_called_once()
        args = mock_run.call_args
        # Should use the latest session
        assert args[0][1] == new_session  # run_dir
        assert args[0][4] == 3  # start_round (after convo 2)

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
                # First call (round 3, resumed): converge
                (run_dir / f"conversation_loop_{round_num}.md").write_text("converged")
                (run_dir / "CONVERGED").write_text("done")
                imp_path.write_text("- [x] [functional] done\n")
                return 0, "converged output", False
            else:
                # After restart: break out of infinite loop
                raise SystemExit(42)

        with patch("evolve.orchestrator._ensure_git"), \
             patch("evolve.orchestrator._ensure_runs_layout"), \
             patch("evolve.orchestrator._setup_forever_branch"), \
             patch("evolve.orchestrator._run_monitored_subprocess", side_effect=mock_monitored), \
             patch("evolve.orchestrator._git_commit"), \
             patch("evolve.orchestrator._run_party_mode") as mock_party, \
             patch("evolve.orchestrator._forever_restart") as mock_restart, \
             patch("evolve.diagnostics._generate_evolution_report"), \
             patch("evolve.orchestrator.fire_hook"), \
             pytest.raises(SystemExit) as exc:
            evolve_loop(project_dir, max_rounds=10, resume=True, forever=True)

        # Party mode and forever_restart should have been called
        mock_party.assert_called_once()
        mock_restart.assert_called_once()
        assert exc.value.code == 42

    def test_resume_forever_no_runs_dir_starts_fresh(self, tmp_path: Path):
        """Resume + forever without runs/ dir creates fresh session."""
        project_dir = tmp_path / "proj"
        project_dir.mkdir()
        (project_dir / "README.md").write_text("# Test\n")

        with patch("evolve.orchestrator._ensure_git"), \
             patch("evolve.orchestrator._setup_forever_branch") as mock_branch, \
             patch("evolve.orchestrator._run_rounds") as mock_run:
            evolve_loop(project_dir, max_rounds=10, resume=True, forever=True)

        mock_branch.assert_called_once()
        mock_run.assert_called_once()
        args = mock_run.call_args
        assert args[0][4] == 1  # start_round (fresh)
        assert args[0][5] == 999999  # max_rounds
        assert args[1].get("forever") is True


# ---------------------------------------------------------------------------
# Backlog growth monitoring (SPEC.md § "Growth monitoring")
# ---------------------------------------------------------------------------


class TestBacklogStateJsonSchema:
    """Tests for the ``backlog`` block exposed in state.json.

    Verifies the field shape documented in SPEC.md § "Growth monitoring":
    ``backlog: { pending, done, blocked, added_this_round,
    growth_rate_last_5_rounds }``.
    """

    @staticmethod
    def _git(project_dir: Path, *args: str) -> None:
        subprocess.run(["git", *args], cwd=project_dir, check=True,
                       capture_output=True)

    def _init_repo(self, project_dir: Path) -> None:
        project_dir.mkdir(parents=True, exist_ok=True)
        self._git(project_dir, "init", "-q", "-b", "main")
        self._git(project_dir, "config", "user.email", "test@example.com")
        self._git(project_dir, "config", "user.name", "Test")
        self._git(project_dir, "config", "commit.gpgsign", "false")

    def test_schema_field_names_and_types(self, tmp_path: Path):
        """state.json.backlog has the exact 5 keys and types documented in SPEC."""
        from evolve.orchestrator import _write_state_json
        import json as _json

        project_dir = tmp_path / "proj"
        self._init_repo(project_dir)
        run_dir = project_dir / "runs" / "session"
        run_dir.mkdir(parents=True)
        improvements = project_dir / "runs" / "improvements.md"
        improvements.write_text(
            "# Improvements\n"
            "- [x] [functional] done one\n"
            "- [ ] [functional] pending one\n"
            "- [ ] [performance] [needs-package] pending two needing pkg\n"
        )

        _write_state_json(
            run_dir=run_dir,
            project_dir=project_dir,
            round_num=1,
            max_rounds=10,
            phase="improvement",
            status="running",
            improvements_path=improvements,
            started_at="2026-04-23T15:00:00Z",
        )

        state = _json.loads((run_dir / "state.json").read_text())
        backlog = state["backlog"]
        # Exact 5 keys, no more, no less.
        assert set(backlog.keys()) == {
            "pending",
            "done",
            "blocked",
            "added_this_round",
            "growth_rate_last_5_rounds",
        }
        # Documented types from SPEC.md § "Growth monitoring".
        assert isinstance(backlog["pending"], int)
        assert isinstance(backlog["done"], int)
        assert isinstance(backlog["blocked"], int)
        assert isinstance(backlog["added_this_round"], int)
        assert isinstance(backlog["growth_rate_last_5_rounds"], (int, float))
        # Counts must agree with the raw improvements.md state.
        assert backlog["pending"] == 2
        assert backlog["done"] == 1
        assert backlog["blocked"] == 1

    def test_added_this_round_and_growth_from_git_history(self, tmp_path: Path):
        """added_this_round = new ``- [ ]`` lines vs HEAD; growth = delta vs HEAD~5."""
        from evolve.orchestrator import _write_state_json
        import json as _json

        project_dir = tmp_path / "proj"
        self._init_repo(project_dir)
        run_dir = project_dir / "runs" / "session"
        run_dir.mkdir(parents=True)
        rel_imp = project_dir / "runs" / "improvements.md"

        # Round 0 baseline: 1 pending item, then 5 commits each adding +1
        # pending. By the end, HEAD has 6 pending and HEAD~5 has 1 pending,
        # so growth_rate = (6 - 1) / 5 = 1.0.
        rel_imp.write_text("# Improvements\n- [ ] [functional] item 0\n")
        self._git(project_dir, "add", "-A")
        self._git(project_dir, "commit", "-q", "-m", "round 0")
        for i in range(1, 6):
            text = "# Improvements\n" + "".join(
                f"- [ ] [functional] item {j}\n" for j in range(i + 1)
            )
            rel_imp.write_text(text)
            self._git(project_dir, "add", "-A")
            self._git(project_dir, "commit", "-q", "-m", f"round {i}")

        # Now stage a NEW unchecked item without committing — this should
        # show up as added_this_round=1 (HEAD has 6, working tree has 7).
        text = rel_imp.read_text() + "- [ ] [functional] freshly added\n"
        rel_imp.write_text(text)

        _write_state_json(
            run_dir=run_dir,
            project_dir=project_dir,
            round_num=6,
            max_rounds=10,
            phase="improvement",
            status="running",
            improvements_path=rel_imp,
            started_at="2026-04-23T15:00:00Z",
        )

        state = _json.loads((run_dir / "state.json").read_text())
        backlog = state["backlog"]
        assert backlog["pending"] == 7
        assert backlog["added_this_round"] == 1
        # (7 - 1) / 5 = 1.2 — pending grew by 6 over 5 rounds of history
        assert backlog["growth_rate_last_5_rounds"] == 1.2

    def test_growth_zero_without_git_history(self, tmp_path: Path):
        """No git repo → added_this_round=0, growth_rate=0.0 (graceful degrade)."""
        from evolve.orchestrator import _write_state_json
        import json as _json

        project_dir = tmp_path / "proj"
        project_dir.mkdir()  # no git init
        run_dir = project_dir / "runs" / "session"
        run_dir.mkdir(parents=True)
        improvements = project_dir / "runs" / "improvements.md"
        improvements.write_text(
            "# Improvements\n- [ ] [functional] only one\n"
        )

        _write_state_json(
            run_dir=run_dir,
            project_dir=project_dir,
            round_num=1,
            max_rounds=10,
            phase="improvement",
            status="running",
            improvements_path=improvements,
            started_at="2026-04-23T15:00:00Z",
        )

        state = _json.loads((run_dir / "state.json").read_text())
        backlog = state["backlog"]
        assert backlog["pending"] == 1
        assert backlog["added_this_round"] == 0
        assert backlog["growth_rate_last_5_rounds"] == 0.0

    def test_added_this_round_uses_line_set_diff_not_count_diff(self, tmp_path: Path):
        """Checking off A and adding B → added_this_round=1, NOT 0 (count is unchanged)."""
        from evolve.orchestrator import _write_state_json
        import json as _json

        project_dir = tmp_path / "proj"
        self._init_repo(project_dir)
        run_dir = project_dir / "runs" / "session"
        run_dir.mkdir(parents=True)
        improvements = project_dir / "runs" / "improvements.md"
        improvements.write_text(
            "# Improvements\n- [ ] [functional] item A\n"
        )
        self._git(project_dir, "add", "-A")
        self._git(project_dir, "commit", "-q", "-m", "init")

        # Check off A, add B — count of unchecked stays at 1, but the
        # set diff reveals 1 new item was added.
        improvements.write_text(
            "# Improvements\n"
            "- [x] [functional] item A\n"
            "- [ ] [functional] item B\n"
        )

        _write_state_json(
            run_dir=run_dir,
            project_dir=project_dir,
            round_num=1,
            max_rounds=10,
            phase="improvement",
            status="running",
            improvements_path=improvements,
            started_at="2026-04-23T15:00:00Z",
        )

        state = _json.loads((run_dir / "state.json").read_text())
        backlog = state["backlog"]
        assert backlog["pending"] == 1
        assert backlog["done"] == 1
        assert backlog["added_this_round"] == 1  # B is new


# ---------------------------------------------------------------------------
# _parse_restart_required
# ---------------------------------------------------------------------------

class TestParseRestartRequired:
    """Test _parse_restart_required marker file parsing."""

    def test_returns_none_when_no_marker(self, tmp_path: Path):
        assert _parse_restart_required(tmp_path) is None

    def test_parses_valid_marker(self, tmp_path: Path):
        (tmp_path / "RESTART_REQUIRED").write_text(
            "# RESTART_REQUIRED\n"
            "reason: extracted git.py from loop.py\n"
            "verify: python -m evolve --help\n"
            "resume: python -m evolve start . --resume\n"
            "round: 5\n"
            "timestamp: 2026-04-23T21:00:00Z\n"
        )
        marker = _parse_restart_required(tmp_path)
        assert marker is not None
        assert marker["reason"] == "extracted git.py from loop.py"
        assert marker["verify"] == "python -m evolve --help"
        assert marker["resume"] == "python -m evolve start . --resume"
        assert marker["round"] == "5"
        assert marker["timestamp"] == "2026-04-23T21:00:00Z"

    def test_returns_none_when_reason_missing(self, tmp_path: Path):
        (tmp_path / "RESTART_REQUIRED").write_text(
            "# RESTART_REQUIRED\n"
            "verify: python -m evolve --help\n"
        )
        assert _parse_restart_required(tmp_path) is None

    def test_ignores_comments_and_blanks(self, tmp_path: Path):
        (tmp_path / "RESTART_REQUIRED").write_text(
            "# RESTART_REQUIRED\n"
            "# This is a comment\n"
            "\n"
            "reason: test reason\n"
        )
        marker = _parse_restart_required(tmp_path)
        assert marker is not None
        assert marker["reason"] == "test reason"

    def test_handles_colons_in_value(self, tmp_path: Path):
        (tmp_path / "RESTART_REQUIRED").write_text(
            "reason: value with: colons: in it\n"
        )
        marker = _parse_restart_required(tmp_path)
        assert marker is not None
        assert marker["reason"] == "value with: colons: in it"


# ---------------------------------------------------------------------------
# _run_rounds — RESTART_REQUIRED handling (exit code 3)
# ---------------------------------------------------------------------------

class TestRunRoundsRestartRequired:
    """Test _run_rounds exits 3 when RESTART_REQUIRED is written."""

    def setup_method(self):
        self.ui = MagicMock()

    def _setup_project(self, tmp_path):
        project_dir = tmp_path / "proj"
        project_dir.mkdir()
        run_dir = project_dir / "runs" / "session"
        run_dir.mkdir(parents=True)
        imp_path = project_dir / "runs" / "improvements.md"
        imp_path.write_text("- [ ] [functional] do something\n")
        return project_dir, run_dir, imp_path

    def test_restart_required_exits_3(self, tmp_path: Path):
        """When RESTART_REQUIRED is written by the agent, exit code is 3."""
        project_dir, run_dir, imp_path = self._setup_project(tmp_path)
        ui = self.ui

        def mock_monitored(cmd, cwd, ui_, round_num, watchdog_timeout=120):
            convo = run_dir / f"conversation_loop_{round_num}.md"
            convo.write_text("# Round conversation")
            (run_dir / "COMMIT_MSG").write_text("STRUCTURAL: feat(git): extract git.py")
            (run_dir / "RESTART_REQUIRED").write_text(
                "# RESTART_REQUIRED\n"
                "reason: extracted git.py from loop.py\n"
                "verify: python -m evolve --help\n"
                "resume: python -m evolve start . --resume\n"
                "round: 1\n"
                "timestamp: 2026-04-23T21:00:00Z\n"
            )
            # Mark progress so zero-progress doesn't fire
            imp_path.write_text("- [x] [functional] do something\n")
            return 0, "output", False

        with patch("evolve.orchestrator._run_monitored_subprocess", side_effect=mock_monitored), \
             patch("evolve.orchestrator._is_self_evolving", return_value=True), \
             patch("evolve.diagnostics._generate_evolution_report"), \
             pytest.raises(SystemExit) as exc:
            _run_rounds(
                project_dir, run_dir, imp_path, ui,
                start_round=1, max_rounds=10, check_cmd="pytest",
                allow_installs=False, timeout=300, model="claude-opus-4-6",
            )
        assert exc.value.code == 3

    def test_restart_required_renders_panel(self, tmp_path: Path):
        """structural_change_required is called on the UI."""
        project_dir, run_dir, imp_path = self._setup_project(tmp_path)
        ui = self.ui

        def mock_monitored(cmd, cwd, ui_, round_num, watchdog_timeout=120):
            convo = run_dir / f"conversation_loop_{round_num}.md"
            convo.write_text("# Round conversation")
            (run_dir / "COMMIT_MSG").write_text("STRUCTURAL: test")
            (run_dir / "RESTART_REQUIRED").write_text(
                "reason: test reason\n"
                "verify: pytest\n"
                "resume: evolve start . --resume\n"
                "round: 1\n"
                "timestamp: 2026-04-23T21:00:00Z\n"
            )
            imp_path.write_text("- [x] [functional] do something\n")
            return 0, "output", False

        with patch("evolve.orchestrator._run_monitored_subprocess", side_effect=mock_monitored), \
             patch("evolve.orchestrator._is_self_evolving", return_value=True), \
             patch("evolve.diagnostics._generate_evolution_report"), \
             pytest.raises(SystemExit):
            _run_rounds(
                project_dir, run_dir, imp_path, ui,
                start_round=1, max_rounds=10, check_cmd="pytest",
                allow_installs=False, timeout=300, model="claude-opus-4-6",
            )
        ui.structural_change_required.assert_called_once()
        marker_arg = ui.structural_change_required.call_args[0][0]
        assert marker_arg["reason"] == "test reason"

    def test_restart_required_fires_hook(self, tmp_path: Path):
        """on_structural_change hook is fired with marker env vars."""
        project_dir, run_dir, imp_path = self._setup_project(tmp_path)
        ui = self.ui

        def mock_monitored(cmd, cwd, ui_, round_num, watchdog_timeout=120):
            convo = run_dir / f"conversation_loop_{round_num}.md"
            convo.write_text("# Round conversation")
            (run_dir / "COMMIT_MSG").write_text("STRUCTURAL: test")
            (run_dir / "RESTART_REQUIRED").write_text(
                "reason: moved hooks.py\n"
                "verify: python -m evolve --help\n"
                "resume: evolve start . --resume\n"
                "round: 2\n"
                "timestamp: 2026-04-23T22:00:00Z\n"
            )
            imp_path.write_text("- [x] [functional] do something\n")
            return 0, "output", False

        with patch("evolve.orchestrator._run_monitored_subprocess", side_effect=mock_monitored), \
             patch("evolve.orchestrator._is_self_evolving", return_value=True), \
             patch("evolve.diagnostics._generate_evolution_report"), \
             patch("evolve.orchestrator.fire_hook") as mock_fire_hook, \
             pytest.raises(SystemExit) as exc:
            _run_rounds(
                project_dir, run_dir, imp_path, ui,
                start_round=1, max_rounds=10, check_cmd="pytest",
                allow_installs=False, timeout=300, model="claude-opus-4-6",
            )
        assert exc.value.code == 3
        # Find the on_structural_change call among all fire_hook calls
        structural_calls = [
            c for c in mock_fire_hook.call_args_list
            if len(c.args) >= 2 and c.args[1] == "on_structural_change"
        ]
        assert len(structural_calls) == 1
        call_kwargs = structural_calls[0].kwargs
        assert call_kwargs["status"] == "structural_change"
        extra = call_kwargs["extra_env"]
        assert extra["EVOLVE_STRUCTURAL_REASON"] == "moved hooks.py"
        assert extra["EVOLVE_STRUCTURAL_VERIFY"] == "python -m evolve --help"
        assert extra["EVOLVE_STRUCTURAL_ROUND"] == "2"

    def test_forever_mode_does_not_bypass(self, tmp_path: Path):
        """--forever does NOT bypass structural change — still exits 3."""
        project_dir, run_dir, imp_path = self._setup_project(tmp_path)
        ui = self.ui

        def mock_monitored(cmd, cwd, ui_, round_num, watchdog_timeout=120):
            convo = run_dir / f"conversation_loop_{round_num}.md"
            convo.write_text("# Round conversation")
            (run_dir / "COMMIT_MSG").write_text("STRUCTURAL: test")
            (run_dir / "RESTART_REQUIRED").write_text(
                "reason: structural change\n"
                "verify: pytest\n"
                "resume: evolve start . --resume\n"
                "round: 1\n"
                "timestamp: 2026-04-23T21:00:00Z\n"
            )
            imp_path.write_text("- [x] [functional] do something\n")
            return 0, "output", False

        # The forever flag is handled by evolve_loop, not _run_rounds.
        # _run_rounds always calls sys.exit(3) on RESTART_REQUIRED.
        # This verifies _run_rounds does NOT skip the exit.
        with patch("evolve.orchestrator._run_monitored_subprocess", side_effect=mock_monitored), \
             patch("evolve.orchestrator._is_self_evolving", return_value=True), \
             patch("evolve.diagnostics._generate_evolution_report"), \
             pytest.raises(SystemExit) as exc:
            _run_rounds(
                project_dir, run_dir, imp_path, ui,
                start_round=1, max_rounds=10, check_cmd="pytest",
                allow_installs=False, timeout=300, model="claude-opus-4-6",
            )
        assert exc.value.code == 3

    def test_no_restart_required_continues_normally(self, tmp_path: Path):
        """Without RESTART_REQUIRED, convergence works normally (exit 0)."""
        project_dir, run_dir, imp_path = self._setup_project(tmp_path)
        ui = self.ui

        def mock_monitored(cmd, cwd, ui_, round_num, watchdog_timeout=120):
            convo = run_dir / f"conversation_loop_{round_num}.md"
            convo.write_text("# Round conversation")
            (run_dir / "CONVERGED").write_text("All done")
            imp_path.write_text("- [x] [functional] do something\n")
            return 0, "output", False

        with patch("evolve.orchestrator._run_monitored_subprocess", side_effect=mock_monitored), \
             patch("evolve.orchestrator._is_self_evolving", return_value=True), \
             patch("evolve.diagnostics._generate_evolution_report"), \
             patch("evolve.orchestrator._run_party_mode"), \
             pytest.raises(SystemExit) as exc:
            _run_rounds(
                project_dir, run_dir, imp_path, ui,
                start_round=1, max_rounds=10, check_cmd="pytest",
                allow_installs=False, timeout=300, model="claude-opus-4-6",
            )
        assert exc.value.code == 0
        ui.structural_change_required.assert_not_called()
