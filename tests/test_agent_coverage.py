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
             patch("evolve.agent._patch_sdk_parser"):
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
             patch("evolve.agent._patch_sdk_parser"):
            _run_async(run_claude_agent("prompt", tmp_path, round_num=1, run_dir=None))

        assert (tmp_path / "runs" / "conversation_loop_1.md").is_file()


# ---------------------------------------------------------------------------
# analyze_and_fix — retry logic paths
# ---------------------------------------------------------------------------

class TestAnalyzeAndFixRetry:
    """Test analyze_and_fix retry paths (lines 371-396)."""

    # Shared mock SDK — allocated once per class
    _mock_sdk = MagicMock()

    def setup_method(self):
        """Fresh UI mock per test — avoids per-test MagicMock() boilerplate."""
        self.mock_ui = MagicMock()

    def _setup_project(self, tmp_path: Path):
        """Create minimal project structure for analyze_and_fix."""
        (tmp_path / "README.md").write_text("# P")
        (tmp_path / "runs").mkdir()

    def test_benign_runtime_error_returns(self, tmp_path: Path):
        """Benign RuntimeError (cancel scope) returns gracefully."""
        self._setup_project(tmp_path)

        def mock_asyncio_run(coro):
            coro.close()  # prevent "coroutine was never awaited" warning
            raise RuntimeError("cancel scope blah")

        with patch("evolve.agent.get_tui", return_value=self.mock_ui), \
             patch.dict("sys.modules", {"claude_agent_sdk": self._mock_sdk}), \
             patch("evolve.agent.asyncio.run", side_effect=mock_asyncio_run):
            analyze_and_fix(tmp_path)

        self.mock_ui.warn.assert_not_called()

    def test_rate_limit_retries(self, tmp_path: Path):
        """Rate limit error triggers retry with backoff."""
        self._setup_project(tmp_path)

        call_count = 0

        def mock_asyncio_run(coro):
            nonlocal call_count
            coro.close()  # prevent "coroutine was never awaited" warning
            call_count += 1
            if call_count < 3:
                raise Exception("rate_limit_exceeded")

        with patch("evolve.agent.get_tui", return_value=self.mock_ui), \
             patch.dict("sys.modules", {"claude_agent_sdk": self._mock_sdk}), \
             patch("evolve.agent.asyncio.run", side_effect=mock_asyncio_run), \
             patch("evolve.agent.time.sleep"):
            analyze_and_fix(tmp_path, max_retries=5)

        assert call_count == 3
        assert self.mock_ui.sdk_rate_limited.call_count == 2

    def test_non_retryable_error_gives_up(self, tmp_path: Path):
        """Non-retryable error calls warn and returns."""
        self._setup_project(tmp_path)

        def mock_asyncio_run(coro):
            coro.close()  # prevent "coroutine was never awaited" warning
            raise Exception("some random error")

        with patch("evolve.agent.get_tui", return_value=self.mock_ui), \
             patch.dict("sys.modules", {"claude_agent_sdk": self._mock_sdk}), \
             patch("evolve.agent.asyncio.run", side_effect=mock_asyncio_run):
            analyze_and_fix(tmp_path, max_retries=3)

        self.mock_ui.warn.assert_called_once()
        assert "failed" in self.mock_ui.warn.call_args[0][0]


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

    def test_no_progress_log_uses_no_progress_header(self, tmp_path: Path):
        """NO PROGRESS diagnostic uses 'Previous round made NO PROGRESS' header."""
        (tmp_path / "README.md").write_text("# P")
        (tmp_path / "runs").mkdir()
        run_dir = tmp_path / "runs" / "session"
        run_dir.mkdir()
        (run_dir / "subprocess_error_round_1.txt").write_text(
            "Round 1 — NO PROGRESS: improvements.md byte-identical (attempt 1)"
        )
        prompt = build_prompt(tmp_path, run_dir=run_dir)
        assert "CRITICAL" in prompt
        assert "Previous round made NO PROGRESS" in prompt
        assert "Start with Edit/Write immediately" in prompt
        # Should NOT contain CRASHED header
        assert "Previous round CRASHED" not in prompt

    def test_crash_log_uses_crashed_header(self, tmp_path: Path):
        """Regular crash diagnostic uses 'Previous round CRASHED' header."""
        (tmp_path / "README.md").write_text("# P")
        (tmp_path / "runs").mkdir()
        run_dir = tmp_path / "runs" / "session"
        run_dir.mkdir()
        (run_dir / "subprocess_error_round_1.txt").write_text(
            "Round 1 — crashed (exit code 1) (attempt 1)"
        )
        prompt = build_prompt(tmp_path, run_dir=run_dir)
        assert "CRITICAL" in prompt
        assert "Previous round CRASHED" in prompt
        assert "Previous round made NO PROGRESS" not in prompt

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


