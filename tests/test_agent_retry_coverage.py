"""Coverage tests for agent.py — analyze_and_fix retry, _patch_sdk_parser, build_prompt."""

import asyncio
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from evolve.infrastructure.claude_sdk.agent import analyze_and_fix
from evolve.infrastructure.claude_sdk.runtime import _patch_sdk_parser
from evolve.infrastructure.claude_sdk.prompt_builder import build_prompt


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

        with patch("evolve.interfaces.tui.get_tui", return_value=self.mock_ui), \
             patch.dict("sys.modules", {"claude_agent_sdk": self._mock_sdk}), \
             patch("evolve.infrastructure.claude_sdk.runtime.asyncio.run", side_effect=mock_asyncio_run):
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

        with patch("evolve.interfaces.tui.get_tui", return_value=self.mock_ui), \
             patch.dict("sys.modules", {"claude_agent_sdk": self._mock_sdk}), \
             patch("evolve.infrastructure.claude_sdk.runtime.asyncio.run", side_effect=mock_asyncio_run), \
             patch("evolve.infrastructure.claude_sdk.runtime.time.sleep"):
            analyze_and_fix(tmp_path, max_retries=5)

        assert call_count == 3
        assert self.mock_ui.sdk_rate_limited.call_count == 2

    def test_non_retryable_error_gives_up(self, tmp_path: Path):
        """Non-retryable error calls warn and returns."""
        self._setup_project(tmp_path)

        def mock_asyncio_run(coro):
            coro.close()  # prevent "coroutine was never awaited" warning
            raise Exception("some random error")

        with patch("evolve.interfaces.tui.get_tui", return_value=self.mock_ui), \
             patch.dict("sys.modules", {"claude_agent_sdk": self._mock_sdk}), \
             patch("evolve.infrastructure.claude_sdk.runtime.asyncio.run", side_effect=mock_asyncio_run):
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

        with patch("evolve.interfaces.tui.get_tui", return_value=mock_ui), \
             patch.dict("sys.modules", {"claude_agent_sdk": mock_sdk}), \
             patch("evolve.infrastructure.claude_sdk.runner.run_claude_agent", side_effect=mock_run_agent):
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

        with patch("evolve.interfaces.tui.get_tui", return_value=mock_ui), \
             patch.dict("sys.modules", {"claude_agent_sdk": mock_sdk}), \
             patch("evolve.infrastructure.claude_sdk.runner.run_claude_agent", side_effect=mock_run_agent), \
             patch("evolve.infrastructure.claude_sdk.runtime.time.sleep"):
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

        with patch("evolve.interfaces.tui.get_tui", return_value=mock_ui), \
             patch.dict("sys.modules", {"claude_agent_sdk": mock_sdk}), \
             patch("evolve.infrastructure.claude_sdk.runner.run_claude_agent", side_effect=mock_run_agent):
            analyze_and_fix(tmp_path, round_num=7, run_dir=run_dir)

        assert captured_calls[0]["log_filename"] == "conversation_loop_7_attempt_2.md"
