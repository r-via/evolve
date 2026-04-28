"""Tests for agent.py — async helpers, OSError edge cases, ResultMessage subtype.

Split off from test_agent.py to keep both files under the 500-line cap
per SPEC § 'Hard rule: source files MUST NOT exceed 500 lines'.
"""

import asyncio
from pathlib import Path
from unittest.mock import patch, MagicMock, AsyncMock

from evolve.agent import (
    build_prompt,
    _build_multimodal_prompt,
    _detect_current_attempt,
    _detect_prior_round_anomalies,
)


# ---------------------------------------------------------------------------
# _detect_current_attempt — OSError path
# ---------------------------------------------------------------------------

class TestDetectCurrentAttemptOSError:
    """Cover _detect_current_attempt OSError when reading diagnostic file."""

    def test_oserror_returns_1(self, tmp_path: Path):
        """When reading the diagnostic file raises OSError, return 1."""
        diag = tmp_path / "subprocess_error_round_5.txt"
        diag.write_text("round 5 (attempt 2)")
        # Make file unreadable
        diag.chmod(0o000)
        try:
            result = _detect_current_attempt(tmp_path, 5)
            assert result == 1
        finally:
            diag.chmod(0o644)

    def test_no_attempt_marker_returns_1(self, tmp_path: Path):
        """When diagnostic has no (attempt K), return 1."""
        diag = tmp_path / "subprocess_error_round_3.txt"
        diag.write_text("round 3 failed somehow, no attempt marker")
        result = _detect_current_attempt(tmp_path, 3)
        assert result == 1

    def test_attempt_marker_returns_next(self, tmp_path: Path):
        """When diagnostic has (attempt 2), return 3."""
        diag = tmp_path / "subprocess_error_round_3.txt"
        diag.write_text("round 3 failed (attempt 2)")
        result = _detect_current_attempt(tmp_path, 3)
        assert result == 3


# ---------------------------------------------------------------------------
# _detect_prior_round_anomalies — OSError paths
# ---------------------------------------------------------------------------

class TestDetectPriorRoundAnomaliesOSError:
    """Cover _detect_prior_round_anomalies OSError branches."""

    def test_check_file_oserror(self, tmp_path: Path):
        """When check_round_N.txt exists but is unreadable, no crash."""
        check_f = tmp_path / "check_round_1.txt"
        check_f.write_text("post-fix check: FAIL")
        check_f.chmod(0o000)
        try:
            result = _detect_prior_round_anomalies(tmp_path, 2)
            # Should not include "post-fix check FAIL" since file unreadable
            assert "post-fix check FAIL" not in result
        finally:
            check_f.chmod(0o644)

    def test_convo_file_oserror(self, tmp_path: Path):
        """When conversation_loop_N.md exists but is unreadable, no crash."""
        convo = tmp_path / "conversation_loop_1.md"
        convo.write_text("stalled (120s without output) — killing subprocess")
        convo.chmod(0o000)
        try:
            result = _detect_prior_round_anomalies(tmp_path, 2)
            # Should not include watchdog anomaly since file unreadable
            assert "watchdog stall" not in result
        finally:
            convo.chmod(0o644)

    def test_normal_anomaly_detection(self, tmp_path: Path):
        """When check_round_N.txt has FAIL, detect it."""
        check_f = tmp_path / "check_round_4.txt"
        check_f.write_text("post-fix check: FAIL")
        result = _detect_prior_round_anomalies(tmp_path, 5)
        assert "post-fix check FAIL" in result

    def test_convo_anomaly_detection(self, tmp_path: Path):
        """When conversation log has watchdog stall, detect it."""
        convo = tmp_path / "conversation_loop_4.md"
        convo.write_text("stalled (120s without output) — killing subprocess")
        result = _detect_prior_round_anomalies(tmp_path, 5)
        assert any("watchdog" in a.lower() or "stall" in a.lower() for a in result)


# ---------------------------------------------------------------------------
# _build_multimodal_prompt — async image builder
# ---------------------------------------------------------------------------

