"""Tests for retry continuity — SPEC.md § "Retry continuity".

These tests verify the four invariants the retry-continuity target claims:

1. A crashing round writes ``conversation_loop_N_attempt_1.md`` and the
   debug retry writes ``conversation_loop_N_attempt_2.md`` — the first
   attempt's log is NOT overwritten.
2. The retry prompt contains the full path to the prior attempt log and
   the documented "Read this file FIRST" instruction under a
   ``## Previous attempt log`` section.
3. ``conversation_loop_N.md`` is produced (copy of the winning attempt)
   after a successful attempt for backward compatibility with report
   generation, party mode, and self-monitoring.
4. The agent-side self-monitoring (prompts/system.md) instructs the
   agent to check prior attempts of the CURRENT round (Step 0) *before*
   looking at rounds N-1 / N-2 (Step 1).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from evolve.infrastructure.claude_sdk.agent import (
    _detect_current_attempt,
    analyze_and_fix,
)
from evolve.infrastructure.claude_sdk.prompt_builder import build_prompt


# ---------------------------------------------------------------------------
# Fixture helper
# ---------------------------------------------------------------------------

def _setup_project(tmp_path: Path) -> Path:
    """Minimal project layout for analyze_and_fix / build_prompt tests."""
    (tmp_path / "README.md").write_text("# Retry continuity test project\n")
    (tmp_path / "runs").mkdir()
    run_dir = tmp_path / "runs" / "session"
    run_dir.mkdir()
    return run_dir


def _diagnostic_attempt(k: int) -> str:
    """Shape of subprocess_error_round_N.txt as written by the orchestrator."""
    return f"Round 7 — crashed (attempt {k})\nCommand: foo\n\nOutput:\n...\n"


# ---------------------------------------------------------------------------
# Invariant (1): per-attempt logs, no overwrite
# ---------------------------------------------------------------------------

class TestPerAttemptLogsNoOverwrite:
    """First crashing attempt + debug retry must produce two distinct files."""

    def test_first_attempt_log_not_overwritten_by_retry(self, tmp_path: Path):
        """attempt_1 log is preserved when attempt 2 runs on the same round."""
        run_dir = _setup_project(tmp_path)
        round_num = 7

        # --- Attempt 1: simulate a crashing first run that wrote its log.
        attempt_1_log = run_dir / f"conversation_loop_{round_num}_attempt_1.md"
        attempt_1_content = (
            "# Attempt 1 transcript\n\n"
            "- Read README.md\n- Edit foo.py — failed assertion\n"
            "- [stacktrace snippet]\n"
        )
        attempt_1_log.write_text(attempt_1_content)

        # Orchestrator writes the diagnostic after attempt 1 crashes.
        (run_dir / f"subprocess_error_round_{round_num}.txt").write_text(
            _diagnostic_attempt(1),
        )

        # --- Attempt 2: analyze_and_fix is re-invoked by the retry loop.
        captured = []

        async def mock_run_agent(
            prompt, project_dir, round_num=1, run_dir=None, log_filename=None
        ):
            captured.append({"log_filename": log_filename, "run_dir": run_dir})
            # The agent writes its transcript to the per-attempt file.
            if run_dir is not None and log_filename is not None:
                (Path(run_dir) / log_filename).write_text(
                    "# Attempt 2 transcript\n\n- Continued from attempt 1\n"
                )

        with patch("evolve.interfaces.tui.get_tui", return_value=MagicMock()), \
             patch("evolve.infrastructure.claude_sdk.runner.run_claude_agent", side_effect=mock_run_agent):
            analyze_and_fix(tmp_path, round_num=round_num, run_dir=run_dir)

        # Attempt 2's invocation must have targeted the attempt_2 log.
        assert captured, "run_claude_agent was not invoked"
        assert captured[0]["log_filename"] == (
            f"conversation_loop_{round_num}_attempt_2.md"
        )

        # Attempt 1's log is still on disk, bit-for-bit identical.
        assert attempt_1_log.is_file(), "attempt 1 log was deleted by retry"
        assert attempt_1_log.read_text() == attempt_1_content

        # Attempt 2's log is a new, distinct file.
        attempt_2_log = run_dir / f"conversation_loop_{round_num}_attempt_2.md"
        assert attempt_2_log.is_file()
        assert attempt_2_log.read_text() != attempt_1_content

    def test_detect_current_attempt_increments_after_crash(self, tmp_path: Path):
        """_detect_current_attempt reads (attempt K) and returns K+1."""
        run_dir = _setup_project(tmp_path)
        round_num = 4

        # No diagnostic yet — first attempt.
        assert _detect_current_attempt(run_dir, round_num) == 1

        (run_dir / f"subprocess_error_round_{round_num}.txt").write_text(
            _diagnostic_attempt(1),
        )
        assert _detect_current_attempt(run_dir, round_num) == 2

        # Second crash bumps the counter again.
        (run_dir / f"subprocess_error_round_{round_num}.txt").write_text(
            _diagnostic_attempt(2),
        )
        assert _detect_current_attempt(run_dir, round_num) == 3

    def test_detect_current_attempt_ignores_other_round_diagnostics(
        self, tmp_path: Path
    ):
        """A diagnostic from a different round must not advance this round's
        attempt counter."""
        run_dir = _setup_project(tmp_path)
        # Diagnostic exists but it's for round 3, not round 8.
        (run_dir / "subprocess_error_round_3.txt").write_text(
            _diagnostic_attempt(2),
        )
        assert _detect_current_attempt(run_dir, round_num=8) == 1


# ---------------------------------------------------------------------------
# Invariant (2): retry prompt surfaces prior attempt log path & instruction
# ---------------------------------------------------------------------------

class TestRetryPromptSurfacesPriorAttempt:
    """build_prompt on a debug retry must surface the prior attempt log."""

    def _prepare_retry_state(self, tmp_path: Path, round_num: int, attempt_k: int):
        """Create diagnostic + prior attempt logs so build_prompt detects retry.

        The prior log must contain substantive content (≥ 500 bytes +
        at least one tool-call marker) to pass the "trivially empty"
        guard in ``build_prompt``; an empty stub is now treated as
        noise and the section is suppressed.
        """
        run_dir = _setup_project(tmp_path)
        (run_dir / f"subprocess_error_round_{round_num}.txt").write_text(
            _diagnostic_attempt(attempt_k),
        )
        prior_log = run_dir / (
            f"conversation_loop_{round_num}_attempt_{attempt_k}.md"
        )
        prior_log.write_text(
            "# Attempt transcript\n\n"
            "Started analysis of the target.\n\n"
            "**Read**: `/src/foo.py`\n"
            "Found that the parser doesn't handle nested braces correctly — "
            "line 42 fails on input `{{nested}}`.  "
            + "Detailed investigation continues. " * 20
            + "\n\n**Edit**: `/src/foo.py` (edit)\n"
        )
        return run_dir, prior_log

    def test_retry_prompt_contains_prior_attempt_section_header(
        self, tmp_path: Path
    ):
        """Second attempt's prompt carries a ## Previous attempt log section."""
        round_num = 6
        run_dir, _ = self._prepare_retry_state(
            tmp_path, round_num=round_num, attempt_k=1
        )
        prompt = build_prompt(tmp_path, run_dir=run_dir, round_num=round_num)
        # The section header renders as its own stand-alone block.  The system
        # prompt references the section name in prose ("under `## Previous
        # attempt log`"), so we must match the standalone section.
        assert "\n## Previous attempt log\n" in prompt

    def test_retry_prompt_contains_full_path_to_prior_log(self, tmp_path: Path):
        """Prompt exposes the exact on-disk path of the previous attempt log."""
        round_num = 6
        run_dir, prior_log = self._prepare_retry_state(
            tmp_path, round_num=round_num, attempt_k=1
        )
        prompt = build_prompt(tmp_path, run_dir=run_dir, round_num=round_num)
        assert str(prior_log) in prompt

    def test_retry_prompt_contains_read_first_instruction(self, tmp_path: Path):
        """Prompt uses the documented 'Read this file FIRST' wording."""
        round_num = 9
        run_dir, _ = self._prepare_retry_state(
            tmp_path, round_num=round_num, attempt_k=1
        )
        prompt = build_prompt(tmp_path, run_dir=run_dir, round_num=round_num)
        assert "Read this file FIRST" in prompt
        # The rationale text is part of the documented wording.
        assert "Do not redo that investigation" in prompt
        assert "Continue" in prompt

    def test_retry_prompt_reports_correct_attempt_numbers(self, tmp_path: Path):
        """Prompt names the current attempt (K+1) and the prior attempt (K)."""
        round_num = 5
        run_dir, _ = self._prepare_retry_state(
            tmp_path, round_num=round_num, attempt_k=1
        )
        prompt = build_prompt(tmp_path, run_dir=run_dir, round_num=round_num)
        # After attempt 1, current is 2 and prior is 1.
        assert "attempt 2 of round 5" in prompt
        assert "attempt 1 is at" in prompt

    def test_first_attempt_prompt_has_no_prior_attempt_section(
        self, tmp_path: Path
    ):
        """First attempt of a fresh round must NOT render the section.

        The system prompt itself references the section name in prose
        (Step 0 explains that ``build_prompt`` surfaces the prior-attempt
        log under ``## Previous attempt log`` when applicable), so we check
        for the standalone rendered section — a leading newline, the
        header, and a trailing newline — rather than the bare string.
        """
        run_dir = _setup_project(tmp_path)
        prompt = build_prompt(tmp_path, run_dir=run_dir, round_num=1)
        assert "\n## Previous attempt log\n" not in prompt
        # And the content that would only appear in the rendered section
        # is likewise absent.
        assert "Read this file FIRST" not in prompt

    def test_prior_attempt_log_missing_skips_section(self, tmp_path: Path):
        """Diagnostic exists but prior log file is missing → section omitted."""
        run_dir = _setup_project(tmp_path)
        round_num = 2
        (run_dir / f"subprocess_error_round_{round_num}.txt").write_text(
            _diagnostic_attempt(1),
        )
        # No prior attempt log on disk.
        prompt = build_prompt(tmp_path, run_dir=run_dir, round_num=round_num)
        assert "\n## Previous attempt log\n" not in prompt


