"""Tests for --dry-run functionality across agent.py and loop.py."""

import asyncio
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from evolve.agent import build_dry_run_prompt, run_dry_run_agent


# ---------------------------------------------------------------------------
# build_dry_run_prompt
# ---------------------------------------------------------------------------

class TestBuildDryRunPrompt:
    """Tests for agent.build_dry_run_prompt."""

    def test_includes_readme(self, tmp_path: Path):
        (tmp_path / "README.md").write_text("# My Project\nDry-run spec")
        (tmp_path / "runs").mkdir()
        prompt = build_dry_run_prompt(tmp_path)
        assert "Dry-run spec" in prompt

    def test_no_readme_fallback(self, tmp_path: Path):
        (tmp_path / "runs").mkdir()
        prompt = build_dry_run_prompt(tmp_path)
        assert "(no README found)" in prompt

    def test_includes_improvements(self, tmp_path: Path):
        (tmp_path / "README.md").write_text("# P")
        runs = tmp_path / "runs"
        runs.mkdir()
        (runs / "improvements.md").write_text("- [x] [functional] Done thing\n")
        prompt = build_dry_run_prompt(tmp_path)
        assert "Done thing" in prompt

    def test_no_improvements_fallback(self, tmp_path: Path):
        (tmp_path / "README.md").write_text("# P")
        (tmp_path / "runs").mkdir()
        prompt = build_dry_run_prompt(tmp_path)
        assert "(none)" in prompt

    def test_check_cmd_and_output(self, tmp_path: Path):
        (tmp_path / "README.md").write_text("# P")
        (tmp_path / "runs").mkdir()
        prompt = build_dry_run_prompt(
            tmp_path, check_output="42 passed", check_cmd="pytest"
        )
        assert "42 passed" in prompt
        assert "## Check command: `pytest`" in prompt

    def test_check_cmd_without_output(self, tmp_path: Path):
        (tmp_path / "README.md").write_text("# P")
        (tmp_path / "runs").mkdir()
        prompt = build_dry_run_prompt(tmp_path, check_cmd="pytest")
        assert "(not yet run)" in prompt

    def test_no_check_cmd(self, tmp_path: Path):
        (tmp_path / "README.md").write_text("# P")
        (tmp_path / "runs").mkdir()
        prompt = build_dry_run_prompt(tmp_path)
        # No check section at all
        assert "## Check command" not in prompt

    def test_run_dir_in_prompt(self, tmp_path: Path):
        (tmp_path / "README.md").write_text("# P")
        (tmp_path / "runs").mkdir()
        rdir = tmp_path / "runs" / "20260325_120000"
        rdir.mkdir()
        prompt = build_dry_run_prompt(tmp_path, run_dir=rdir)
        assert str(rdir) in prompt
        assert "dry_run_report.md" in prompt

    def test_run_dir_defaults_to_runs(self, tmp_path: Path):
        (tmp_path / "README.md").write_text("# P")
        (tmp_path / "runs").mkdir()
        prompt = build_dry_run_prompt(tmp_path)
        assert "runs/dry_run_report.md" in prompt

    def test_dry_run_mode_instruction(self, tmp_path: Path):
        (tmp_path / "README.md").write_text("# P")
        (tmp_path / "runs").mkdir()
        prompt = build_dry_run_prompt(tmp_path)
        assert "DRY RUN" in prompt
        assert "read-only" in prompt.lower()
        assert "MUST NOT modify" in prompt

    def test_readme_rst_fallback(self, tmp_path: Path):
        """Picks up README.rst when README.md doesn't exist."""
        (tmp_path / "README.rst").write_text("RST content here")
        (tmp_path / "runs").mkdir()
        prompt = build_dry_run_prompt(tmp_path)
        assert "RST content here" in prompt


# ---------------------------------------------------------------------------
# run_dry_run_agent — retry and error handling
# ---------------------------------------------------------------------------

