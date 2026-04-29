import evolve.infrastructure.claude_sdk.runtime as _rt_mod
"""Tests for --validate functionality across agent.py and loop.py."""

import asyncio
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from evolve.agent import build_validate_prompt, run_validate_agent
class TestBuildValidatePrompt:
    def test_includes_readme(self, tmp_path: Path):
        (tmp_path / "README.md").write_text("# My Project\nValidation spec")
        (tmp_path / "runs").mkdir()
        prompt = build_validate_prompt(tmp_path)
        assert "Validation spec" in prompt
    def test_no_readme_fallback(self, tmp_path: Path):
        (tmp_path / "runs").mkdir()
        prompt = build_validate_prompt(tmp_path)
        assert "(no README found)" in prompt
    def test_includes_improvements(self, tmp_path: Path):
        (tmp_path / "README.md").write_text("# P")
        runs = tmp_path / "runs"
        runs.mkdir()
        (runs / "improvements.md").write_text("- [x] [functional] Done thing\n")
        prompt = build_validate_prompt(tmp_path)
        assert "Done thing" in prompt
    def test_no_improvements_fallback(self, tmp_path: Path):
        (tmp_path / "README.md").write_text("# P")
        (tmp_path / "runs").mkdir()
        prompt = build_validate_prompt(tmp_path)
        assert "(none)" in prompt
    def test_check_cmd_and_output(self, tmp_path: Path):
        (tmp_path / "README.md").write_text("# P")
        (tmp_path / "runs").mkdir()
        prompt = build_validate_prompt(
            tmp_path, check_output="42 passed", check_cmd="pytest"
        )
        assert "42 passed" in prompt
        assert "## Check command: `pytest`" in prompt
    def test_check_cmd_without_output(self, tmp_path: Path):
        (tmp_path / "README.md").write_text("# P")
        (tmp_path / "runs").mkdir()
        prompt = build_validate_prompt(tmp_path, check_cmd="pytest")
        assert "(not yet run)" in prompt
    def test_no_check_cmd(self, tmp_path: Path):
        (tmp_path / "README.md").write_text("# P")
        (tmp_path / "runs").mkdir()
        prompt = build_validate_prompt(tmp_path)
        assert "## Check command" not in prompt
    def test_run_dir_in_prompt(self, tmp_path: Path):
        (tmp_path / "README.md").write_text("# P")
        (tmp_path / "runs").mkdir()
        rdir = tmp_path / "runs" / "20260325_120000"
        rdir.mkdir()
        prompt = build_validate_prompt(tmp_path, run_dir=rdir)
        assert str(rdir) in prompt
        assert "validate_report.md" in prompt
    def test_run_dir_defaults_to_runs(self, tmp_path: Path):
        (tmp_path / "README.md").write_text("# P")
        (tmp_path / "runs").mkdir()
        prompt = build_validate_prompt(tmp_path)
        assert "runs/validate_report.md" in prompt
    def test_validate_mode_instruction(self, tmp_path: Path):
        (tmp_path / "README.md").write_text("# P")
        (tmp_path / "runs").mkdir()
        prompt = build_validate_prompt(tmp_path)
        assert "VALIDATE" in prompt
        assert "spec compliance" in prompt.lower()
        assert "MUST NOT modify" in prompt
    def test_readme_rst_fallback(self, tmp_path: Path):
        (tmp_path / "README.rst").write_text("RST content here")
        (tmp_path / "runs").mkdir()
        prompt = build_validate_prompt(tmp_path)
        assert "RST content here" in prompt
    def test_report_format_instructions(self, tmp_path: Path):
        (tmp_path / "README.md").write_text("# P")
        (tmp_path / "runs").mkdir()
        prompt = build_validate_prompt(tmp_path)
        assert "✅" in prompt
        assert "❌" in prompt
        assert "Compliance" in prompt