# ---------------------------------------------------------------------------
# Invariant (3): conversation_loop_N.md copy for backward compatibility
# ---------------------------------------------------------------------------

class TestCanonicalLogCopy:
    """A successful attempt copies its per-attempt log to the canonical name."""

    def test_canonical_log_mirrors_attempt_log(self, tmp_path: Path):
        """conversation_loop_N.md has the exact content of the winning attempt."""
        run_dir = _setup_project(tmp_path)
        round_num = 3
        attempt_content = "# Round 3 / attempt 1\n\n- final transcript\n"

        async def mock_run_agent(
            prompt, project_dir, round_num=1, run_dir=None, log_filename=None
        ):
            (Path(run_dir) / log_filename).write_text(attempt_content)

        with patch("evolve.interfaces.tui.get_tui", return_value=MagicMock()), \
             patch("evolve.infrastructure.claude_sdk.runner.run_claude_agent", side_effect=mock_run_agent):
            analyze_and_fix(tmp_path, round_num=round_num, run_dir=run_dir)

        canonical = run_dir / f"conversation_loop_{round_num}.md"
        attempt = run_dir / f"conversation_loop_{round_num}_attempt_1.md"
        assert canonical.is_file()
        assert attempt.is_file()
        assert canonical.read_text() == attempt_content
        assert canonical.read_text() == attempt.read_text()

    def test_canonical_log_replaced_when_second_attempt_succeeds(
        self, tmp_path: Path
    ):
        """When attempt 2 succeeds after attempt 1 crashed, the canonical log
        points at attempt 2's transcript (the latest successful run).
        """
        run_dir = _setup_project(tmp_path)
        round_num = 8

        # Attempt 1 crashed: its log is on disk + diagnostic recorded.
        attempt_1_log = run_dir / f"conversation_loop_{round_num}_attempt_1.md"
        attempt_1_log.write_text("# attempt 1 — crashed before finishing\n")
        (run_dir / f"subprocess_error_round_{round_num}.txt").write_text(
            _diagnostic_attempt(1),
        )

        attempt_2_content = "# attempt 2 — finished successfully\n"

        async def mock_run_agent(
            prompt, project_dir, round_num=1, run_dir=None, log_filename=None
        ):
            (Path(run_dir) / log_filename).write_text(attempt_2_content)

        with patch("evolve.interfaces.tui.get_tui", return_value=MagicMock()), \
             patch("evolve.infrastructure.claude_sdk.runner.run_claude_agent", side_effect=mock_run_agent):
            analyze_and_fix(tmp_path, round_num=round_num, run_dir=run_dir)

        canonical = run_dir / f"conversation_loop_{round_num}.md"
        assert canonical.read_text() == attempt_2_content

    def test_canonical_log_copy_is_not_a_symlink(self, tmp_path: Path):
        """The copy is a real file, not a symlink — matches SPEC's
        'copy (not symlink)' guarantee for cross-filesystem safety.
        """
        run_dir = _setup_project(tmp_path)
        round_num = 11

        async def mock_run_agent(
            prompt, project_dir, round_num=1, run_dir=None, log_filename=None
        ):
            (Path(run_dir) / log_filename).write_text("# content\n")

        with patch("evolve.interfaces.tui.get_tui", return_value=MagicMock()), \
             patch("evolve.infrastructure.claude_sdk.runner.run_claude_agent", side_effect=mock_run_agent):
            analyze_and_fix(tmp_path, round_num=round_num, run_dir=run_dir)

        canonical = run_dir / f"conversation_loop_{round_num}.md"
        assert canonical.is_file()
        assert not canonical.is_symlink()

    def test_canonical_log_skipped_when_attempt_log_missing(
        self, tmp_path: Path
    ):
        """If the attempt log wasn't actually written (e.g. agent crashed
        before streaming anything), the canonical-copy step is a no-op
        rather than raising.
        """
        run_dir = _setup_project(tmp_path)
        round_num = 2

        async def mock_run_agent(
            prompt, project_dir, round_num=1, run_dir=None, log_filename=None
        ):
            # Agent "crashed" before writing any log — do nothing here.
            return

        with patch("evolve.interfaces.tui.get_tui", return_value=MagicMock()), \
             patch("evolve.infrastructure.claude_sdk.runner.run_claude_agent", side_effect=mock_run_agent):
            # Should not raise even though no log was written.
            analyze_and_fix(tmp_path, round_num=round_num, run_dir=run_dir)

        canonical = run_dir / f"conversation_loop_{round_num}.md"
        assert not canonical.exists()