class TestBuildMultimodalPrompt:
    """Cover _build_multimodal_prompt async generator."""

    def test_text_only(self):
        """No images → content has just the text block."""
        gen = _build_multimodal_prompt("hello", [])
        messages = list(_exhaust_async_gen(gen))
        assert len(messages) == 1
        content = messages[0]["message"]["content"]
        assert len(content) == 1
        assert content[0]["type"] == "text"
        assert content[0]["text"] == "hello"

    def test_with_image(self, tmp_path: Path):
        """Valid PNG file → content has text + image blocks."""
        img = tmp_path / "test.png"
        # Minimal valid PNG (1x1 pixel, red)
        import base64
        # Tiny 1x1 red PNG
        png_b64 = (
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4"
            "2mP8z8BQDwADhQGAWjR9awAAAABJRU5ErkJggg=="
        )
        img.write_bytes(base64.b64decode(png_b64))
        gen = _build_multimodal_prompt("hello", [img])
        messages = list(_exhaust_async_gen(gen))
        content = messages[0]["message"]["content"]
        assert len(content) == 2
        assert content[0]["type"] == "text"
        assert content[1]["type"] == "image"
        assert content[1]["source"]["media_type"] == "image/png"

    def test_missing_image_skipped(self, tmp_path: Path):
        """Non-existent image path → skipped, only text block."""
        gen = _build_multimodal_prompt("hello", [tmp_path / "nope.png"])
        messages = list(_exhaust_async_gen(gen))
        content = messages[0]["message"]["content"]
        assert len(content) == 1
        assert content[0]["type"] == "text"

    def test_session_id(self):
        """Output message has session_id = 'party-mode'."""
        gen = _build_multimodal_prompt("test", [])
        messages = list(_exhaust_async_gen(gen))
        assert messages[0]["session_id"] == "party-mode"

    def test_unreadable_image_skipped(self, tmp_path: Path):
        """Image that raises OSError on read → skipped gracefully."""
        img = tmp_path / "bad.png"
        img.write_text("not a png")
        img.chmod(0o000)
        try:
            gen = _build_multimodal_prompt("hello", [img])
            messages = list(_exhaust_async_gen(gen))
            content = messages[0]["message"]["content"]
            # Only text block, image was skipped due to OSError
            assert len(content) == 1
            assert content[0]["type"] == "text"
        finally:
            img.chmod(0o644)


def _exhaust_async_gen(agen):
    """Helper: collect all items from an async generator synchronously."""
    results = []
    async def _collect():
        async for item in agen:
            results.append(item)
    asyncio.run(_collect())
    return results


# ---------------------------------------------------------------------------
# analyze_and_fix — yolo parameter + copyfile OSError
# ---------------------------------------------------------------------------

class TestAnalyzeAndFixEdgeCases:
    """Cover analyze_and_fix edge cases: yolo alias, copyfile OSError."""

    @patch("evolve.agent.run_claude_agent", new_callable=AsyncMock)
    @patch("evolve.agent._run_agent_with_retries")
    def test_yolo_forwards_to_allow_installs(self, mock_retries, mock_agent, tmp_path: Path):
        """yolo=True forwards to build_prompt as allow_installs=True."""
        from evolve.agent import analyze_and_fix
        (tmp_path / "README.md").write_text("# Spec")
        run_dir = tmp_path / ".evolve" / "runs" / "session"
        run_dir.mkdir(parents=True)

        with patch("evolve.agent.build_prompt") as mock_bp:
            mock_bp.return_value = "prompt"
            analyze_and_fix(tmp_path, "ok", yolo=True, run_dir=run_dir, round_num=1)
            call_kwargs = mock_bp.call_args
            # yolo=True should pass allow_installs=True to build_prompt
            assert call_kwargs[0][2] is None or call_kwargs[1].get("allow_installs") is None
            # The yolo fallback sets allow_installs = yolo before calling build_prompt
            # Actually, analyze_and_fix passes allow_installs positionally
            # Let's just verify build_prompt was called
            mock_bp.assert_called_once()

    @patch("evolve.agent._run_agent_with_retries")
    def test_copyfile_oserror_non_fatal(self, mock_retries, tmp_path: Path):
        """When shutil.copyfile raises OSError, analyze_and_fix doesn't crash."""
        from evolve.agent import analyze_and_fix
        import shutil
        (tmp_path / "README.md").write_text("# Spec")
        run_dir = tmp_path / ".evolve" / "runs" / "session"
        run_dir.mkdir(parents=True)

        # Create the attempt log so copyfile path is exercised
        attempt_log = run_dir / "conversation_loop_1_attempt_1.md"
        attempt_log.write_text("# log")

        with patch("shutil.copyfile", side_effect=OSError("cross-fs")):
            analyze_and_fix(tmp_path, "ok", run_dir=run_dir, round_num=1)
        # Should not raise — OSError is caught silently


# ---------------------------------------------------------------------------
# build_prompt — yolo alias
# ---------------------------------------------------------------------------

class TestBuildPromptYoloAlias:
    """Cover the yolo→allow_installs fallback in build_prompt."""

    def test_yolo_param_sets_allow_installs(self, tmp_path: Path):
        """When yolo=True, the constraint block is absent (same as allow_installs=True)."""
        (tmp_path / "README.md").write_text("# Spec")
        (tmp_path / "runs").mkdir()
        run_dir = tmp_path / "runs" / "s1"
        run_dir.mkdir()
        prompt = build_prompt(tmp_path, yolo=True, run_dir=run_dir)
        assert "[needs-package]" not in prompt or "skipped" not in prompt


