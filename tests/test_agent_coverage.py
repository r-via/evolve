"""Coverage tests for agent.py — run_claude_agent, analyze_and_fix retry paths."""

import asyncio
import textwrap
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from evolve.infrastructure.claude_sdk.runner import run_claude_agent
from evolve.infrastructure.claude_sdk.runtime import analyze_and_fix
from evolve.infrastructure.claude_sdk.runtime import _should_retry_rate_limit
from evolve.infrastructure.claude_sdk.runtime import (
    _patch_sdk_parser,
    _is_benign_runtime_error,
)
from evolve.infrastructure.claude_sdk.prompt_builder import build_prompt


def _run_async(coro):
    """Helper to run async functions in tests without pytest-asyncio."""
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# run_claude_agent — mocked SDK
# ---------------------------------------------------------------------------

class TestRunClaudeAgent:
    """Test run_claude_agent with mocked SDK to cover lines 195-312."""

    # Shared mock block types — allocated once per class, not per test
    class AM:
        """Shared AssistantMessage mock with optional content."""
        def __init__(self, content=None):
            self.content = content

    class RM:
        """Shared ResultMessage mock with optional content."""
        def __init__(self, content=None):
            self.content = content

    class MockTextBlock:
        def __init__(self, text):
            self.text = text

    class MockToolBlock:
        def __init__(self, name="Tool", input_data=None, block_id="t0"):
            self.name = name
            self.input = input_data
            self.id = block_id

    class MockThinkingBlock:
        def __init__(self, thinking):
            self.thinking = thinking

    class ToolResultBlock:
        def __init__(self, content, is_error=False):
            self.content = content
            self.is_error = is_error

    def _make_sdk(self, messages):
        """Create a mock SDK wired to yield the given messages."""
        mock_sdk = MagicMock()

        async def mock_query(prompt, options):
            for m in messages:
                yield m

        mock_sdk.query = mock_query
        mock_sdk.ClaudeAgentOptions = lambda **kw: None
        mock_sdk.AssistantMessage = self.AM
        mock_sdk.ResultMessage = self.RM
        return mock_sdk

    def _make_run_dir(self, tmp_path: Path) -> Path:
        """Create and return the standard run directory."""
        run_dir = tmp_path / "runs" / "session"
        run_dir.mkdir(parents=True)
        return run_dir

    def _run_agent(self, tmp_path, run_dir, mock_sdk, **kwargs):
        """Run the agent with the given SDK mock and standard patches."""
        with patch.dict("sys.modules", {"claude_agent_sdk": mock_sdk}), \
             patch("evolve.infrastructure.claude_sdk.runtime._patch_sdk_parser"):
            _run_async(run_claude_agent(
                kwargs.pop("prompt", "test"), tmp_path,
                round_num=kwargs.pop("round_num", 1),
                run_dir=run_dir, **kwargs,
            ))

    def test_run_with_text_messages(self, tmp_path: Path):
        """Agent receives text messages and logs them."""
        run_dir = self._make_run_dir(tmp_path)
        messages = [
            self.AM([self.MockTextBlock("Hello from agent")]),
            self.RM([self.MockTextBlock("Done working")]),
        ]
        self._run_agent(tmp_path, run_dir, self._make_sdk(messages))

        log_path = run_dir / "conversation_loop_1.md"
        assert log_path.is_file()
        content = log_path.read_text()
        assert "Hello from agent" in content
        assert "Done working" in content

    def test_run_with_tool_use(self, tmp_path: Path):
        """Agent uses tools and they are logged."""
        run_dir = self._make_run_dir(tmp_path)
        messages = [
            self.AM([self.MockToolBlock("Bash", {"command": "pytest tests/"}, "t1")]),
            self.AM([self.MockToolBlock("Read", {"file_path": "/tmp/foo.py"}, "t2")]),
            self.AM([self.MockToolBlock("Edit", {"old_string": "x", "file_path": "bar.py"}, "t3")]),
            self.AM([self.MockToolBlock("Grep", {"pattern": "def main"}, "t4")]),
            self.AM([self.MockToolBlock("Write", {"content": "hello " * 200}, "t5")]),
        ]
        self._run_agent(tmp_path, run_dir, self._make_sdk(messages))

        content = (run_dir / "conversation_loop_1.md").read_text()
        assert "Bash" in content
        assert "pytest tests/" in content

    def test_run_with_thinking_block(self, tmp_path: Path):
        """Thinking blocks are logged."""
        run_dir = self._make_run_dir(tmp_path)
        messages = [self.AM([self.MockThinkingBlock("Let me analyze this...")])]
        self._run_agent(tmp_path, run_dir, self._make_sdk(messages))

        content = (run_dir / "conversation_loop_1.md").read_text()
        assert "Thinking" in content
        assert "analyze this" in content

    def test_run_with_none_messages(self, tmp_path: Path):
        """None messages in stream are skipped."""
        run_dir = self._make_run_dir(tmp_path)
        mock_sdk = MagicMock()

        async def mock_query(prompt, options):
            yield None
            yield None

        mock_sdk.query = mock_query
        mock_sdk.ClaudeAgentOptions = lambda **kw: None
        mock_sdk.AssistantMessage = self.AM
        mock_sdk.ResultMessage = self.RM
        self._run_agent(tmp_path, run_dir, mock_sdk)

    def test_run_with_stream_event(self, tmp_path: Path):
        """StreamEvent messages are skipped."""
        run_dir = self._make_run_dir(tmp_path)

        class StreamEvent:
            pass

        mock_sdk = MagicMock()

        async def mock_query(prompt, options):
            yield StreamEvent()

        mock_sdk.query = mock_query
        mock_sdk.ClaudeAgentOptions = lambda **kw: None
        mock_sdk.AssistantMessage = self.AM
        mock_sdk.ResultMessage = self.RM
        self._run_agent(tmp_path, run_dir, mock_sdk)

    def test_run_with_rate_limit_event(self, tmp_path: Path):
        """RateLimitEvent messages are logged."""
        run_dir = self._make_run_dir(tmp_path)

        class RateLimitEvent:
            pass

        mock_sdk = MagicMock()

        async def mock_query(prompt, options):
            yield RateLimitEvent()

        mock_sdk.query = mock_query
        mock_sdk.ClaudeAgentOptions = lambda **kw: None
        mock_sdk.AssistantMessage = self.AM
        mock_sdk.ResultMessage = self.RM
        self._run_agent(tmp_path, run_dir, mock_sdk)

        content = (run_dir / "conversation_loop_1.md").read_text()
        assert "Rate limited" in content

    def test_run_with_system_message(self, tmp_path: Path):
        """SystemMessage messages are logged."""
        run_dir = self._make_run_dir(tmp_path)

        class SystemMessage:
            pass

        mock_sdk = MagicMock()

        async def mock_query(prompt, options):
            yield SystemMessage()

        mock_sdk.query = mock_query
        mock_sdk.ClaudeAgentOptions = lambda **kw: None
        mock_sdk.AssistantMessage = self.AM
        mock_sdk.ResultMessage = self.RM
        self._run_agent(tmp_path, run_dir, mock_sdk)

        content = (run_dir / "conversation_loop_1.md").read_text()
        assert "Session initialized" in content

    def test_run_with_sdk_error(self, tmp_path: Path):
        """SDK errors during streaming are caught and logged."""
        run_dir = self._make_run_dir(tmp_path)
        mock_sdk = MagicMock()

        async def mock_query(prompt, options):
            raise RuntimeError("SDK connection lost")
            yield  # make it async generator

        mock_sdk.query = mock_query
        mock_sdk.ClaudeAgentOptions = lambda **kw: None
        mock_sdk.AssistantMessage = self.AM
        mock_sdk.ResultMessage = self.RM
        self._run_agent(tmp_path, run_dir, mock_sdk)

        content = (run_dir / "conversation_loop_1.md").read_text()
        assert "SDK error" in content

    def test_run_with_tool_result_block(self, tmp_path: Path):
        """ToolResultBlock messages are logged."""
        run_dir = self._make_run_dir(tmp_path)
        messages = [
            self.AM([self.ToolResultBlock("result output")]),
            self.AM([self.ToolResultBlock("error output", is_error=True)]),
        ]
        self._run_agent(tmp_path, run_dir, self._make_sdk(messages))

        content = (run_dir / "conversation_loop_1.md").read_text()
        assert "result output" in content
        assert "Error:" in content

    def test_run_deduplicates_tool_ids(self, tmp_path: Path):
        """Duplicate tool block IDs are only logged once."""
        run_dir = self._make_run_dir(tmp_path)
        messages = [
            self.AM([self.MockToolBlock("Bash", {"command": "ls"}, "tool_1")]),
            self.AM([self.MockToolBlock("Bash", {"command": "ls"}, "tool_1")]),
        ]
        self._run_agent(tmp_path, run_dir, self._make_sdk(messages))

        content = (run_dir / "conversation_loop_1.md").read_text()
        assert content.count("**Bash**") == 1

    def test_run_deduplicates_text_hashes(self, tmp_path: Path):
        """Duplicate text blocks are only logged once."""
        run_dir = self._make_run_dir(tmp_path)
        messages = [
            self.AM([self.MockTextBlock("duplicate text")]),
            self.AM([self.MockTextBlock("duplicate text")]),
        ]
        self._run_agent(tmp_path, run_dir, self._make_sdk(messages))

        content = (run_dir / "conversation_loop_1.md").read_text()
        assert content.count("duplicate text") == 1

    def test_run_custom_log_filename(self, tmp_path: Path):
        """Custom log filename is used when provided."""
        run_dir = self._make_run_dir(tmp_path)
        mock_sdk = MagicMock()

        async def mock_query(prompt, options):
            return
            yield

        mock_sdk.query = mock_query
        mock_sdk.ClaudeAgentOptions = lambda **kw: None
        mock_sdk.AssistantMessage = self.AM
        mock_sdk.ResultMessage = self.RM
        self._run_agent(tmp_path, run_dir, mock_sdk,
                        prompt="prompt", log_filename="custom_log.md")

        assert (run_dir / "custom_log.md").is_file()

    def test_run_empty_content_message_skipped(self, tmp_path: Path):
        """Messages with empty/None content are skipped."""
        run_dir = self._make_run_dir(tmp_path)
        mock_sdk = MagicMock()

        AM = self.AM

        async def mock_query(prompt, options):
            yield AM([])
            m = AM.__new__(AM)
            m.content = None
            yield m

        mock_sdk.query = mock_query
        mock_sdk.ClaudeAgentOptions = lambda **kw: None
        mock_sdk.AssistantMessage = self.AM
        mock_sdk.ResultMessage = self.RM
        self._run_agent(tmp_path, run_dir, mock_sdk, prompt="prompt")

    def test_run_tool_with_string_input(self, tmp_path: Path):
        """Tool blocks with non-dict input are handled."""
        run_dir = self._make_run_dir(tmp_path)
        tool = self.MockToolBlock("CustomTool", "raw string input that is longer than 100 chars " * 3, "tool_1")
        messages = [self.AM([tool])]
        self._run_agent(tmp_path, run_dir, self._make_sdk(messages), prompt="prompt")

        content = (run_dir / "conversation_loop_1.md").read_text()
        assert "CustomTool" in content

    def test_run_no_run_dir_uses_project_runs(self, tmp_path: Path):
        """When run_dir is None, uses project/runs."""
        (tmp_path / "runs").mkdir()
        mock_sdk = MagicMock()

        async def mock_query(prompt, options):
            return
            yield

        mock_sdk.query = mock_query
        mock_sdk.ClaudeAgentOptions = lambda **kw: None
        mock_sdk.AssistantMessage = self.AM
        mock_sdk.ResultMessage = self.RM

        with patch.dict("sys.modules", {"claude_agent_sdk": mock_sdk}), \
             patch("evolve.infrastructure.claude_sdk.runtime._patch_sdk_parser"):
            _run_async(run_claude_agent("prompt", tmp_path, round_num=1, run_dir=None))

        assert (tmp_path / "runs" / "conversation_loop_1.md").is_file()


