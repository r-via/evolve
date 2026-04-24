"""Tests for `evolve diff` subcommand across agent.py and loop.py."""

import asyncio
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from evolve.agent import build_diff_prompt, run_diff_agent


# ---------------------------------------------------------------------------
# build_diff_prompt
# ---------------------------------------------------------------------------

class TestBuildDiffPrompt:
    """Tests for agent.build_diff_prompt."""

    def test_includes_spec(self, tmp_path: Path):
        (tmp_path / "README.md").write_text("# My Project\nDiff spec content")
        (tmp_path / "runs").mkdir()
        prompt = build_diff_prompt(tmp_path)
        assert "Diff spec content" in prompt

    def test_no_spec_fallback(self, tmp_path: Path):
        (tmp_path / "runs").mkdir()
        prompt = build_diff_prompt(tmp_path)
        assert "(no spec found)" in prompt

    def test_includes_improvements(self, tmp_path: Path):
        (tmp_path / "README.md").write_text("# P")
        runs = tmp_path / "runs"
        runs.mkdir()
        (runs / "improvements.md").write_text("- [x] [functional] Done thing\n")
        prompt = build_diff_prompt(tmp_path)
        assert "Done thing" in prompt

    def test_no_improvements_fallback(self, tmp_path: Path):
        (tmp_path / "README.md").write_text("# P")
        (tmp_path / "runs").mkdir()
        prompt = build_diff_prompt(tmp_path)
        assert "(none)" in prompt

    def test_diff_mode_instruction(self, tmp_path: Path):
        (tmp_path / "README.md").write_text("# P")
        (tmp_path / "runs").mkdir()
        prompt = build_diff_prompt(tmp_path)
        assert "DIFF" in prompt
        assert "MUST NOT modify" in prompt
        assert "gap" in prompt.lower() or "Gap" in prompt

    def test_run_dir_in_prompt(self, tmp_path: Path):
        (tmp_path / "README.md").write_text("# P")
        (tmp_path / "runs").mkdir()
        rdir = tmp_path / "runs" / "20260325_120000"
        rdir.mkdir()
        prompt = build_diff_prompt(tmp_path, run_dir=rdir)
        assert str(rdir) in prompt
        assert "diff_report.md" in prompt

    def test_run_dir_defaults_to_runs(self, tmp_path: Path):
        (tmp_path / "README.md").write_text("# P")
        (tmp_path / "runs").mkdir()
        prompt = build_diff_prompt(tmp_path)
        assert "runs/diff_report.md" in prompt

    def test_report_format_instructions(self, tmp_path: Path):
        """Prompt instructs agent to use pass/fail markers."""
        (tmp_path / "README.md").write_text("# P")
        (tmp_path / "runs").mkdir()
        prompt = build_diff_prompt(tmp_path)
        assert "\u2705" in prompt  # checkmark
        assert "\u274c" in prompt  # cross
        assert "Compliance" in prompt

    def test_no_check_cmd_section(self, tmp_path: Path):
        """Diff prompt does NOT include a check command section."""
        (tmp_path / "README.md").write_text("# P")
        (tmp_path / "runs").mkdir()
        prompt = build_diff_prompt(tmp_path)
        assert "## Check command" not in prompt

    def test_spec_flag_uses_custom_spec(self, tmp_path: Path):
        """--spec flag loads the specified file instead of README.md."""
        (tmp_path / "SPEC.md").write_text("# Custom Spec\nSpec-specific content")
        (tmp_path / "runs").mkdir()
        prompt = build_diff_prompt(tmp_path, spec="SPEC.md")
        assert "Spec-specific content" in prompt

    def test_readme_rst_fallback(self, tmp_path: Path):
        """Picks up README.rst when README.md doesn't exist."""
        (tmp_path / "README.rst").write_text("RST content here")
        (tmp_path / "runs").mkdir()
        prompt = build_diff_prompt(tmp_path)
        assert "RST content here" in prompt

    def test_lightweight_instruction(self, tmp_path: Path):
        """Prompt emphasizes lightweight/quick scan, not exhaustive."""
        (tmp_path / "README.md").write_text("# P")
        (tmp_path / "runs").mkdir()
        prompt = build_diff_prompt(tmp_path)
        assert "NOT verify exhaustively" in prompt or "not verify exhaustively" in prompt.lower()


# ---------------------------------------------------------------------------
# run_diff_agent — retry and error handling
# ---------------------------------------------------------------------------