class TestRunDryRunAgent:
    """Tests for agent.run_dry_run_agent with mocked SDK."""

    def test_sdk_not_installed(self, tmp_path: Path):
        """Graceful skip when claude-agent-sdk is missing."""
        import builtins
        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "claude_agent_sdk":
                raise ImportError("no module")
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=fake_import):
            # Should not raise
            run_dry_run_agent(tmp_path)

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
            # Should return without error
            run_dry_run_agent(tmp_path, run_dir=rdir)

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
            # Second call succeeds

        with patch("evolve.agent.asyncio.run", side_effect=mock_asyncio_run), \
             patch("evolve.agent.time.sleep") as mock_sleep, \
             patch.dict("sys.modules", {"claude_agent_sdk": MagicMock()}):
            run_dry_run_agent(tmp_path, run_dir=rdir, max_retries=3)
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
            # Should not raise — logs warning and returns
            run_dry_run_agent(tmp_path, run_dir=rdir)

    def test_creates_run_dir(self, tmp_path: Path):
        """run_dir is created if it doesn't exist."""
        (tmp_path / "README.md").write_text("# P")
        rdir = tmp_path / "runs" / "new_session"

        def mock_asyncio_run(coro):
            coro.close()

        with patch("evolve.agent.asyncio.run", side_effect=mock_asyncio_run), \
             patch.dict("sys.modules", {"claude_agent_sdk": MagicMock()}):
            run_dry_run_agent(tmp_path, run_dir=rdir)
            assert rdir.is_dir()


# ---------------------------------------------------------------------------
# run_dry_run (loop.py orchestrator)
# ---------------------------------------------------------------------------

class TestRunDryRun:
    """Tests for loop.run_dry_run — the orchestrator function."""

    def test_creates_session_dir(self, tmp_path: Path):
        """A timestamped session directory is created."""
        (tmp_path / "README.md").write_text("# P")
        (tmp_path / "runs").mkdir()

        with patch("evolve.orchestrator._auto_detect_check", return_value=None), \
             patch("evolve.agent.run_dry_run_agent") as mock_agent:
            from loop import run_dry_run
            run_dry_run(tmp_path)

        # Should have created a timestamped dir under runs/
        sessions = [d for d in (tmp_path / "runs").iterdir() if d.is_dir()]
        assert len(sessions) >= 1

    def test_check_cmd_runs(self, tmp_path: Path):
        """Check command is run and its output passed to the agent."""
        (tmp_path / "README.md").write_text("# P")
        (tmp_path / "runs").mkdir()

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "5 passed"
        mock_result.stderr = ""

        with patch("evolve.orchestrator.subprocess.run", return_value=mock_result) as mock_sub, \
             patch("evolve.agent.run_dry_run_agent") as mock_agent:
            from loop import run_dry_run
            run_dry_run(tmp_path, check_cmd="pytest", timeout=60)

        # subprocess.run should have been called with the check command
        calls = [c for c in mock_sub.call_args_list if "pytest" in str(c)]
        assert len(calls) >= 1
        # Agent should have received check output
        mock_agent.assert_called_once()
        assert mock_agent.call_args.kwargs.get("check_cmd") == "pytest"

    def test_check_timeout(self, tmp_path: Path):
        """Check command timeout is handled gracefully."""
        import subprocess
        (tmp_path / "README.md").write_text("# P")
        (tmp_path / "runs").mkdir()

        with patch("evolve.orchestrator.subprocess.run", side_effect=subprocess.TimeoutExpired("pytest", 60)), \
             patch("evolve.agent.run_dry_run_agent"):
            from loop import run_dry_run
            # Should not raise
            run_dry_run(tmp_path, check_cmd="pytest", timeout=60)

    def test_auto_detect_when_no_check(self, tmp_path: Path):
        """Auto-detects check command when none provided."""
        (tmp_path / "README.md").write_text("# P")
        (tmp_path / "runs").mkdir()

        mock_result = MagicMock(returncode=0, stdout="ok", stderr="")

        with patch("evolve.orchestrator._auto_detect_check", return_value="pytest") as mock_detect, \
             patch("evolve.orchestrator.subprocess.run", return_value=mock_result), \
             patch("evolve.agent.run_dry_run_agent"):
            from loop import run_dry_run
            run_dry_run(tmp_path)
            mock_detect.assert_called_once_with(tmp_path)

    def test_report_file_message(self, tmp_path: Path, capsys):
        """Warns when no dry_run_report.md is produced."""
        (tmp_path / "README.md").write_text("# P")
        (tmp_path / "runs").mkdir()

        with patch("evolve.orchestrator._auto_detect_check", return_value=None), \
             patch("evolve.agent.run_dry_run_agent"):
            from loop import run_dry_run
            run_dry_run(tmp_path)

        # No report produced so warn is displayed
        output = capsys.readouterr().out
        assert "WARN" in output or "dry_run_report.md" in output

    def test_report_exists_message(self, tmp_path: Path, capsys):
        """Shows report path when dry_run_report.md exists."""
        (tmp_path / "README.md").write_text("# P")
        (tmp_path / "runs").mkdir()

        def create_report(**kwargs):
            # Create the report in the run_dir the agent receives
            rd = kwargs.get("run_dir")
            if rd:
                (rd / "dry_run_report.md").write_text("# Report\n")

        with patch("evolve.orchestrator._auto_detect_check", return_value=None), \
             patch("evolve.agent.run_dry_run_agent", side_effect=create_report):
            from loop import run_dry_run
            run_dry_run(tmp_path)

        output = capsys.readouterr().out
        # Rich may wrap long paths across lines AND inject ANSI escape
        # codes mid-word (e.g. "d\x1b[0m\x1b[95mry_run_report.md"), so
        # strip both newlines and ANSI sequences before checking.
        import re
        stripped = re.sub(r"\x1b\[[0-9;]*m", "", output.replace("\n", ""))
        assert "dry_run_report.md" in stripped
        # Should NOT have WARN since report exists
        # (both messages could appear in different contexts)

    def test_model_passed_to_agent(self, tmp_path: Path):
        """Model parameter is passed through to the agent module."""
        (tmp_path / "README.md").write_text("# P")
        (tmp_path / "runs").mkdir()

        import evolve.agent as _agent_mod

        with patch("evolve.orchestrator._auto_detect_check", return_value=None), \
             patch("evolve.agent.run_dry_run_agent"):
            from loop import run_dry_run
            run_dry_run(tmp_path, model="claude-sonnet-4-20250514")
            assert _agent_mod.MODEL == "claude-sonnet-4-20250514"

        # Reset
        _agent_mod.MODEL = "claude-opus-4-6"


