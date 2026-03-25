"""Coverage tests for agent.py — run_claude_agent, analyze_and_fix retry paths."""

import asyncio
import textwrap
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from agent import (
    run_claude_agent,
    analyze_and_fix,
    _patch_sdk_parser,
    _is_benign_runtime_error,
    _should_retry_rate_limit,
    build_prompt,
)


def _run_async(coro):
    """Helper to run async functions in tests without pytest-asyncio."""
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# run_claude_agent — mocked SDK
# ---------------------------------------------------------------------------

class TestRunClaudeAgent:
    """Test run_claude_agent with mocked SDK to cover lines 195-312."""

    def _setup_mock_sdk(self, messages, AssistantMessage=None, ResultMessage=None):
        """Create mock SDK with given messages."""
        if AssistantMessage is None:
            AssistantMessage = type("AssistantMessage", (), {})
        if ResultMessage is None:
            ResultMessage = type("ResultMessage", (), {})

        class MockClaudeAgentOptions:
            def __init__(self, **kwargs):
                pass

        async def mock_query(prompt, options):
            for msg in messages:
                yield msg

        mock_sdk = MagicMock()
        mock_sdk.query = mock_query
        mock_sdk.ClaudeAgentOptions = MockClaudeAgentOptions
        mock_sdk.AssistantMessage = AssistantMessage
        mock_sdk.ResultMessage = ResultMessage
        return mock_sdk

    def test_run_with_text_messages(self, tmp_path: Path):
        """Agent receives text messages and logs them."""
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

        messages = [
            AM([MockTextBlock("Hello from agent")]),
            RM([MockTextBlock("Done working")]),
        ]

        mock_sdk = _run_async(asyncio.coroutine(lambda: None)()) if False else None
        mock_sdk = MagicMock()

        class MockOpts:
            def __init__(self, **kw):
                pass

        async def mock_query(prompt, options):
            for m in messages:
                yield m

        mock_sdk.query = mock_query
        mock_sdk.ClaudeAgentOptions = MockOpts
        mock_sdk.AssistantMessage = AM
        mock_sdk.ResultMessage = RM

        with patch.dict("sys.modules", {"claude_agent_sdk": mock_sdk}), \
             patch("agent._patch_sdk_parser"):
            _run_async(run_claude_agent("test prompt", tmp_path, round_num=1, run_dir=run_dir))

        log_path = run_dir / "conversation_loop_1.md"
        assert log_path.is_file()
        content = log_path.read_text()
        assert "Hello from agent" in content
        assert "Done working" in content

    def test_run_with_tool_use(self, tmp_path: Path):
        """Agent uses tools and they are logged."""
        run_dir = tmp_path / "runs" / "session"
        run_dir.mkdir(parents=True)

        class MockToolBlock:
            def __init__(self, name, input_data, block_id):
                self.name = name
                self.input = input_data
                self.id = block_id

        class AM:
            def __init__(self, content):
                self.content = content

        class RM:
            pass

        messages = [
            AM([MockToolBlock("Bash", {"command": "pytest tests/"}, "t1")]),
            AM([MockToolBlock("Read", {"file_path": "/tmp/foo.py"}, "t2")]),
            AM([MockToolBlock("Edit", {"old_string": "x", "file_path": "bar.py"}, "t3")]),
            AM([MockToolBlock("Grep", {"pattern": "def main"}, "t4")]),
            AM([MockToolBlock("Write", {"content": "hello " * 200}, "t5")]),
        ]

        mock_sdk = MagicMock()

        async def mock_query(prompt, options):
            for m in messages:
                yield m

        mock_sdk.query = mock_query
        mock_sdk.ClaudeAgentOptions = lambda **kw: None
        mock_sdk.AssistantMessage = AM
        mock_sdk.ResultMessage = RM

        with patch.dict("sys.modules", {"claude_agent_sdk": mock_sdk}), \
             patch("agent._patch_sdk_parser"):
            _run_async(run_claude_agent("test", tmp_path, round_num=1, run_dir=run_dir))

        content = (run_dir / "conversation_loop_1.md").read_text()
        assert "Bash" in content
        assert "pytest tests/" in content

    def test_run_with_thinking_block(self, tmp_path: Path):
        """Thinking blocks are logged."""
        run_dir = tmp_path / "runs" / "session"
        run_dir.mkdir(parents=True)

        class MockThinkingBlock:
            def __init__(self, thinking):
                self.thinking = thinking

        class AM:
            def __init__(self, content):
                self.content = content

        class RM:
            pass

        messages = [AM([MockThinkingBlock("Let me analyze this...")])]

        mock_sdk = MagicMock()

        async def mock_query(prompt, options):
            for m in messages:
                yield m

        mock_sdk.query = mock_query
        mock_sdk.ClaudeAgentOptions = lambda **kw: None
        mock_sdk.AssistantMessage = AM
        mock_sdk.ResultMessage = RM

        with patch.dict("sys.modules", {"claude_agent_sdk": mock_sdk}), \
             patch("agent._patch_sdk_parser"):
            _run_async(run_claude_agent("test", tmp_path, round_num=1, run_dir=run_dir))

        content = (run_dir / "conversation_loop_1.md").read_text()
        assert "Thinking" in content
        assert "analyze this" in content

    def test_run_with_none_messages(self, tmp_path: Path):
        """None messages in stream are skipped."""
        run_dir = tmp_path / "runs" / "session"
        run_dir.mkdir(parents=True)

        class AM:
            pass

        class RM:
            pass

        mock_sdk = MagicMock()

        async def mock_query(prompt, options):
            yield None
            yield None

        mock_sdk.query = mock_query
        mock_sdk.ClaudeAgentOptions = lambda **kw: None
        mock_sdk.AssistantMessage = AM
        mock_sdk.ResultMessage = RM

        with patch.dict("sys.modules", {"claude_agent_sdk": mock_sdk}), \
             patch("agent._patch_sdk_parser"):
            _run_async(run_claude_agent("test", tmp_path, round_num=1, run_dir=run_dir))

    def test_run_with_stream_event(self, tmp_path: Path):
        """StreamEvent messages are skipped."""
        run_dir = tmp_path / "runs" / "session"
        run_dir.mkdir(parents=True)

        class StreamEvent:
            pass

        class AM:
            pass

        class RM:
            pass

        mock_sdk = MagicMock()

        async def mock_query(prompt, options):
            yield StreamEvent()

        mock_sdk.query = mock_query
        mock_sdk.ClaudeAgentOptions = lambda **kw: None
        mock_sdk.AssistantMessage = AM
        mock_sdk.ResultMessage = RM

        with patch.dict("sys.modules", {"claude_agent_sdk": mock_sdk}), \
             patch("agent._patch_sdk_parser"):
            _run_async(run_claude_agent("test", tmp_path, round_num=1, run_dir=run_dir))

    def test_run_with_rate_limit_event(self, tmp_path: Path):
        """RateLimitEvent messages are logged."""
        run_dir = tmp_path / "runs" / "session"
        run_dir.mkdir(parents=True)

        class RateLimitEvent:
            pass

        class AM:
            pass

        class RM:
            pass

        mock_sdk = MagicMock()

        async def mock_query(prompt, options):
            yield RateLimitEvent()

        mock_sdk.query = mock_query
        mock_sdk.ClaudeAgentOptions = lambda **kw: None
        mock_sdk.AssistantMessage = AM
        mock_sdk.ResultMessage = RM

        with patch.dict("sys.modules", {"claude_agent_sdk": mock_sdk}), \
             patch("agent._patch_sdk_parser"):
            _run_async(run_claude_agent("test", tmp_path, round_num=1, run_dir=run_dir))

        content = (run_dir / "conversation_loop_1.md").read_text()
        assert "Rate limited" in content

    def test_run_with_system_message(self, tmp_path: Path):
        """SystemMessage messages are logged."""
        run_dir = tmp_path / "runs" / "session"
        run_dir.mkdir(parents=True)

        class SystemMessage:
            pass

        class AM:
            pass

        class RM:
            pass

        mock_sdk = MagicMock()

        async def mock_query(prompt, options):
            yield SystemMessage()

        mock_sdk.query = mock_query
        mock_sdk.ClaudeAgentOptions = lambda **kw: None
        mock_sdk.AssistantMessage = AM
        mock_sdk.ResultMessage = RM

        with patch.dict("sys.modules", {"claude_agent_sdk": mock_sdk}), \
             patch("agent._patch_sdk_parser"):
            _run_async(run_claude_agent("test", tmp_path, round_num=1, run_dir=run_dir))

        content = (run_dir / "conversation_loop_1.md").read_text()
        assert "Session initialized" in content

    def test_run_with_sdk_error(self, tmp_path: Path):
        """SDK errors during streaming are caught and logged."""
        run_dir = tmp_path / "runs" / "session"
        run_dir.mkdir(parents=True)

        class AM:
            pass

        class RM:
            pass

        mock_sdk = MagicMock()

        async def mock_query(prompt, options):
            raise RuntimeError("SDK connection lost")
            yield  # make it async generator

        mock_sdk.query = mock_query
        mock_sdk.ClaudeAgentOptions = lambda **kw: None
        mock_sdk.AssistantMessage = AM
        mock_sdk.ResultMessage = RM

        with patch.dict("sys.modules", {"claude_agent_sdk": mock_sdk}), \
             patch("agent._patch_sdk_parser"):
            _run_async(run_claude_agent("test", tmp_path, round_num=1, run_dir=run_dir))

        content = (run_dir / "conversation_loop_1.md").read_text()
        assert "SDK error" in content

    def test_run_with_tool_result_block(self, tmp_path: Path):
        """ToolResultBlock messages are logged."""
        run_dir = tmp_path / "runs" / "session"
        run_dir.mkdir(parents=True)

        class ToolResultBlock:
            def __init__(self, content, is_error=False):
                self.content = content
                self.is_error = is_error

        class AM:
            def __init__(self, content):
                self.content = content

        class RM:
            def __init__(self, content):
                self.content = content

        messages = [
            AM([ToolResultBlock("result output")]),
            AM([ToolResultBlock("error output", is_error=True)]),
        ]

        mock_sdk = MagicMock()

        async def mock_query(prompt, options):
            for m in messages:
                yield m

        mock_sdk.query = mock_query
        mock_sdk.ClaudeAgentOptions = lambda **kw: None
        mock_sdk.AssistantMessage = AM
        mock_sdk.ResultMessage = RM

        with patch.dict("sys.modules", {"claude_agent_sdk": mock_sdk}), \
             patch("agent._patch_sdk_parser"):
            _run_async(run_claude_agent("test", tmp_path, round_num=1, run_dir=run_dir))

        content = (run_dir / "conversation_loop_1.md").read_text()
        assert "result output" in content
        assert "Error:" in content

    def test_run_deduplicates_tool_ids(self, tmp_path: Path):
        """Duplicate tool block IDs are only logged once."""
        run_dir = tmp_path / "runs" / "session"
        run_dir.mkdir(parents=True)

        class MockToolBlock:
            def __init__(self, name, input_data, block_id):
                self.name = name
                self.input = input_data
                self.id = block_id

        class AM:
            def __init__(self, content):
                self.content = content

        class RM:
            pass

        messages = [
            AM([MockToolBlock("Bash", {"command": "ls"}, "tool_1")]),
            AM([MockToolBlock("Bash", {"command": "ls"}, "tool_1")]),
        ]

        mock_sdk = MagicMock()

        async def mock_query(prompt, options):
            for m in messages:
                yield m

        mock_sdk.query = mock_query
        mock_sdk.ClaudeAgentOptions = lambda **kw: None
        mock_sdk.AssistantMessage = AM
        mock_sdk.ResultMessage = RM

        with patch.dict("sys.modules", {"claude_agent_sdk": mock_sdk}), \
             patch("agent._patch_sdk_parser"):
            _run_async(run_claude_agent("test", tmp_path, round_num=1, run_dir=run_dir))

        content = (run_dir / "conversation_loop_1.md").read_text()
        assert content.count("**Bash**") == 1

    def test_run_deduplicates_text_hashes(self, tmp_path: Path):
        """Duplicate text blocks are only logged once."""
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

        messages = [
            AM([MockTextBlock("duplicate text")]),
            AM([MockTextBlock("duplicate text")]),
        ]

        mock_sdk = MagicMock()

        async def mock_query(prompt, options):
            for m in messages:
                yield m

        mock_sdk.query = mock_query
        mock_sdk.ClaudeAgentOptions = lambda **kw: None
        mock_sdk.AssistantMessage = AM
        mock_sdk.ResultMessage = RM

        with patch.dict("sys.modules", {"claude_agent_sdk": mock_sdk}), \
             patch("agent._patch_sdk_parser"):
            _run_async(run_claude_agent("test", tmp_path, round_num=1, run_dir=run_dir))

        content = (run_dir / "conversation_loop_1.md").read_text()
        assert content.count("duplicate text") == 1

    def test_run_custom_log_filename(self, tmp_path: Path):
        """Custom log filename is used when provided."""
        run_dir = tmp_path / "runs" / "session"
        run_dir.mkdir(parents=True)

        class AM:
            pass

        class RM:
            pass

        mock_sdk = MagicMock()

        async def mock_query(prompt, options):
            return
            yield

        mock_sdk.query = mock_query
        mock_sdk.ClaudeAgentOptions = lambda **kw: None
        mock_sdk.AssistantMessage = AM
        mock_sdk.ResultMessage = RM

        with patch.dict("sys.modules", {"claude_agent_sdk": mock_sdk}), \
             patch("agent._patch_sdk_parser"):
            _run_async(run_claude_agent("prompt", tmp_path, round_num=1, run_dir=run_dir,
                                         log_filename="custom_log.md"))

        assert (run_dir / "custom_log.md").is_file()

    def test_run_empty_content_message_skipped(self, tmp_path: Path):
        """Messages with empty/None content are skipped."""
        run_dir = tmp_path / "runs" / "session"
        run_dir.mkdir(parents=True)

        class AM:
            def __init__(self, content):
                self.content = content

        class RM:
            pass

        mock_sdk = MagicMock()

        async def mock_query(prompt, options):
            yield AM([])
            m = AM.__new__(AM)
            m.content = None
            yield m

        mock_sdk.query = mock_query
        mock_sdk.ClaudeAgentOptions = lambda **kw: None
        mock_sdk.AssistantMessage = AM
        mock_sdk.ResultMessage = RM

        with patch.dict("sys.modules", {"claude_agent_sdk": mock_sdk}), \
             patch("agent._patch_sdk_parser"):
            _run_async(run_claude_agent("prompt", tmp_path, round_num=1, run_dir=run_dir))

    def test_run_tool_with_string_input(self, tmp_path: Path):
        """Tool blocks with non-dict input are handled."""
        run_dir = tmp_path / "runs" / "session"
        run_dir.mkdir(parents=True)

        class MockToolBlock:
            def __init__(self):
                self.name = "CustomTool"
                self.input = "raw string input that is longer than 100 chars " * 3
                self.id = "tool_1"

        class AM:
            def __init__(self, content):
                self.content = content

        class RM:
            pass

        mock_sdk = MagicMock()

        async def mock_query(prompt, options):
            yield AM([MockToolBlock()])

        mock_sdk.query = mock_query
        mock_sdk.ClaudeAgentOptions = lambda **kw: None
        mock_sdk.AssistantMessage = AM
        mock_sdk.ResultMessage = RM

        with patch.dict("sys.modules", {"claude_agent_sdk": mock_sdk}), \
             patch("agent._patch_sdk_parser"):
            _run_async(run_claude_agent("prompt", tmp_path, round_num=1, run_dir=run_dir))

        content = (run_dir / "conversation_loop_1.md").read_text()
        assert "CustomTool" in content

    def test_run_no_run_dir_uses_project_runs(self, tmp_path: Path):
        """When run_dir is None, uses project/runs."""
        (tmp_path / "runs").mkdir()

        class AM:
            pass

        class RM:
            pass

        mock_sdk = MagicMock()

        async def mock_query(prompt, options):
            return
            yield

        mock_sdk.query = mock_query
        mock_sdk.ClaudeAgentOptions = lambda **kw: None
        mock_sdk.AssistantMessage = AM
        mock_sdk.ResultMessage = RM

        with patch.dict("sys.modules", {"claude_agent_sdk": mock_sdk}), \
             patch("agent._patch_sdk_parser"):
            _run_async(run_claude_agent("prompt", tmp_path, round_num=1, run_dir=None))

        assert (tmp_path / "runs" / "conversation_loop_1.md").is_file()


