"""Tests for agent.py SDK interaction paths — coverage for lines 696, 719,
767-770, 792-793, 1109, 1132, 1536-1596, 1620-1624.

All tests mock the Claude SDK at the `claude_agent_sdk.query` level.
No live SDK calls are made (per SPEC § "Hard rule: tests MUST NOT call
the real Claude SDK").
"""

import asyncio
import json
import sys
import types
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

def _ns(**kw):
    """Create a SimpleNamespace (fake SDK object) with the given attrs."""
    return types.SimpleNamespace(**kw)
def _assistant_msg(*blocks, usage=None):
    """Build a fake AssistantMessage with content blocks and optional usage."""
    msg = _ns(content=list(blocks))
    msg.__class__ = type("AssistantMessage", (), {})
    if usage is not None:
        msg.usage = usage
    return msg
def _result_msg(*blocks, usage=None):
    """Build a fake ResultMessage."""
    msg = _ns(content=list(blocks))
    msg.__class__ = type("ResultMessage", (), {})
    if usage is not None:
        msg.usage = usage
    return msg
def _text_block(text):
    return _ns(text=text)
def _thinking_block(thinking):
    return _ns(thinking=thinking)
def _tool_block(name, inp=None, block_id=None):
    b = _ns(name=name)
    if inp is not None:
        b.input = inp
    if block_id is not None:
        b.id = block_id
    return b
def _usage(inp=100, out=50, cache_create=10, cache_read=20):
    return _ns(
        input_tokens=inp,
        output_tokens=out,
        cache_creation_input_tokens=cache_create,
        cache_read_input_tokens=cache_read,
    )
def _install_fake_sdk():
    """Install a fake claude_agent_sdk module that exposes the needed classes."""
    fake_sdk = types.ModuleType("claude_agent_sdk")
    fake_sdk.ClaudeAgentOptions = lambda **kw: _ns(**kw)

    # AssistantMessage and ResultMessage need to be actual classes so
    # isinstance() checks work in the agent code.
    class AssistantMessage:
        pass

    class ResultMessage:
        pass

    fake_sdk.AssistantMessage = AssistantMessage
    fake_sdk.ResultMessage = ResultMessage

    # query will be patched per-test
    fake_sdk.query = None

    fake_sdk._internal = types.ModuleType("claude_agent_sdk._internal")
    return fake_sdk, AssistantMessage, ResultMessage
_FAKE_SDK, _AM, _RM = _install_fake_sdk()
def _make_msg(cls, blocks, usage=None):
    """Create an instance of the fake AssistantMessage/ResultMessage class."""
    msg = cls.__new__(cls)
    msg.content = list(blocks)
    if usage is not None:
        msg.usage = usage
    return msg
def _async_query_from_messages(messages):
    """Return an async generator function that yields the given messages."""
    async def _query(**kwargs):
        for m in messages:
            yield m
    # If called as query(prompt=..., options=...), need to handle both
    # positional and keyword styles.  The agent code does:
    #   async for message in query(prompt=..., options=...):
    return _query
@pytest.fixture
def fake_sdk():
    """Patch sys.modules with a fake claude_agent_sdk for the test."""
    sdk, AM, RM = _install_fake_sdk()
    with patch.dict(sys.modules, {
        "claude_agent_sdk": sdk,
        "claude_agent_sdk._internal": sdk._internal,
    }):
        yield sdk, AM, RM
@pytest.fixture
def project_dir(tmp_path):
    """Create a minimal project directory."""
    (tmp_path / "README.md").write_text("# Test Spec")
    (tmp_path / "SPEC.md").write_text("# Test Spec")
    run_dir = tmp_path / ".evolve" / "runs" / "session"
    run_dir.mkdir(parents=True)
    return tmp_path, run_dir

class TestRunClaudeAgentImages:
    """Cover the images/multimodal prompt path in run_claude_agent."""

    def test_images_triggers_multimodal_prompt(self, fake_sdk, project_dir):
        sdk, AM, RM = fake_sdk
        proj, run_dir = project_dir

        # Create a fake image file
        img = proj / "frame.png"
        img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 50)

        msg = AM.__new__(AM)
        msg.content = [_text_block("done")]

        async def _query(prompt, options):
            yield msg

        sdk.query = _query

        with patch("evolve.infrastructure.claude_sdk.runner.get_tui", return_value=MagicMock()):
            from evolve.agent import run_claude_agent
            asyncio.run(run_claude_agent(
                "test prompt", proj, round_num=1,
                run_dir=run_dir, images=[img],
            ))

        log = (run_dir / "conversation_loop_1.md").read_text()
        assert "done" in log