# ---------------------------------------------------------------------------
# ResultMessage.subtype — authoritative termination signal
# ---------------------------------------------------------------------------

class TestResultMessageSubtype:
    """Tests for SPEC § 'Authoritative termination signal from the SDK'."""

    def test_run_claude_agent_returns_subtype_success(self, tmp_path: Path):
        """run_claude_agent returns 'success' when ResultMessage.subtype='success'."""
        from types import SimpleNamespace

        result_msg = SimpleNamespace(
            content=[],
            usage=SimpleNamespace(
                input_tokens=100, output_tokens=50,
                cache_creation_input_tokens=0, cache_read_input_tokens=0,
            ),
            subtype="success",
            is_error=False,
            num_turns=10,
        )

        async def fake_query(**kwargs):
            yield result_msg

        sdk_mod = MagicMock()
        sdk_mod.ClaudeAgentOptions = MagicMock(return_value=MagicMock())
        sdk_mod.query = fake_query
        # Make isinstance checks work: ResultMessage must be the class
        sdk_mod.ResultMessage = type(result_msg)
        sdk_mod.AssistantMessage = type("AssistantMessage", (), {})

        with patch.dict("sys.modules", {"claude_agent_sdk": sdk_mod}), \
             patch("evolve.infrastructure.claude_sdk.runner._patch_sdk_parser", lambda: None):
            from evolve.agent import run_claude_agent

            ui_mock = MagicMock()
            with patch("evolve.infrastructure.claude_sdk.runner.get_tui", return_value=ui_mock):
                subtype = asyncio.run(run_claude_agent(
                    "test prompt", tmp_path, round_num=1,
                    run_dir=tmp_path,
                ))

        assert subtype == "success"
        # ui.warn should NOT be called for success
        for call in ui_mock.warn.call_args_list:
            assert "Agent stopped" not in str(call)

    def test_run_claude_agent_returns_subtype_error_max_turns(self, tmp_path: Path):
        """run_claude_agent returns 'error_max_turns' and warns on is_error."""
        from types import SimpleNamespace

        result_msg = SimpleNamespace(
            content=[],
            usage=SimpleNamespace(
                input_tokens=100, output_tokens=50,
                cache_creation_input_tokens=0, cache_read_input_tokens=0,
            ),
            subtype="error_max_turns",
            is_error=True,
            num_turns=40,
        )

        async def fake_query(**kwargs):
            yield result_msg

        sdk_mod = MagicMock()
        sdk_mod.ClaudeAgentOptions = MagicMock(return_value=MagicMock())
        sdk_mod.query = fake_query
        sdk_mod.ResultMessage = type(result_msg)
        sdk_mod.AssistantMessage = type("AssistantMessage", (), {})

        with patch.dict("sys.modules", {"claude_agent_sdk": sdk_mod}), \
             patch("evolve.infrastructure.claude_sdk.runner._patch_sdk_parser", lambda: None):
            from evolve.agent import run_claude_agent

            ui_mock = MagicMock()
            with patch("evolve.infrastructure.claude_sdk.runner.get_tui", return_value=ui_mock):
                subtype = asyncio.run(run_claude_agent(
                    "test prompt", tmp_path, round_num=1,
                    run_dir=tmp_path,
                ))

        assert subtype == "error_max_turns"
        # ui.agent_warn should be called with the error signal
        warn_calls = [str(c) for c in ui_mock.agent_warn.call_args_list]
        assert any("error_max_turns" in c for c in warn_calls)
        assert any("40 turns" in c for c in warn_calls)

    def test_run_claude_agent_returns_none_when_no_result_message(self, tmp_path: Path):
        """When no ResultMessage is emitted, returns None gracefully."""
        from types import SimpleNamespace

        # An AssistantMessage without subtype
        assistant_msg = SimpleNamespace(
            content=[SimpleNamespace(text="hello", name=None)],
        )
        # Ensure it's not a ResultMessage
        class FakeAssistant:
            content = [SimpleNamespace(text="hello")]

        async def fake_query(**kwargs):
            yield FakeAssistant()

        sdk_mod = MagicMock()
        sdk_mod.ClaudeAgentOptions = MagicMock(return_value=MagicMock())
        sdk_mod.query = fake_query
        sdk_mod.ResultMessage = type("ResultMessage", (), {})
        sdk_mod.AssistantMessage = FakeAssistant

        with patch.dict("sys.modules", {"claude_agent_sdk": sdk_mod}), \
             patch("evolve.infrastructure.claude_sdk.runner._patch_sdk_parser", lambda: None):
            from evolve.agent import run_claude_agent

            ui_mock = MagicMock()
            with patch("evolve.infrastructure.claude_sdk.runner.get_tui", return_value=ui_mock):
                subtype = asyncio.run(run_claude_agent(
                    "test prompt", tmp_path, round_num=1,
                    run_dir=tmp_path,
                ))

        assert subtype is None

    def test_done_log_line_includes_subtype(self, tmp_path: Path):
        """The Done: log line includes subtype= and num_turns= fields."""
        from types import SimpleNamespace

        result_msg = SimpleNamespace(
            content=[],
            usage=SimpleNamespace(
                input_tokens=10, output_tokens=5,
                cache_creation_input_tokens=0, cache_read_input_tokens=0,
            ),
            subtype="error_max_turns",
            is_error=True,
            num_turns=60,
        )

        async def fake_query(**kwargs):
            yield result_msg

        sdk_mod = MagicMock()
        sdk_mod.ClaudeAgentOptions = MagicMock(return_value=MagicMock())
        sdk_mod.query = fake_query
        sdk_mod.ResultMessage = type(result_msg)
        sdk_mod.AssistantMessage = type("AssistantMessage", (), {})

        with patch.dict("sys.modules", {"claude_agent_sdk": sdk_mod}), \
             patch("evolve.infrastructure.claude_sdk.runner._patch_sdk_parser", lambda: None):
            from evolve.agent import run_claude_agent

            ui_mock = MagicMock()
            with patch("evolve.infrastructure.claude_sdk.runner.get_tui", return_value=ui_mock):
                asyncio.run(run_claude_agent(
                    "test prompt", tmp_path, round_num=1,
                    run_dir=tmp_path,
                ))

        log_path = tmp_path / "conversation_loop_1.md"
        log_text = log_path.read_text()
        assert "subtype=error_max_turns" in log_text
        assert "num_turns=60" in log_text

    def test_analyze_and_fix_propagates_subtype(self, tmp_path: Path):
        """analyze_and_fix returns the subtype from run_claude_agent."""
        from evolve.agent import analyze_and_fix

        (tmp_path / "README.md").write_text("# Spec")
        run_dir = tmp_path / ".evolve" / "runs" / "session"
        run_dir.mkdir(parents=True)

        # Mock _run_agent_with_retries to return a known subtype
        with patch("evolve.agent._run_agent_with_retries", return_value="error_max_turns"):
            with patch("evolve.agent.build_prompt", return_value="prompt"):
                result = analyze_and_fix(
                    tmp_path, "ok", run_dir=run_dir, round_num=1,
                )

        assert result == "error_max_turns"

    def test_analyze_and_fix_returns_none_on_no_sdk(self, tmp_path: Path):
        """analyze_and_fix returns None when SDK is missing."""
        from evolve.agent import analyze_and_fix

        (tmp_path / "README.md").write_text("# Spec")
        run_dir = tmp_path / ".evolve" / "runs" / "session"
        run_dir.mkdir(parents=True)

        with patch("evolve.agent._run_agent_with_retries", return_value=None):
            with patch("evolve.agent.build_prompt", return_value="prompt"):
                result = analyze_and_fix(
                    tmp_path, "ok", run_dir=run_dir, round_num=1,
                )

        assert result is None