class TestRunDiffAgent:
    """Tests for agent.run_diff_agent with mocked SDK."""

    def test_sdk_not_installed(self, tmp_path: Path):
        """Graceful skip when claude-agent-sdk is missing."""
        import builtins
        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "claude_agent_sdk":
                raise ImportError("no module")
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=fake_import):
            run_diff_agent(tmp_path)

    def test_benign_runtime_error_ignored(self, tmp_path: Path):
        """Benign async teardown errors are silently ignored."""
        (tmp_path / "README.md").write_text("# P")
        rdir = tmp_path / "runs" / "session"
        rdir.mkdir(parents=True)

        def mock_asyncio_run(coro):
            coro.close()
            raise RuntimeError("cancel scope")

        with patch("evolve.agent.asyncio.run", side_effect=mock_asyncio_run), \
             patch.dict("sys.modules", {"claude_agent_sdk": MagicMock()}):
            run_diff_agent(tmp_path, run_dir=rdir)

    def test_rate_limit_retry(self, tmp_path: Path):
        """Rate-limit errors trigger backoff retries."""
        (tmp_path / "README.md").write_text("# P")
        rdir = tmp_path / "runs" / "session"
        rdir.mkdir(parents=True)

        call_count = 0

        def mock_asyncio_run(coro):
            nonlocal call_count
            coro.close()
            call_count += 1
            if call_count < 2:
                raise Exception("rate_limit exceeded")

        with patch("evolve.agent.asyncio.run", side_effect=mock_asyncio_run), \
             patch("evolve.agent.time.sleep") as mock_sleep, \
             patch.dict("sys.modules", {"claude_agent_sdk": MagicMock()}):
            run_diff_agent(tmp_path, run_dir=rdir, max_retries=3)
            assert call_count == 2
            mock_sleep.assert_called_once_with(60)

    def test_non_retryable_error(self, tmp_path: Path):
        """Non-retryable errors give up immediately."""
        (tmp_path / "README.md").write_text("# P")
        rdir = tmp_path / "runs" / "session"
        rdir.mkdir(parents=True)

        def mock_asyncio_run(coro):
            coro.close()
            raise ValueError("unexpected SDK error")

        with patch("evolve.agent.asyncio.run", side_effect=mock_asyncio_run), \
             patch.dict("sys.modules", {"claude_agent_sdk": MagicMock()}):
            run_diff_agent(tmp_path, run_dir=rdir)

    def test_creates_run_dir(self, tmp_path: Path):
        """run_dir is created if it doesn't exist."""
        (tmp_path / "README.md").write_text("# P")
        rdir = tmp_path / "runs" / "new_session"

        def mock_asyncio_run(coro):
            coro.close()

        with patch("evolve.agent.asyncio.run", side_effect=mock_asyncio_run), \
             patch.dict("sys.modules", {"claude_agent_sdk": MagicMock()}):
            run_diff_agent(tmp_path, run_dir=rdir)
            assert rdir.is_dir()


# ---------------------------------------------------------------------------
# run_diff (loop.py orchestrator)
# ---------------------------------------------------------------------------