class TestRunValidateAgent:
    def test_sdk_not_installed(self, tmp_path: Path):
        import builtins
        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "claude_agent_sdk":
                raise ImportError("no module")
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=fake_import):
            run_validate_agent(tmp_path)
    def test_benign_runtime_error_ignored(self, tmp_path: Path):
        (tmp_path / "README.md").write_text("# P")
        rdir = tmp_path / "runs" / "session"
        rdir.mkdir(parents=True)

        def mock_asyncio_run(coro):
            coro.close()
            raise RuntimeError("cancel scope")

        with patch("evolve.agent.asyncio.run", side_effect=mock_asyncio_run), \
             patch.dict("sys.modules", {"claude_agent_sdk": MagicMock()}):
            run_validate_agent(tmp_path, run_dir=rdir)
    def test_rate_limit_retry(self, tmp_path: Path):
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
            run_validate_agent(tmp_path, run_dir=rdir, max_retries=3)
            assert call_count == 2
            mock_sleep.assert_called_once_with(60)
    def test_non_retryable_error(self, tmp_path: Path):
        (tmp_path / "README.md").write_text("# P")
        rdir = tmp_path / "runs" / "session"
        rdir.mkdir(parents=True)

        def mock_asyncio_run(coro):
            coro.close()
            raise ValueError("unexpected SDK error")

        with patch("evolve.agent.asyncio.run", side_effect=mock_asyncio_run), \
             patch.dict("sys.modules", {"claude_agent_sdk": MagicMock()}):
            run_validate_agent(tmp_path, run_dir=rdir)
    def test_creates_run_dir(self, tmp_path: Path):
        (tmp_path / "README.md").write_text("# P")
        rdir = tmp_path / "runs" / "new_session"

        def mock_asyncio_run(coro):
            coro.close()

        with patch("evolve.agent.asyncio.run", side_effect=mock_asyncio_run), \
             patch.dict("sys.modules", {"claude_agent_sdk": MagicMock()}):
            run_validate_agent(tmp_path, run_dir=rdir)
            assert rdir.is_dir()
class TestRunValidate:
    def test_creates_session_dir(self, tmp_path: Path):
        (tmp_path / "README.md").write_text("# P")
        (tmp_path / "runs").mkdir()

        with patch("evolve.orchestrator._auto_detect_check", return_value=None), \
             patch("evolve.agent.run_validate_agent"):
            from evolve.orchestrator import run_validate
            run_validate(tmp_path)

        sessions = [d for d in (tmp_path / "runs").iterdir() if d.is_dir()]
        assert len(sessions) >= 1
    def test_check_cmd_runs(self, tmp_path: Path):
        (tmp_path / "README.md").write_text("# P")
        (tmp_path / "runs").mkdir()

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "5 passed"
        mock_result.stderr = ""

        with patch("evolve.orchestrator.subprocess.run", return_value=mock_result) as mock_sub, \
             patch("evolve.agent.run_validate_agent") as mock_agent:
            from evolve.orchestrator import run_validate
            run_validate(tmp_path, check_cmd="pytest", timeout=60)

        calls = [c for c in mock_sub.call_args_list if "pytest" in str(c)]
        assert len(calls) >= 1
        mock_agent.assert_called_once()
        assert mock_agent.call_args.kwargs.get("check_cmd") == "pytest"
    def test_check_timeout(self, tmp_path: Path):
        import subprocess
        (tmp_path / "README.md").write_text("# P")
        (tmp_path / "runs").mkdir()

        with patch("evolve.orchestrator.subprocess.run", side_effect=subprocess.TimeoutExpired("pytest", 60)), \
             patch("evolve.agent.run_validate_agent"):
            from evolve.orchestrator import run_validate
            run_validate(tmp_path, check_cmd="pytest", timeout=60)
    def test_auto_detect_when_no_check(self, tmp_path: Path):
        (tmp_path / "README.md").write_text("# P")
        (tmp_path / "runs").mkdir()

        mock_result = MagicMock(returncode=0, stdout="ok", stderr="")

        with patch("evolve.orchestrator._auto_detect_check", return_value="pytest") as mock_detect, \
             patch("evolve.orchestrator.subprocess.run", return_value=mock_result), \
             patch("evolve.agent.run_validate_agent"):
            from evolve.orchestrator import run_validate
            run_validate(tmp_path)
            mock_detect.assert_called_once_with(tmp_path)
    def test_exit_0_all_pass(self, tmp_path: Path):
        (tmp_path / "README.md").write_text("# P")
        (tmp_path / "runs").mkdir()

        def create_report(**kwargs):
            rd = kwargs.get("run_dir")
            if rd:
                (rd / "validate_report.md").write_text(
                    "# Validation Report\n"
                    "- ✅ Feature A\n"
                    "- ✅ Feature B\n"
                    "## Summary\nCompliance: 100%\n"
                )

        with patch("evolve.orchestrator._auto_detect_check", return_value=None), \
             patch("evolve.agent.run_validate_agent", side_effect=create_report):
            from evolve.orchestrator import run_validate
            result = run_validate(tmp_path)
            assert result == 0
    def test_exit_1_some_fail(self, tmp_path: Path):
        (tmp_path / "README.md").write_text("# P")
        (tmp_path / "runs").mkdir()

        def create_report(**kwargs):
            rd = kwargs.get("run_dir")
            if rd:
                (rd / "validate_report.md").write_text(
                    "# Validation Report\n"
                    "- ✅ Feature A\n"
                    "- ❌ Feature B — not implemented\n"
                    "## Summary\nCompliance: 50%\n"
                )

        with patch("evolve.orchestrator._auto_detect_check", return_value=None), \
             patch("evolve.agent.run_validate_agent", side_effect=create_report):
            from evolve.orchestrator import run_validate
            result = run_validate(tmp_path)
            assert result == 1
    def test_exit_2_no_report(self, tmp_path: Path):
        (tmp_path / "README.md").write_text("# P")
        (tmp_path / "runs").mkdir()

        with patch("evolve.orchestrator._auto_detect_check", return_value=None), \
             patch("evolve.agent.run_validate_agent"):
            from evolve.orchestrator import run_validate
            result = run_validate(tmp_path)
            assert result == 2
    def test_exit_2_no_markers(self, tmp_path: Path):
        (tmp_path / "README.md").write_text("# P")
        (tmp_path / "runs").mkdir()

        def create_report(**kwargs):
            rd = kwargs.get("run_dir")
            if rd:
                (rd / "validate_report.md").write_text(
                    "# Validation Report\nEmpty report\n"
                )

        with patch("evolve.orchestrator._auto_detect_check", return_value=None), \
             patch("evolve.agent.run_validate_agent", side_effect=create_report):
            from evolve.orchestrator import run_validate
            result = run_validate(tmp_path)
            assert result == 2
    def test_model_passed_to_agent(self, tmp_path: Path):
        (tmp_path / "README.md").write_text("# P")
        (tmp_path / "runs").mkdir()

        import evolve.agent as _agent_mod

        with patch("evolve.orchestrator._auto_detect_check", return_value=None), \
             patch("evolve.agent.run_validate_agent"):
            from evolve.orchestrator import run_validate
            run_validate(tmp_path, model="claude-sonnet-4-20250514")
            assert __rt_mod.MODEL == "claude-sonnet-4-20250514"

        # Reset
        __rt_mod.MODEL = "claude-opus-4-6"