class TestRunClaudeAgentThinking:
    """Cover thinking-block dedup in run_claude_agent (L714-721)."""

    def test_thinking_blocks_logged_and_deduped(self, fake_sdk, project_dir):
        sdk, AM, RM = fake_sdk
        proj, run_dir = project_dir

        msg1 = AM.__new__(AM)
        msg1.content = [_thinking_block("deep thought")]
        msg2 = AM.__new__(AM)
        msg2.content = [_thinking_block("deep thought")]  # duplicate
        msg3 = AM.__new__(AM)
        msg3.content = [_thinking_block("new thought")]

        async def _query(prompt, options):
            yield msg1
            yield msg2
            yield msg3

        sdk.query = _query

        with patch("evolve.infrastructure.claude_sdk.runner.get_tui", return_value=MagicMock()):
            from evolve.agent import run_claude_agent
            asyncio.run(run_claude_agent(
                "test", proj, round_num=1, run_dir=run_dir,
            ))

        log = (run_dir / "conversation_loop_1.md").read_text()
        # "deep thought" should appear once (deduped), "new thought" once
        assert log.count("deep thought") == 1
        assert log.count("new thought") == 1

class TestRunClaudeAgentUsage:
    """Cover usage token extraction from SDK messages (L765-770)."""

    def test_usage_tokens_written_to_json(self, fake_sdk, project_dir):
        sdk, AM, RM = fake_sdk
        proj, run_dir = project_dir

        usage_data = _usage(inp=1000, out=500, cache_create=200, cache_read=800)
        msg = RM.__new__(RM)
        msg.content = [_text_block("final")]
        msg.usage = usage_data

        async def _query(prompt, options):
            yield msg

        sdk.query = _query

        with patch("evolve.infrastructure.claude_sdk.runner.get_tui", return_value=MagicMock()):
            from evolve.agent import run_claude_agent
            asyncio.run(run_claude_agent(
                "test", proj, round_num=1, run_dir=run_dir,
            ))

        usage_file = run_dir / "usage_round_1.json"
        assert usage_file.exists()
        data = json.loads(usage_file.read_text())
        assert data["input_tokens"] == 1000
        assert data["output_tokens"] == 500
        assert data["cache_creation_tokens"] == 200
        assert data["cache_read_tokens"] == 800

class TestRunClaudeAgentUsageSaveError:
    """Cover the usage save exception path (L792-793)."""

    def test_usage_save_oserror_non_fatal(self, fake_sdk, project_dir):
        sdk, AM, RM = fake_sdk
        proj, run_dir = project_dir

        msg = RM.__new__(RM)
        msg.content = [_text_block("ok")]
        msg.usage = _usage()

        async def _query(prompt, options):
            yield msg

        sdk.query = _query

        mock_tui = MagicMock()
        with patch("evolve.infrastructure.claude_sdk.runner.get_tui", return_value=mock_tui):
            with patch("evolve.costs.TokenUsage.save", side_effect=OSError("disk full")):
                from evolve.agent import run_claude_agent
                # Should not raise
                asyncio.run(run_claude_agent(
                    "test", proj, round_num=1, run_dir=run_dir,
                ))

        # agent_done should still be called
        mock_tui.agent_done.assert_called_once()

