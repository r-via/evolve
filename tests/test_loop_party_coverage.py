"""Coverage tests split from test_loop_coverage.py:
party-mode result handling, retry paths, agent read errors, workflow fallback,
plus two non-party tests interleaved in the original file
(TestEvolveLoopAutoDetect, TestForeverRestartConvergedFile).

The agent-loading, prompt-content, missing-workflow, and end-to-end cases
live in ``test_loop_party_advanced.py`` to keep both modules under the
500-line cap.
"""

from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from evolve.orchestrator import evolve_loop
from evolve.party import _run_party_mode


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

        with patch("evolve.orchestrator._auto_detect_check", return_value="pytest") as mock_detect, \
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

        with patch("evolve.orchestrator._auto_detect_check", return_value=None) as mock_detect, \
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

        with patch("evolve.orchestrator._auto_detect_check") as mock_detect, \
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