class TestRunValidateClaudeAgent:
    def test_logs_conversation(self, tmp_path: Path):
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

        messages = [AM([MockTextBlock("Validation complete")])]

        async def mock_query(prompt, options):
            for m in messages:
                yield m

        mock_sdk = MagicMock()
        mock_sdk.query = mock_query
        mock_sdk.ClaudeAgentOptions = lambda **kw: None
        mock_sdk.AssistantMessage = AM
        mock_sdk.ResultMessage = RM

        with patch.dict("sys.modules", {"claude_agent_sdk": mock_sdk}):
            from evolve.agent import _run_validate_claude_agent
            asyncio.run(_run_validate_claude_agent(
                "test prompt", tmp_path, run_dir
            ))

        log = run_dir / "validate_conversation.md"
        assert log.is_file()
        content = log.read_text()
        assert "Validation complete" in content
    def test_tool_use_blocks_logged(self, tmp_path: Path):
        run_dir = tmp_path / "runs" / "session"
        run_dir.mkdir(parents=True)

        class MockToolBlock:
            def __init__(self):
                self.name = "Read"
                self.id = "tool_456"
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
            from evolve.agent import _run_validate_claude_agent
            asyncio.run(_run_validate_claude_agent(
                "test prompt", tmp_path, run_dir
            ))

        content = (run_dir / "validate_conversation.md").read_text()
        assert "Read" in content
        assert "/tmp/foo.py" in content
    def test_sdk_error_logged(self, tmp_path: Path):
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
            from evolve.agent import _run_validate_claude_agent
            asyncio.run(_run_validate_claude_agent(
                "test prompt", tmp_path, run_dir
            ))

        content = (run_dir / "validate_conversation.md").read_text()
        assert "SDK error" in content
    def test_deduplicates_text_blocks(self, tmp_path: Path):
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
            from evolve.agent import _run_validate_claude_agent
            asyncio.run(_run_validate_claude_agent(
                "test prompt", tmp_path, run_dir
            ))

        content = (run_dir / "validate_conversation.md").read_text()
        assert content.count("Same text") == 1
    def test_deduplicates_tool_blocks(self, tmp_path: Path):
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
            from evolve.agent import _run_validate_claude_agent
            asyncio.run(_run_validate_claude_agent(
                "test prompt", tmp_path, run_dir
            ))

        content = (run_dir / "validate_conversation.md").read_text()
        assert content.count("**Glob**") == 1
    def test_none_messages_skipped(self, tmp_path: Path):
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
            from evolve.agent import _run_validate_claude_agent
            asyncio.run(_run_validate_claude_agent(
                "test prompt", tmp_path, run_dir
            ))

        assert (run_dir / "validate_conversation.md").is_file()