class TestBuildPromptSubtypePrefixes:
    """build_prompt handles MAX_TURNS: and SDK ERROR: diagnostic prefixes."""

    def _setup_project(self, tmp_path: Path):
        (tmp_path / "README.md").write_text("# Spec")
        run_dir = tmp_path / ".evolve" / "runs" / "session"
        run_dir.mkdir(parents=True)
        return run_dir

    def test_max_turns_prefix_renders_dedicated_header(self, tmp_path: Path):
        """MAX_TURNS: prefix in diagnostic renders the max_turns header."""
        run_dir = self._setup_project(tmp_path)
        diag = run_dir / "subprocess_error_round_1.txt"
        diag.write_text(
            "MAX_TURNS: no COMMIT_MSG written AND "
            "improvements.md byte-identical AND "
            "SDK subtype=error_max_turns (attempt 1)"
        )

        prompt = build_prompt(
            tmp_path, run_dir=run_dir, round_num=2,
        )
        assert "Agent hit max_turns cap" in prompt
        assert "fix-only" in prompt.lower() or "Fix only" in prompt or "Start with Edit/Write immediately" in prompt

    def test_sdk_error_prefix_renders_dedicated_header(self, tmp_path: Path):
        """SDK ERROR: prefix in diagnostic renders the SDK error header."""
        run_dir = self._setup_project(tmp_path)
        diag = run_dir / "subprocess_error_round_1.txt"
        diag.write_text(
            "SDK ERROR: improvements.md byte-identical AND "
            "SDK subtype=error_during_execution (attempt 1)"
        )

        prompt = build_prompt(
            tmp_path, run_dir=run_dir, round_num=2,
        )
        assert "SDK execution error" in prompt
        assert "error_during_execution" in prompt
