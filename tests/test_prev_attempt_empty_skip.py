"""Tests for the "skip prior-attempt section when log is empty" rule.

``_PREV_ATTEMPT_LOG_FMT`` tells the agent to "Read this file FIRST"
— a dutiful instruction that produces pure noise when the prior
attempt's log is empty (a scope-creep kill before any tool calls,
a circuit-breaker exit before the first Edit, etc.).  The fix
inspects the prior log's content before rendering the section:
under 500 bytes OR missing any tool-call marker → section omitted.
"""

from __future__ import annotations

from pathlib import Path

from evolve.infrastructure.claude_sdk.prompt_builder import build_prompt


def _setup(tmp_path: Path) -> Path:
    (tmp_path / "README.md").write_text("# P\n")
    run_dir = tmp_path / "runs" / "session"
    run_dir.mkdir(parents=True)
    # Mark this as attempt 2 of round 7 by dropping a diagnostic
    # with ``(attempt 1)`` in the run_dir.
    (run_dir / "subprocess_error_round_7.txt").write_text(
        "Round 7 — scope creep (attempt 1)\n"
    )
    return run_dir


class TestPrevAttemptSectionSkip:
    def test_empty_prior_log_skips_section(self, tmp_path: Path):
        """A 1-line placeholder log does NOT trigger the section."""
        run_dir = _setup(tmp_path)
        # Near-empty prior log — header only, no content.
        (run_dir / "conversation_loop_7_attempt_1.md").write_text(
            "# Round 7 attempt 1\n"
        )

        prompt = build_prompt(
            tmp_path, check_output="", check_cmd=None,
            allow_installs=False, run_dir=run_dir, round_num=7,
        )

        # The phrase ``## Previous attempt log`` appears in the system
        # prompt's documentation too; the rendered SECTION is uniquely
        # identified by the template's "This is attempt K of round N"
        # line and the "Read this file FIRST" instruction.  Match on
        # those to detect section absence / presence.
        assert "This is attempt 2 of round 7" not in prompt
        assert "Read this file FIRST" not in prompt
        assert "Read this file FIRST" not in prompt

    def test_nontrivial_prior_log_keeps_section(self, tmp_path: Path):
        """A substantive prior log (≥500 bytes with tool calls)
        triggers the section — retry-continuity actually helps."""
        run_dir = _setup(tmp_path)
        content = (
            "# Round 7 attempt 1\n\n"
            "Starting analysis.\n\n"
            "**Read**: `/src/foo.py`\n"
            "Found a bug in the parser: it doesn't handle nested braces.\n"
            + "Detailed reasoning goes here " * 30 + "\n"
            "**Edit**: `/src/foo.py` (edit)\n"
        )
        (run_dir / "conversation_loop_7_attempt_1.md").write_text(content)

        prompt = build_prompt(
            tmp_path, check_output="", check_cmd=None,
            allow_installs=False, run_dir=run_dir, round_num=7,
        )

        assert "This is attempt 2 of round 7" in prompt
        assert "Read this file FIRST" in prompt

    def test_large_log_without_tool_calls_still_skipped(self, tmp_path: Path):
        """A log that's large but contains no tool-call markers
        (pure prose, no actual work) is still treated as noise.
        """
        run_dir = _setup(tmp_path)
        # > 500 bytes of text but no markers — e.g. an aborted
        # "thinking aloud" preamble before the agent got killed.
        (run_dir / "conversation_loop_7_attempt_1.md").write_text(
            "# Round 7 attempt 1\n\n"
            "I should start by reading the spec.  " * 40
        )

        prompt = build_prompt(
            tmp_path, check_output="", check_cmd=None,
            allow_installs=False, run_dir=run_dir, round_num=7,
        )

        # The phrase ``## Previous attempt log`` appears in the system
        # prompt's documentation too; the rendered SECTION is uniquely
        # identified by the template's "This is attempt K of round N"
        # line and the "Read this file FIRST" instruction.  Match on
        # those to detect section absence / presence.
        assert "This is attempt 2 of round 7" not in prompt
        assert "Read this file FIRST" not in prompt

    def test_small_log_with_tool_calls_still_skipped(self, tmp_path: Path):
        """< 500 bytes with a single tool-call marker is still
        judged too thin to be worth "continue from here".
        """
        run_dir = _setup(tmp_path)
        (run_dir / "conversation_loop_7_attempt_1.md").write_text(
            "# Round 7 attempt 1\n\n**Read**: `/src/foo.py`\n"
        )

        prompt = build_prompt(
            tmp_path, check_output="", check_cmd=None,
            allow_installs=False, run_dir=run_dir, round_num=7,
        )

        # The phrase ``## Previous attempt log`` appears in the system
        # prompt's documentation too; the rendered SECTION is uniquely
        # identified by the template's "This is attempt K of round N"
        # line and the "Read this file FIRST" instruction.  Match on
        # those to detect section absence / presence.
        assert "This is attempt 2 of round 7" not in prompt
        assert "Read this file FIRST" not in prompt