class TestRunDiff:
    """Tests for loop.run_diff — the orchestrator function."""

    def test_creates_session_dir(self, tmp_path: Path):
        """A timestamped session directory is created."""
        (tmp_path / "README.md").write_text("# P")
        (tmp_path / "runs").mkdir()

        with patch("evolve.agent.run_diff_agent"):
            from evolve.orchestrator import run_diff
            run_diff(tmp_path)

        sessions = [d for d in (tmp_path / "runs").iterdir() if d.is_dir()]
        assert len(sessions) >= 1

    def test_exit_0_all_present(self, tmp_path: Path):
        """Returns exit code 0 when all sections are present."""
        (tmp_path / "README.md").write_text("# P")
        (tmp_path / "runs").mkdir()

        def create_report(**kwargs):
            rd = kwargs.get("run_dir")
            if rd:
                (rd / "diff_report.md").write_text(
                    "# Diff Report\n"
                    "- \u2705 **CLI flags** \u2014 present\n"
                    "- \u2705 **TUI** \u2014 present\n"
                    "## Summary\nCompliance: 100%\n"
                )

        with patch("evolve.agent.run_diff_agent", side_effect=create_report):
            from evolve.orchestrator import run_diff
            result = run_diff(tmp_path)
            assert result == 0

    def test_exit_1_gaps_found(self, tmp_path: Path):
        """Returns exit code 1 when gaps are found."""
        (tmp_path / "README.md").write_text("# P")
        (tmp_path / "runs").mkdir()

        def create_report(**kwargs):
            rd = kwargs.get("run_dir")
            if rd:
                (rd / "diff_report.md").write_text(
                    "# Diff Report\n"
                    "- \u2705 **CLI flags** \u2014 present\n"
                    "- \u274c **Diff subcommand** \u2014 missing\n"
                    "## Summary\nCompliance: 50%\n"
                )

        with patch("evolve.agent.run_diff_agent", side_effect=create_report):
            from evolve.orchestrator import run_diff
            result = run_diff(tmp_path)
            assert result == 1

    def test_exit_2_no_report(self, tmp_path: Path):
        """Returns exit code 2 when no report is produced."""
        (tmp_path / "README.md").write_text("# P")
        (tmp_path / "runs").mkdir()

        with patch("evolve.agent.run_diff_agent"):
            from evolve.orchestrator import run_diff
            result = run_diff(tmp_path)
            assert result == 2

    def test_exit_2_no_markers(self, tmp_path: Path):
        """Returns exit code 2 when report has no pass/fail markers."""
        (tmp_path / "README.md").write_text("# P")
        (tmp_path / "runs").mkdir()

        def create_report(**kwargs):
            rd = kwargs.get("run_dir")
            if rd:
                (rd / "diff_report.md").write_text(
                    "# Diff Report\nEmpty report\n"
                )

        with patch("evolve.agent.run_diff_agent", side_effect=create_report):
            from evolve.orchestrator import run_diff
            result = run_diff(tmp_path)
            assert result == 2

    def test_exit_2_spec_not_found(self, tmp_path: Path):
        """Returns exit code 2 when spec file does not exist."""
        (tmp_path / "runs").mkdir()

        from evolve.orchestrator import run_diff
        result = run_diff(tmp_path, spec="NONEXISTENT.md")
        assert result == 2

    def test_model_passed_to_agent(self, tmp_path: Path):
        """Model parameter is passed through to the agent module."""
        (tmp_path / "README.md").write_text("# P")
        (tmp_path / "runs").mkdir()

        import evolve.agent as _agent_mod

        with patch("evolve.agent.run_diff_agent"):
            from evolve.orchestrator import run_diff
            run_diff(tmp_path, model="claude-sonnet-4-20250514")
            assert _agent_mod.MODEL == "claude-sonnet-4-20250514"

        # Reset
        _agent_mod.MODEL = "claude-opus-4-6"

    def test_effort_default_low(self, tmp_path: Path):
        """Default effort for diff is 'low'."""
        (tmp_path / "README.md").write_text("# P")
        (tmp_path / "runs").mkdir()

        import evolve.agent as _agent_mod

        with patch("evolve.agent.run_diff_agent"):
            from evolve.orchestrator import run_diff
            run_diff(tmp_path)
            assert _agent_mod.EFFORT == "low"

        # Reset
        _agent_mod.EFFORT = "max"

    def test_effort_override(self, tmp_path: Path):
        """Explicit effort parameter overrides the default."""
        (tmp_path / "README.md").write_text("# P")
        (tmp_path / "runs").mkdir()

        import evolve.agent as _agent_mod

        with patch("evolve.agent.run_diff_agent"):
            from evolve.orchestrator import run_diff
            run_diff(tmp_path, effort="high")
            assert _agent_mod.EFFORT == "high"

        # Reset
        _agent_mod.EFFORT = "max"

    def test_does_not_run_check_cmd(self, tmp_path: Path):
        """Diff does NOT run any check command (SPEC says so)."""
        (tmp_path / "README.md").write_text("# P")
        (tmp_path / "runs").mkdir()

        with patch("evolve.agent.run_diff_agent"), \
             patch("evolve.orchestrator.subprocess.run") as mock_sub:
            from evolve.orchestrator import run_diff
            run_diff(tmp_path)
            mock_sub.assert_not_called()


# ---------------------------------------------------------------------------
# _run_diff_claude_agent — mocked SDK
# ---------------------------------------------------------------------------