# ---------------------------------------------------------------------------
# _patch_sdk_parser — patched function invocation (lines 181-186)
# ---------------------------------------------------------------------------

class TestPatchSdkParserInvocation:
    """Test that the patched parse_message function works correctly."""

    def test_patched_fn_returns_none_on_rate_limit_event_error(self):
        """Patched parse_message returns None for rate_limit_event errors."""
        def original(data):
            raise ValueError("bad event data")

        original._patched = False

        mock_parser = MagicMock()
        mock_parser.parse_message = original

        with patch.dict("sys.modules", {
            "claude_agent_sdk": MagicMock(),
            "claude_agent_sdk._internal": MagicMock(message_parser=mock_parser),
            "claude_agent_sdk._internal.message_parser": mock_parser,
        }):
            _patch_sdk_parser()

        # The patched function should return None for rate_limit_event
        result = mock_parser.parse_message({"type": "rate_limit_event"})
        assert result is None

    def test_patched_fn_reraises_non_rate_limit_errors(self):
        """Patched parse_message re-raises errors for non-rate_limit_event data."""
        def original(data):
            raise ValueError("parse error")

        original._patched = False

        mock_parser = MagicMock()
        mock_parser.parse_message = original

        with patch.dict("sys.modules", {
            "claude_agent_sdk": MagicMock(),
            "claude_agent_sdk._internal": MagicMock(message_parser=mock_parser),
            "claude_agent_sdk._internal.message_parser": mock_parser,
        }):
            _patch_sdk_parser()

        with pytest.raises(ValueError, match="parse error"):
            mock_parser.parse_message({"type": "other_event"})

    def test_patched_fn_passes_through_on_success(self):
        """Patched parse_message returns original result when no exception."""
        sentinel = object()

        def original(data):
            return sentinel

        original._patched = False

        mock_parser = MagicMock()
        mock_parser.parse_message = original

        with patch.dict("sys.modules", {
            "claude_agent_sdk": MagicMock(),
            "claude_agent_sdk._internal": MagicMock(message_parser=mock_parser),
            "claude_agent_sdk._internal.message_parser": mock_parser,
        }):
            _patch_sdk_parser()

        result = mock_parser.parse_message({"type": "anything"})
        assert result is sentinel

    def test_patched_fn_reraises_for_non_dict_data(self):
        """Patched parse_message re-raises when data is not a dict."""
        def original(data):
            raise TypeError("not a dict")

        original._patched = False

        mock_parser = MagicMock()
        mock_parser.parse_message = original

        with patch.dict("sys.modules", {
            "claude_agent_sdk": MagicMock(),
            "claude_agent_sdk._internal": MagicMock(message_parser=mock_parser),
            "claude_agent_sdk._internal.message_parser": mock_parser,
        }):
            _patch_sdk_parser()

        with pytest.raises(TypeError, match="not a dict"):
            mock_parser.parse_message("string data")


# ---------------------------------------------------------------------------
# analyze_and_fix — _run() inner function (lines 390-393)
# ---------------------------------------------------------------------------