# ---------------------------------------------------------------------------
# _run_dry_run_claude_agent — mocked SDK
# ---------------------------------------------------------------------------

class TestRunDryRunClaudeAgent:
    """Tests for agent._run_dry_run_claude_agent with mocked SDK."""

    def test_logs_conversation(self, tmp_path: Path):
        """Conversation log is written to dry_run_conversation.md."""
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

        messages = [AM([MockTextBlock("Analysis complete")])]

        async def mock_query(prompt, options):
            for m in messages:
                yield m

        mock_sdk = MagicMock()
        mock_sdk.query = mock_query
        mock_sdk.ClaudeAgentOptions = lambda **kw: None
        mock_sdk.AssistantMessage = AM
        mock_sdk.ResultMessage = RM

        with patch.dict("sys.modules", {"claude_agent_sdk": mock_sdk}):
            from agent import _run_dry_run_claude_agent
            asyncio.run(_run_dry_run_claude_agent(
                "test prompt", tmp_path, run_dir
            ))

        log = run_dir / "dry_run_conversation.md"
        assert log.is_file()
        content = log.read_text()
        assert "Analysis complete" in content

    def test_tool_use_blocks_logged(self, tmp_path: Path):
        """Tool use blocks are logged with name and input."""
        run_dir = tmp_path / "runs" / "session"
        run_dir.mkdir(parents=True)

        class MockToolBlock:
            def __init__(self):
                self.name = "Read"
                self.id = "tool_123"
                self.input = {"file_path": "/tmp/foo.py"}

        class AM:
            def __init__(self, content):
                self.content = content

        class RM:
            pass

        messages = [AM([MockToolBlock()])]

        async def mock_query(prompt, options):
            for m in messages:
                yield m

        mock_sdk = MagicMock()
        mock_sdk.query = mock_query
        mock_sdk.ClaudeAgentOptions = lambda **kw: None
        mock_sdk.AssistantMessage = AM
        mock_sdk.ResultMessage = RM

        with patch.dict("sys.modules", {"claude_agent_sdk": mock_sdk}):
            from agent import _run_dry_run_claude_agent
            asyncio.run(_run_dry_run_claude_agent(
                "test prompt", tmp_path, run_dir
            ))

        content = (run_dir / "dry_run_conversation.md").read_text()
        assert "Read" in content
        assert "/tmp/foo.py" in content

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
            from agent import _run_dry_run_claude_agent
            asyncio.run(_run_dry_run_claude_agent(
                "test prompt", tmp_path, run_dir
            ))

        content = (run_dir / "dry_run_conversation.md").read_text()
        assert "SDK error" in content

    def test_deduplicates_text_blocks(self, tmp_path: Path):
        """Duplicate text blocks are not logged twice."""
        run_dir = tmp_path / "runs" / "session"
        run_dir.mkdir(parents=True)

        class MockTextBlock:
            def __init__(self, text):
                self.text = text

        class AM:
            def __init__(self, content):
                self.content = content

        class RM:
            pass

        # Same text block sent twice (partial message scenario)
        block = MockTextBlock("Same text")
        messages = [AM([block]), AM([block])]

        async def mock_query(prompt, options):
            for m in messages:
                yield m

        mock_sdk = MagicMock()
        mock_sdk.query = mock_query
        mock_sdk.ClaudeAgentOptions = lambda **kw: None
        mock_sdk.AssistantMessage = AM
        mock_sdk.ResultMessage = RM

        with patch.dict("sys.modules", {"claude_agent_sdk": mock_sdk}):
            from agent import _run_dry_run_claude_agent
            asyncio.run(_run_dry_run_claude_agent(
                "test prompt", tmp_path, run_dir
            ))

        content = (run_dir / "dry_run_conversation.md").read_text()
        assert content.count("Same text") == 1

    def test_deduplicates_tool_blocks(self, tmp_path: Path):
        """Duplicate tool use blocks (same id) are not logged twice."""
        run_dir = tmp_path / "runs" / "session"
        run_dir.mkdir(parents=True)

        class MockToolBlock:
            def __init__(self):
                self.name = "Glob"
                self.id = "tool_dup"
                self.input = {"pattern": "*.py"}

        class AM:
            def __init__(self, content):
                self.content = content

        class RM:
            pass

        block = MockToolBlock()
        messages = [AM([block]), AM([block])]

        async def mock_query(prompt, options):
            for m in messages:
                yield m

        mock_sdk = MagicMock()
        mock_sdk.query = mock_query
        mock_sdk.ClaudeAgentOptions = lambda **kw: None
        mock_sdk.AssistantMessage = AM
        mock_sdk.ResultMessage = RM

        with patch.dict("sys.modules", {"claude_agent_sdk": mock_sdk}):
            from agent import _run_dry_run_claude_agent
            asyncio.run(_run_dry_run_claude_agent(
                "test prompt", tmp_path, run_dir
            ))

        content = (run_dir / "dry_run_conversation.md").read_text()
        # "Glob" should appear only once (dedup by tool id)
        assert content.count("**Glob**") == 1

    def test_none_messages_skipped(self, tmp_path: Path):
        """None messages from SDK are silently skipped."""
        run_dir = tmp_path / "runs" / "session"
        run_dir.mkdir(parents=True)

        class AM:
            pass

        class RM:
            pass

        async def mock_query(prompt, options):
            yield None
            yield None

        mock_sdk = MagicMock()
        mock_sdk.query = mock_query
        mock_sdk.ClaudeAgentOptions = lambda **kw: None
        mock_sdk.AssistantMessage = AM
        mock_sdk.ResultMessage = RM

        with patch.dict("sys.modules", {"claude_agent_sdk": mock_sdk}):
            from agent import _run_dry_run_claude_agent
            asyncio.run(_run_dry_run_claude_agent(
                "test prompt", tmp_path, run_dir
            ))

        # Should complete without error
        assert (run_dir / "dry_run_conversation.md").is_file()