class TestRunDiffClaudeAgent:
    """Tests for agent._run_diff_claude_agent with mocked SDK."""

    def test_logs_conversation(self, tmp_path: Path):
        """Conversation log is written to diff_conversation.md."""
        run_dir = tmp_path / "runs" / "session"
        run_dir.mkdir(parents=True)

        class MockTextBlock:
            def __init__(self, text):
                self.text = text

        class AM:
            def __init__(self, content):
                self.content = content

        class RM:
            def __init__(self, content):
                self.content = content

        messages = [AM([MockTextBlock("Diff analysis complete")])]

        async def mock_query(prompt, options):
            for m in messages:
                yield m

        mock_sdk = MagicMock()
        mock_sdk.query = mock_query
        mock_sdk.ClaudeAgentOptions = lambda **kw: None
        mock_sdk.AssistantMessage = AM
        mock_sdk.ResultMessage = RM

        with patch.dict("sys.modules", {"claude_agent_sdk": mock_sdk}):
            from evolve.agent import _run_diff_claude_agent
            asyncio.run(_run_diff_claude_agent(
                "test prompt", tmp_path, run_dir
            ))

        log = run_dir / "diff_conversation.md"
        assert log.is_file()
        content = log.read_text()
        assert "Diff analysis complete" in content
        assert "Diff Analysis" in content

    def test_sdk_error_logged(self, tmp_path: Path):
        """SDK exceptions are caught and logged."""
        run_dir = tmp_path / "runs" / "session"
        run_dir.mkdir(parents=True)

        class AM:
            pass

        class RM:
            pass

        async def mock_query(prompt, options):
            raise RuntimeError("SDK blew up")
            yield  # make it a generator  # noqa: E501

        mock_sdk = MagicMock()
        mock_sdk.query = mock_query
        mock_sdk.ClaudeAgentOptions = lambda **kw: None
        mock_sdk.AssistantMessage = AM
        mock_sdk.ResultMessage = RM

        with patch.dict("sys.modules", {"claude_agent_sdk": mock_sdk}):
            from evolve.agent import _run_diff_claude_agent
            asyncio.run(_run_diff_claude_agent(
                "test prompt", tmp_path, run_dir
            ))

        content = (run_dir / "diff_conversation.md").read_text()
        assert "SDK error" in content


# ---------------------------------------------------------------------------
# CLI integration — evolve diff subcommand parsing
# ---------------------------------------------------------------------------

class TestDiffCLI:
    """Tests for the `evolve diff` CLI subcommand argument parsing."""

    def test_diff_subcommand_recognized(self, tmp_path: Path):
        """The `diff` subcommand is recognized by the argparser."""
        import sys
        (tmp_path / "README.md").write_text("# P")
        with patch.object(sys, "argv", ["evolve", "diff", str(tmp_path)]):
            from evolve import main
            with patch("evolve.orchestrator.run_diff", return_value=0) as mock_diff, \
                 patch("evolve._check_deps"):
                try:
                    main()
                except SystemExit as e:
                    assert e.code == 0
                mock_diff.assert_called_once()

    def test_diff_spec_flag(self, tmp_path: Path):
        """The --spec flag is passed through to run_diff."""
        import sys
        (tmp_path / "SPEC.md").write_text("# Spec")
        with patch.object(sys, "argv", ["evolve", "diff", str(tmp_path), "--spec", "SPEC.md"]):
            from evolve import main
            with patch("evolve.orchestrator.run_diff", return_value=0) as mock_diff, \
                 patch("evolve._check_deps"):
                try:
                    main()
                except SystemExit as e:
                    assert e.code == 0
                call_kwargs = mock_diff.call_args
                assert call_kwargs.kwargs.get("spec") == "SPEC.md" or \
                       (call_kwargs[1].get("spec") == "SPEC.md" if len(call_kwargs) > 1 else False)

    def test_diff_default_effort_low(self, tmp_path: Path):
        """Default effort for diff subcommand is 'low'."""
        import os, sys
        (tmp_path / "README.md").write_text("# P")
        env = {k: v for k, v in os.environ.items() if k != "EVOLVE_EFFORT"}
        with patch.object(sys, "argv", ["evolve", "diff", str(tmp_path)]), \
             patch.dict(os.environ, env, clear=True):
            from evolve import main
            with patch("evolve.orchestrator.run_diff", return_value=0) as mock_diff, \
                 patch("evolve._check_deps"):
                try:
                    main()
                except SystemExit as e:
                    assert e.code == 0
                call_kwargs = mock_diff.call_args
                effort = call_kwargs.kwargs.get("effort") or \
                         (call_kwargs[1].get("effort") if len(call_kwargs) > 1 else None)
                assert effort == "low"