# ---------------------------------------------------------------------------
# analyze_and_fix — retry logic paths
# ---------------------------------------------------------------------------

class TestAnalyzeAndFixRetry:
    """Test analyze_and_fix retry paths (lines 371-396)."""

    def test_benign_runtime_error_returns(self, tmp_path: Path):
        """Benign RuntimeError (cancel scope) returns gracefully."""
        (tmp_path / "README.md").write_text("# P")
        (tmp_path / "runs").mkdir()

        mock_ui = MagicMock()
        mock_sdk = MagicMock()

        def mock_asyncio_run(coro):
            coro.close()  # prevent "coroutine was never awaited" warning
            raise RuntimeError("cancel scope blah")

        with patch("agent.get_tui", return_value=mock_ui), \
             patch.dict("sys.modules", {"claude_agent_sdk": mock_sdk}), \
             patch("agent.asyncio.run", side_effect=mock_asyncio_run):
            analyze_and_fix(tmp_path)

        mock_ui.warn.assert_not_called()

    def test_rate_limit_retries(self, tmp_path: Path):
        """Rate limit error triggers retry with backoff."""
        (tmp_path / "README.md").write_text("# P")
        (tmp_path / "runs").mkdir()

        mock_ui = MagicMock()
        mock_sdk = MagicMock()

        call_count = 0

        def mock_asyncio_run(coro):
            nonlocal call_count
            coro.close()  # prevent "coroutine was never awaited" warning
            call_count += 1
            if call_count < 3:
                raise Exception("rate_limit_exceeded")

        with patch("agent.get_tui", return_value=mock_ui), \
             patch.dict("sys.modules", {"claude_agent_sdk": mock_sdk}), \
             patch("agent.asyncio.run", side_effect=mock_asyncio_run), \
             patch("agent.time.sleep"):
            analyze_and_fix(tmp_path, max_retries=5)

        assert call_count == 3
        assert mock_ui.sdk_rate_limited.call_count == 2

    def test_non_retryable_error_gives_up(self, tmp_path: Path):
        """Non-retryable error calls warn and returns."""
        (tmp_path / "README.md").write_text("# P")
        (tmp_path / "runs").mkdir()

        mock_ui = MagicMock()
        mock_sdk = MagicMock()

        def mock_asyncio_run(coro):
            coro.close()  # prevent "coroutine was never awaited" warning
            raise Exception("some random error")

        with patch("agent.get_tui", return_value=mock_ui), \
             patch.dict("sys.modules", {"claude_agent_sdk": mock_sdk}), \
             patch("agent.asyncio.run", side_effect=mock_asyncio_run):
            analyze_and_fix(tmp_path, max_retries=3)

        mock_ui.warn.assert_called_once()
        assert "failed" in mock_ui.warn.call_args[0][0]