class TestReadonlyAgentEdgeCases:
    """Cover edge cases in _run_readonly_claude_agent."""

    def test_empty_content_skipped(self, fake_sdk, project_dir):
        sdk, AM, RM = fake_sdk
        proj, run_dir = project_dir

        msg_empty = AM.__new__(AM)
        msg_empty.content = []

        msg_none_content = AM.__new__(AM)
        msg_none_content.content = None

        msg_text = AM.__new__(AM)
        msg_text.content = [_text_block("hello")]

        async def _query(prompt, options):
            yield msg_empty
            yield msg_none_content
            yield msg_text

        sdk.query = _query

        with patch("evolve.infrastructure.claude_sdk.oneshot_agents.get_tui", return_value=MagicMock()):
            from evolve.agent import _run_readonly_claude_agent
            asyncio.run(_run_readonly_claude_agent(
                "test", proj, run_dir,
                log_filename="dry_run_conversation.md",
                log_header="Dry Run",
            ))

        log = (run_dir / "dry_run_conversation.md").read_text()
        assert "hello" in log

    def test_non_dict_tool_input(self, fake_sdk, project_dir):
        sdk, AM, RM = fake_sdk
        proj, run_dir = project_dir

        # Tool with a non-dict input (e.g. a string)
        tool = _tool_block("Read", inp="some/file/path.py", block_id="t1")

        msg = AM.__new__(AM)
        msg.content = [tool]

        async def _query(prompt, options):
            yield msg

        sdk.query = _query

        mock_tui = MagicMock()
        with patch("evolve.infrastructure.claude_sdk.oneshot_agents.get_tui", return_value=mock_tui):
            from evolve.agent import _run_readonly_claude_agent
            asyncio.run(_run_readonly_claude_agent(
                "test", proj, run_dir,
                log_filename="validate_conversation.md",
                log_header="Validate",
            ))

        log = (run_dir / "validate_conversation.md").read_text()
        assert "some/file/path.py" in log
        mock_tui.agent_tool.assert_called_once_with("Read", "some/file/path.py")

class TestSyncReadmeAgent:
    """Cover _run_sync_readme_claude_agent and run_sync_readme_agent."""

    def test_sync_readme_agent_streams_and_logs(self, fake_sdk, project_dir):
        sdk, AM, RM = fake_sdk
        proj, run_dir = project_dir

        msg1 = AM.__new__(AM)
        msg1.content = [_text_block("updating readme")]
        msg2 = AM.__new__(AM)
        msg2.content = [_tool_block("Write", inp={"file_path": "README.md"}, block_id="w1")]

        async def _query(prompt, options):
            yield msg1
            yield msg2

        sdk.query = _query

        mock_tui = MagicMock()
        with patch("evolve.tui.get_tui", return_value=mock_tui):
            from evolve.agent import _run_sync_readme_claude_agent
            asyncio.run(_run_sync_readme_claude_agent("test prompt", proj, run_dir))

        log = (run_dir / "sync_readme_conversation.md").read_text()
        assert "# Sync README" in log
        assert "updating readme" in log
        assert "**Write**" in log
        assert "1 tool calls" in log
        mock_tui.agent_done.assert_called_once()

    def test_sync_readme_agent_deduplicates_text(self, fake_sdk, project_dir):
        sdk, AM, RM = fake_sdk
        proj, run_dir = project_dir

        msg1 = AM.__new__(AM)
        msg1.content = [_text_block("same text")]
        msg2 = AM.__new__(AM)
        msg2.content = [_text_block("same text")]  # dup
        msg3 = AM.__new__(AM)
        msg3.content = [_text_block("different text")]

        async def _query(prompt, options):
            yield msg1
            yield msg2
            yield msg3

        sdk.query = _query

        with patch("evolve.tui.get_tui", return_value=MagicMock()):
            from evolve.agent import _run_sync_readme_claude_agent
            asyncio.run(_run_sync_readme_claude_agent("test", proj, run_dir))

        log = (run_dir / "sync_readme_conversation.md").read_text()
        assert log.count("same text") == 1
        assert log.count("different text") == 1

    def test_sync_readme_agent_deduplicates_tools(self, fake_sdk, project_dir):
        sdk, AM, RM = fake_sdk
        proj, run_dir = project_dir

        msg1 = AM.__new__(AM)
        msg1.content = [_tool_block("Read", inp={"file_path": "a.py"}, block_id="r1")]
        msg2 = AM.__new__(AM)
        msg2.content = [_tool_block("Read", inp={"file_path": "a.py"}, block_id="r1")]  # dup

        async def _query(prompt, options):
            yield msg1
            yield msg2

        sdk.query = _query

        mock_tui = MagicMock()
        with patch("evolve.tui.get_tui", return_value=mock_tui):
            from evolve.agent import _run_sync_readme_claude_agent
            asyncio.run(_run_sync_readme_claude_agent("test", proj, run_dir))

        log = (run_dir / "sync_readme_conversation.md").read_text()
        assert log.count("**Read**") == 1

    def test_sync_readme_agent_non_dict_input(self, fake_sdk, project_dir):
        sdk, AM, RM = fake_sdk
        proj, run_dir = project_dir

        msg = AM.__new__(AM)
        msg.content = [_tool_block("Glob", inp="**/*.md", block_id="g1")]

        async def _query(prompt, options):
            yield msg

        sdk.query = _query

        mock_tui = MagicMock()
        with patch("evolve.tui.get_tui", return_value=mock_tui):
            from evolve.agent import _run_sync_readme_claude_agent
            asyncio.run(_run_sync_readme_claude_agent("test", proj, run_dir))

        log = (run_dir / "sync_readme_conversation.md").read_text()
        assert "**/*.md" in log

    def test_sync_readme_agent_sdk_error_logged(self, fake_sdk, project_dir):
        sdk, AM, RM = fake_sdk
        proj, run_dir = project_dir

        async def _query(prompt, options):
            yield AM.__new__(AM)  # msg with no content attr set
            raise RuntimeError("SDK exploded")

        # Ensure the message has no content attr to test the no-content path too
        sdk.query = _query

        mock_tui = MagicMock()
        with patch("evolve.tui.get_tui", return_value=mock_tui):
            from evolve.agent import _run_sync_readme_claude_agent
            asyncio.run(_run_sync_readme_claude_agent("test", proj, run_dir))

        log = (run_dir / "sync_readme_conversation.md").read_text()
        assert "SDK error: SDK exploded" in log

    def test_run_sync_readme_agent_builds_prompt_and_retries(self, fake_sdk, project_dir):
        sdk, AM, RM = fake_sdk
        proj, _ = project_dir

        # Use a fresh run_dir that doesn't exist yet
        new_run_dir = proj / ".evolve" / "runs" / "new_session"

        with patch("evolve.agent.build_sync_readme_prompt", return_value="sync prompt") as mock_bsp:
            with patch("evolve.agent._run_agent_with_retries") as mock_retries:
                from evolve.agent import run_sync_readme_agent
                run_sync_readme_agent(proj, new_run_dir, spec="SPEC.md", apply=True)

                assert new_run_dir.exists()
                mock_bsp.assert_called_once_with(proj, new_run_dir, spec="SPEC.md", apply=True)
                mock_retries.assert_called_once()
                call_kwargs = mock_retries.call_args
                assert call_kwargs[1]["fail_label"] == "Sync-readme agent"

