"""Extended tests for agent.py — build_prompt edge cases, analyze_and_fix error paths."""

import textwrap
from pathlib import Path
from unittest.mock import patch, MagicMock

from evolve.infrastructure.claude_sdk.prompt_builder import build_prompt
from evolve.infrastructure.claude_sdk.runtime import (
    _is_benign_runtime_error,
    _patch_sdk_parser,
)
from evolve.infrastructure.claude_sdk.runtime import _should_retry_rate_limit
from evolve.infrastructure.claude_sdk.agent import analyze_and_fix


# ---------------------------------------------------------------------------
# build_prompt — extended edge cases
# ---------------------------------------------------------------------------

class TestBuildPromptExtended:
    def test_readme_rst(self, tmp_path: Path):
        """Falls back to README.rst when README.md doesn't exist."""
        (tmp_path / "README.rst").write_text("RST readme content")
        (tmp_path / "runs").mkdir()
        prompt = build_prompt(tmp_path)
        assert "RST readme content" in prompt

    def test_readme_txt(self, tmp_path: Path):
        """Falls back to README.txt."""
        (tmp_path / "README.txt").write_text("TXT readme content")
        (tmp_path / "runs").mkdir()
        prompt = build_prompt(tmp_path)
        assert "TXT readme content" in prompt

    def test_readme_plain(self, tmp_path: Path):
        """Falls back to README (no extension)."""
        (tmp_path / "README").write_text("Plain readme content")
        (tmp_path / "runs").mkdir()
        prompt = build_prompt(tmp_path)
        assert "Plain readme content" in prompt

    def test_no_improvements_file(self, tmp_path: Path):
        """Prompt says improvements don't exist yet."""
        (tmp_path / "README.md").write_text("# P")
        (tmp_path / "runs").mkdir()
        prompt = build_prompt(tmp_path)
        assert "does not exist yet" in prompt

    def test_memory_included(self, tmp_path: Path):
        """Memory file content is included in prompt."""
        (tmp_path / "README.md").write_text("# P")
        runs = tmp_path / "runs"
        runs.mkdir()
        (runs / "memory.md").write_text("## Error: something broke\n- fix: did X")
        prompt = build_prompt(tmp_path)
        assert "something broke" in prompt

    def test_check_cmd_no_output(self, tmp_path: Path):
        """Check cmd specified but no output yet."""
        (tmp_path / "README.md").write_text("# P")
        (tmp_path / "runs").mkdir()
        prompt = build_prompt(tmp_path, check_cmd="pytest", check_output="")
        assert "pytest" in prompt
        assert "Run this command" in prompt

    def test_no_check_cmd_section(self, tmp_path: Path):
        """No check command configured section."""
        (tmp_path / "README.md").write_text("# P")
        (tmp_path / "runs").mkdir()
        prompt = build_prompt(tmp_path)
        assert "No check command configured" in prompt

    def test_previous_check_results(self, tmp_path: Path):
        """Previous check results from run_dir are included."""
        (tmp_path / "README.md").write_text("# P")
        runs = tmp_path / "runs"
        runs.mkdir()
        run_dir = runs / "session1"
        run_dir.mkdir()
        (run_dir / "check_round_1.txt").write_text("Round 1: PASS")
        prompt = build_prompt(tmp_path, run_dir=run_dir)
        assert "Round 1: PASS" in prompt

    def test_all_checked_no_target(self, tmp_path: Path):
        """When all items are checked, target says create initial."""
        (tmp_path / "README.md").write_text("# P")
        runs = tmp_path / "runs"
        runs.mkdir()
        (runs / "improvements.md").write_text("- [x] [functional] all done\n")
        prompt = build_prompt(tmp_path)
        assert "create initial" in prompt

    def test_run_dir_interpolation(self, tmp_path: Path):
        """run_dir is used as fallback when no run_dir specified."""
        (tmp_path / "README.md").write_text("# P")
        (tmp_path / "runs").mkdir()
        prompt = build_prompt(tmp_path, run_dir=None)
        # Default run_dir is "runs" string
        assert prompt is not None  # just verify no crash


# ---------------------------------------------------------------------------
# _patch_sdk_parser — safe when SDK not installed
# ---------------------------------------------------------------------------

class TestPatchSdkParser:
    def test_no_crash_when_sdk_missing(self):
        """_patch_sdk_parser doesn't crash when SDK is not installed."""
        with patch.dict("sys.modules", {"claude_agent_sdk": None,
                                         "claude_agent_sdk._internal": None,
                                         "claude_agent_sdk._internal.message_parser": None}):
            _patch_sdk_parser()  # should not raise


# ---------------------------------------------------------------------------
# _is_benign_runtime_error — extended
# ---------------------------------------------------------------------------

class TestIsBenignExtended:
    def test_combined_message(self):
        """Message containing both patterns."""
        assert _is_benign_runtime_error(RuntimeError("cancel scope Event loop is closed")) is True

    def test_empty_message(self):
        assert _is_benign_runtime_error(RuntimeError("")) is False


# ---------------------------------------------------------------------------
# _should_retry_rate_limit — extended
# ---------------------------------------------------------------------------

class TestShouldRetryExtended:
    def test_case_insensitive(self):
        """Rate_Limit in different cases should match."""
        e = Exception("Rate_Limit error occurred")
        assert _should_retry_rate_limit(e, 1, 5) == 60

    def test_attempt_equals_max(self):
        """Last attempt should return None."""
        e = Exception("rate_limit_exceeded")
        assert _should_retry_rate_limit(e, 5, 5) is None

    def test_zero_attempts(self):
        """Edge case: attempt 0."""
        e = Exception("rate_limit_exceeded")
        result = _should_retry_rate_limit(e, 0, 5)
        assert result == 0  # 60 * 0

    def test_generic_error_not_retryable(self):
        e = ValueError("bad input")
        assert _should_retry_rate_limit(e, 1, 5) is None


# ---------------------------------------------------------------------------
# analyze_and_fix — error/import paths
# ---------------------------------------------------------------------------

class TestAnalyzeAndFix:
    def test_sdk_not_installed(self, tmp_path: Path):
        """Returns gracefully when SDK is not installed."""
        (tmp_path / "README.md").write_text("# P")
        (tmp_path / "runs").mkdir()
        mock_ui = MagicMock()
        with patch.dict("sys.modules", {"claude_agent_sdk": None}), \
             patch("evolve.interfaces.tui.get_tui", return_value=mock_ui):
            analyze_and_fix(tmp_path)
        mock_ui.warn.assert_called_once_with(
            "claude-agent-sdk not installed, skipping agent"
        )