# ---------------------------------------------------------------------------
# _patch_sdk_parser — when SDK is available
# ---------------------------------------------------------------------------

class TestPatchSdkParserWithSDK:
    def test_patches_parse_message(self):
        """When SDK is available, parse_message gets patched."""
        mock_parser = MagicMock()
        mock_parser.parse_message._patched = False

        mock_internal = MagicMock()
        mock_internal.message_parser = mock_parser

        with patch.dict("sys.modules", {
            "claude_agent_sdk": MagicMock(),
            "claude_agent_sdk._internal": mock_internal,
            "claude_agent_sdk._internal.message_parser": mock_parser,
        }):
            _patch_sdk_parser()

        assert mock_parser.parse_message._patched is True

    def test_idempotent_patching(self):
        """Repeated calls to _patch_sdk_parser are safe."""
        mock_fn = MagicMock()
        mock_fn._patched = True

        mock_parser = MagicMock()
        mock_parser.parse_message = mock_fn

        mock_internal = MagicMock()
        mock_internal.message_parser = mock_parser

        with patch.dict("sys.modules", {
            "claude_agent_sdk": MagicMock(),
            "claude_agent_sdk._internal": mock_internal,
            "claude_agent_sdk._internal.message_parser": mock_parser,
        }):
            _patch_sdk_parser()

        assert mock_parser.parse_message is mock_fn