class TestRunClaudeAgentFullStream:
    """Integration-style test exercising the full streaming loop."""

    def test_mixed_messages_streamed(self, fake_sdk, project_dir):
        sdk, AM, RM = fake_sdk
        proj, run_dir = project_dir

        # StreamEvent-like message (skipped)
        StreamEvent = type("StreamEvent", (), {})
        stream_evt = StreamEvent()

        # Rate limit event
        RateLimitEvent = type("RateLimitEvent", (), {})
        rate_evt = RateLimitEvent()

        # System message
        SystemMessage = type("SystemMessage", (), {})
        sys_msg = SystemMessage()

        # AssistantMessage with text
        msg_text = AM.__new__(AM)
        msg_text.content = [_text_block("hello world")]

        # AssistantMessage with tool
        msg_tool = AM.__new__(AM)
        msg_tool.content = [_tool_block("Edit", inp={"file_path": "src/main.py"}, block_id="e1")]

        # ResultMessage with usage
        msg_result = RM.__new__(RM)
        msg_result.content = [_text_block("done")]
        msg_result.usage = _usage(inp=500, out=200, cache_create=50, cache_read=100)

        # None message
        async def _query(prompt, options):
            yield None
            yield stream_evt
            yield rate_evt
            yield sys_msg
            yield msg_text
            yield msg_tool
            yield msg_result

        sdk.query = _query

        mock_tui = MagicMock()
        with patch("evolve.infrastructure.claude_sdk.runner.get_tui", return_value=mock_tui):
            from evolve.agent import run_claude_agent
            asyncio.run(run_claude_agent(
                "test", proj, round_num=1, run_dir=run_dir,
            ))

        log = (run_dir / "conversation_loop_1.md").read_text()
        assert "hello world" in log
        assert "**Edit**" in log
        assert "done" in log
        assert "Rate limited" in log
        assert "Session initialized" in log

        # Usage file written with the last message's values
        usage_file = run_dir / "usage_round_1.json"
        assert usage_file.exists()
        data = json.loads(usage_file.read_text())
        assert data["input_tokens"] == 500