class TestAnalyzeAndFixRunInner:
    """Test the _run() closure inside analyze_and_fix covers attempt counting."""

    def _setup_project(self, tmp_path: Path):
        (tmp_path / "README.md").write_text("# P")
        (tmp_path / "runs").mkdir()

    def test_first_attempt_uses_attempt_1_log_filename(self, tmp_path: Path):
        """First subprocess attempt writes to conversation_loop_N_attempt_1.md.

        Per SPEC.md § "Retry continuity" rule (1), every attempt — including
        the first — gets its own per-attempt log file so debug retries can
        read the prior attempt's full transcript.
        """
        self._setup_project(tmp_path)
        run_dir = tmp_path / "runs" / "session"
        run_dir.mkdir()
        mock_ui = MagicMock()

        captured_calls = []

        async def mock_run_agent(prompt, project_dir, round_num=1, run_dir=None, log_filename=None):
            captured_calls.append({"round_num": round_num, "log_filename": log_filename})
            # Simulate the agent writing the per-attempt log so the post-run
            # copy step can find it.
            if run_dir is not None and log_filename is not None:
                (Path(run_dir) / log_filename).write_text("# Round\n")

        mock_sdk = MagicMock()

        with patch("evolve.agent.get_tui", return_value=mock_ui), \
             patch.dict("sys.modules", {"claude_agent_sdk": mock_sdk}), \
             patch("evolve.agent.run_claude_agent", side_effect=mock_run_agent):
            analyze_and_fix(tmp_path, round_num=3, run_dir=run_dir)

        assert len(captured_calls) == 1
        assert captured_calls[0]["log_filename"] == "conversation_loop_3_attempt_1.md"
        assert captured_calls[0]["round_num"] == 3
        # Per-attempt log is also copied to the canonical name.
        assert (run_dir / "conversation_loop_3.md").is_file()
        assert (run_dir / "conversation_loop_3_attempt_1.md").is_file()

    def test_sdk_rate_limit_retries_share_attempt_log(self, tmp_path: Path):
        """SDK rate-limit retries within a single subprocess attempt write
        to the SAME per-attempt log file.

        Per-attempt log naming is keyed off the orchestrator-level subprocess
        attempt (parsed from subprocess_error_round_N.txt), not the in-process
        SDK rate-limit retry counter.  Three SDK retries within attempt 1 all
        share ``conversation_loop_5_attempt_1.md``.
        """
        self._setup_project(tmp_path)
        run_dir = tmp_path / "runs" / "session"
        run_dir.mkdir()
        mock_ui = MagicMock()

        captured_calls = []
        call_count = 0

        async def mock_run_agent(prompt, project_dir, round_num=1, run_dir=None, log_filename=None):
            nonlocal call_count
            call_count += 1
            captured_calls.append({"round_num": round_num, "log_filename": log_filename, "attempt": call_count})
            if call_count < 3:
                raise Exception("rate_limit_exceeded")
            if run_dir is not None and log_filename is not None:
                (Path(run_dir) / log_filename).write_text("# Round\n")

        mock_sdk = MagicMock()

        with patch("evolve.agent.get_tui", return_value=mock_ui), \
             patch.dict("sys.modules", {"claude_agent_sdk": mock_sdk}), \
             patch("evolve.agent.run_claude_agent", side_effect=mock_run_agent), \
             patch("evolve.agent.time.sleep"):
            analyze_and_fix(tmp_path, round_num=5, run_dir=run_dir, max_retries=5)

        assert len(captured_calls) == 3
        # All three SDK retries share the same per-attempt log file
        # because the orchestrator-level attempt is still 1 (no
        # subprocess_error_round_5.txt exists).
        for call in captured_calls:
            assert call["log_filename"] == "conversation_loop_5_attempt_1.md"

    def test_subsequent_orchestrator_attempt_uses_attempt_2_log(self, tmp_path: Path):
        """When subprocess_error_round_N.txt records attempt 1 failed, the
        next subprocess attempt writes to ``..._attempt_2.md``."""
        self._setup_project(tmp_path)
        run_dir = tmp_path / "runs" / "session"
        run_dir.mkdir()
        # Simulate orchestrator-written diagnostic from attempt 1's failure.
        (run_dir / "subprocess_error_round_7.txt").write_text(
            "Round 7 — crashed (attempt 1)\nCommand: foo\n\nOutput:\n...\n"
        )
        mock_ui = MagicMock()

        captured_calls = []

        async def mock_run_agent(prompt, project_dir, round_num=1, run_dir=None, log_filename=None):
            captured_calls.append({"log_filename": log_filename})
            if run_dir is not None and log_filename is not None:
                (Path(run_dir) / log_filename).write_text("# Round\n")

        mock_sdk = MagicMock()

        with patch("evolve.agent.get_tui", return_value=mock_ui), \
             patch.dict("sys.modules", {"claude_agent_sdk": mock_sdk}), \
             patch("evolve.agent.run_claude_agent", side_effect=mock_run_agent):
            analyze_and_fix(tmp_path, round_num=7, run_dir=run_dir)

        assert captured_calls[0]["log_filename"] == "conversation_loop_7_attempt_2.md"