# ---------------------------------------------------------------------------
# build_prompt — crash log paths
# ---------------------------------------------------------------------------

class TestBuildPromptCrashLogs:
    def test_crash_log_included(self, tmp_path: Path):
        """Previous round crash log is included in prompt."""
        (tmp_path / "README.md").write_text("# P")
        (tmp_path / "runs").mkdir()
        run_dir = tmp_path / "runs" / "session"
        run_dir.mkdir()
        (run_dir / "subprocess_error_round_1.txt").write_text("Crashed: exit code 1\nTraceback...")
        prompt = build_prompt(tmp_path, run_dir=run_dir)
        assert "CRITICAL" in prompt
        assert "Crashed: exit code 1" in prompt

    def test_multiple_check_results_picks_latest(self, tmp_path: Path):
        """When multiple check results exist, picks the highest round number."""
        (tmp_path / "README.md").write_text("# P")
        (tmp_path / "runs").mkdir()
        run_dir = tmp_path / "runs" / "session"
        run_dir.mkdir()
        (run_dir / "check_round_1.txt").write_text("Round 1: OLD")
        (run_dir / "check_round_3.txt").write_text("Round 3: LATEST")
        (run_dir / "check_round_2.txt").write_text("Round 2: MID")
        prompt = build_prompt(tmp_path, run_dir=run_dir)
        assert "Round 3: LATEST" in prompt