# ---------------------------------------------------------------------------
# Invariant (4): agent-side self-monitoring instructions (prompts/system.md)
# ---------------------------------------------------------------------------

class TestAgentSideSelfMonitoringInstructions:
    """The system prompt must instruct the agent to read prior-attempt logs
    of the CURRENT round before the N-1 / N-2 check."""

    def _load_system_prompt(self, tmp_path: Path) -> str:
        """build_prompt returns the system prompt concatenated with context —
        that's what the agent actually sees at runtime, so that's what we test.
        """
        _setup_project(tmp_path)
        return build_prompt(tmp_path, run_dir=tmp_path / "runs" / "session", round_num=5)

    def test_step_0_instructs_glob_for_prior_attempts(self, tmp_path: Path):
        """Step 0 tells the agent to glob the current round's attempt logs."""
        prompt = self._load_system_prompt(tmp_path)
        # Pattern the agent must search for — the round_num placeholder is
        # substituted to the current round (5 in this test).
        assert "conversation_loop_5_attempt_*.md" in prompt

    def test_step_0_labelled_highest_priority(self, tmp_path: Path):
        """Step 0 is explicitly marked as the top-priority action."""
        prompt = self._load_system_prompt(tmp_path)
        assert "Step 0" in prompt
        # The section header names it highest priority.
        assert "highest priority" in prompt

    def test_step_0_precedes_step_1_in_prompt(self, tmp_path: Path):
        """Prior-attempt check (Step 0) MUST appear before the N-1/N-2
        stuck-loop check (Step 1) in the rendered prompt text.
        """
        prompt = self._load_system_prompt(tmp_path)
        idx_step_0 = prompt.find("Step 0")
        idx_step_1 = prompt.find("Step 1")
        assert idx_step_0 != -1, "Step 0 missing from system prompt"
        assert idx_step_1 != -1, "Step 1 missing from system prompt"
        assert idx_step_0 < idx_step_1, (
            "Step 0 (prior-attempt check) must precede Step 1 "
            "(stuck-loop check) in the system prompt"
        )

    def test_step_0_instructs_read_all_before_anything_else(self, tmp_path: Path):
        """Step 0's wording requires reading attempt logs before any work."""
        prompt = self._load_system_prompt(tmp_path)
        assert "read them all before doing anything else" in prompt

    def test_step_0_warns_against_redoing_investigation(self, tmp_path: Path):
        """Step 0 explicitly forbids re-running the previous attempt's work."""
        prompt = self._load_system_prompt(tmp_path)
        assert "Continue from where the prior attempt stopped" in prompt
        assert "not** redo" in prompt or "not redo" in prompt

    def test_step_1_still_applies_on_subsequent_rounds(self, tmp_path: Path):
        """Step 1 (stuck-loop / N-1, N-2 log read) must still be described."""
        prompt = self._load_system_prompt(tmp_path)
        # Round 5's Step 1 references rounds 4 and 3.
        assert "conversation_loop_4.md" in prompt
        assert "conversation_loop_3.md" in prompt

    def test_step_0_applies_even_when_step_1_is_skipped(self, tmp_path: Path):
        """Step 0 check applies on every round — including round 1 where
        Step 1 is skipped because there's no N-1/N-2 history yet.
        """
        _setup_project(tmp_path)
        prompt = build_prompt(
            tmp_path, run_dir=tmp_path / "runs" / "session", round_num=1,
        )
        # Step 0 still present with round_num=1.
        assert "Step 0" in prompt
        assert "conversation_loop_1_attempt_*.md" in prompt
        # The explicit "Step 0 still applies on every round" rule is there.
        assert "every round" in prompt
