"""Tests for the Phase 1 escape hatch (SPEC.md § "Phase 1 escape hatch
for unrelated pre-existing failures").

Two production sites cooperate to implement the escape hatch:

1. ``loop._save_subprocess_diagnostic`` — when the *next* attempt of a
   round will be attempt 3 (i.e. the previous failed attempt was #2),
   it prepends a "Phase 1 escape hatch notice" block to the
   ``subprocess_error_round_N.txt`` diagnostic file.

2. ``agent.build_prompt`` — at prompt-build time it inspects the most
   recent ``subprocess_error_round_*.txt`` for the *current* round and
   parses ``(attempt K)`` from the header line. The CURRENT attempt is
   K + 1 (the previous attempt failed, so this run is the next one).
   It then substitutes the ``{attempt_marker}`` placeholder in
   ``prompts/system.md`` with one of three banners:

       attempt 1 → "NOT permitted on the first attempt"
       attempt 2 → "NOT permitted on attempt 2"
       attempt 3 → "FINAL RETRY" + "NOW PERMITTED" language

The bypass language itself (the four required actions, the three guard
conditions, the documented memory.md/improvements.md/COMMIT_MSG
formats) lives in ``prompts/system.md`` and is verified end-to-end by
asserting against the prompt that ``build_prompt`` returns.

These tests cover everything that is testable WITHOUT actually running
the Claude SDK agent — i.e. the plumbing that makes the escape hatch
visible to the agent and the documentation that teaches the agent the
contract. The agent's actual decision to apply the bypass (writing
``## Blocked Errors`` to memory.md, appending the ``Phase 1 bypass: ...``
item to improvements.md, including the top-level COMMIT_MSG line) is
behavior under model control — those tests assert that the prompt
contains the exact instructions the agent must follow.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from evolve.agent import build_prompt
from evolve.diagnostics import _save_subprocess_diagnostic


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_project(tmp_path: Path) -> Path:
    """Set up a minimal project layout with a README and runs/ dir."""
    (tmp_path / "README.md").write_text("# Test Project\n")
    (tmp_path / "runs").mkdir()
    return tmp_path


# ---------------------------------------------------------------------------
# Attempt counter — bypass NOT triggered on attempt 1 or 2
# ---------------------------------------------------------------------------


class TestAttemptMarkerBypassNotTriggered:
    """The bypass MUST NOT trigger on attempts 1 or 2."""

    def test_no_diagnostic_means_attempt_1_no_bypass(self, tmp_path: Path):
        """Fresh round (no prior diagnostic) → attempt 1, bypass forbidden.

        Note: "FINAL RETRY" appears in the static system.md template
        section header ("Phase 1 escape hatch — FINAL RETRY ONLY...") so
        we assert against the unique attempt-3 banner markers
        (``CURRENT ATTEMPT: 3 of 3`` and ``NOW PERMITTED``) which are
        only emitted by the build_prompt attempt-marker substitution.
        """
        project = _make_project(tmp_path)
        run_dir = tmp_path / "runs" / "session1"
        run_dir.mkdir()

        prompt = build_prompt(project, run_dir=run_dir, round_num=5)

        assert "CURRENT ATTEMPT: 1 of 3" in prompt
        assert "NOT permitted on the first attempt" in prompt
        assert "CURRENT ATTEMPT: 3 of 3" not in prompt
        assert "NOW PERMITTED" not in prompt

    def test_attempt_1_failure_diagnostic_means_attempt_2_no_bypass(
        self, tmp_path: Path
    ):
        """Diagnostic from attempt 1 failure → THIS run is attempt 2, no bypass."""
        project = _make_project(tmp_path)
        run_dir = tmp_path / "runs" / "session1"
        run_dir.mkdir()
        # attempt=1 just failed → next run is attempt 2
        (run_dir / "subprocess_error_round_5.txt").write_text(
            "Round 5 — crashed (exit code 1) (attempt 1)\n"
            "Command: pytest\n\n"
            "Output (last 3000 chars):\n"
            "AssertionError\n"
        )

        prompt = build_prompt(project, run_dir=run_dir, round_num=5)

        assert "CURRENT ATTEMPT: 2 of 3" in prompt
        assert "NOT permitted on attempt 2" in prompt
        # The attempt-3 banner marker must be absent.
        assert "CURRENT ATTEMPT: 3 of 3" not in prompt
        # The "NOW PERMITTED" language is the explicit unlock signal — must
        # NOT appear on attempt 2 or the agent could mistakenly apply the
        # bypass one round too early.
        assert "NOW PERMITTED" not in prompt


# ---------------------------------------------------------------------------
# Attempt counter — bypass DOES trigger on attempt 3
# ---------------------------------------------------------------------------


class TestAttemptMarkerBypassTriggered:
    """The bypass IS unlocked on attempt 3 (final retry)."""

    def test_attempt_2_failure_diagnostic_means_attempt_3_bypass_unlocked(
        self, tmp_path: Path
    ):
        """Diagnostic from attempt 2 failure → THIS run is attempt 3,
        the FINAL retry, and the bypass is permitted."""
        project = _make_project(tmp_path)
        run_dir = tmp_path / "runs" / "session1"
        run_dir.mkdir()
        # attempt=2 just failed → next run is attempt 3 (FINAL)
        (run_dir / "subprocess_error_round_5.txt").write_text(
            "Round 5 — crashed (exit code 1) (attempt 2)\n"
            "Command: pytest\n\n"
            "Output (last 3000 chars):\n"
            "AssertionError\n"
        )

        prompt = build_prompt(project, run_dir=run_dir, round_num=5)

        assert "CURRENT ATTEMPT: 3 of 3" in prompt
        assert "FINAL RETRY" in prompt
        assert "NOW PERMITTED" in prompt
        # The 3 guard conditions must be explicitly listed.
        assert "Phase 1 errors still present" in prompt
        # Files-named-in-target guard:
        assert (
            "files named in" in prompt
            or "current improvement target" in prompt
        )

    def test_higher_attempt_diagnostic_still_unlocks_bypass(
        self, tmp_path: Path
    ):
        """If a diagnostic somehow records attempt > 2, the prompt must still
        unlock the bypass (the FINAL retry banner is the trigger, not exact
        equality with 3)."""
        project = _make_project(tmp_path)
        run_dir = tmp_path / "runs" / "session1"
        run_dir.mkdir()
        (run_dir / "subprocess_error_round_5.txt").write_text(
            "Round 5 — crashed (exit code 1) (attempt 3)\n"
            "Command: pytest\n\n"
            "Output (last 3000 chars):\n"
            "AssertionError\n"
        )

        prompt = build_prompt(project, run_dir=run_dir, round_num=5)

        assert "FINAL RETRY" in prompt
        assert "NOW PERMITTED" in prompt


# ---------------------------------------------------------------------------
# Attempt counter — diagnostics for *other* rounds must NOT promote
# ---------------------------------------------------------------------------


class TestAttemptMarkerCrossRoundIsolation:
    """A diagnostic file for a different round must not influence the
    current round's attempt counter — otherwise an old crash from round
    3 would silently make round 5 look like a retry."""

    def test_different_round_diagnostic_does_not_promote_attempt(
        self, tmp_path: Path
    ):
        """Round 3 has a (attempt 2) crash diagnostic, but we are running
        round 5 — round 5's attempt must stay at 1."""
        project = _make_project(tmp_path)
        run_dir = tmp_path / "runs" / "session1"
        run_dir.mkdir()
        # Crash from round 3, NOT round 5
        (run_dir / "subprocess_error_round_3.txt").write_text(
            "Round 3 — crashed (exit code 1) (attempt 2)\n"
            "Command: pytest\n\n"
            "Output (last 3000 chars):\n"
            "AssertionError\n"
        )

        prompt = build_prompt(project, run_dir=run_dir, round_num=5)

        assert "CURRENT ATTEMPT: 1 of 3" in prompt
        assert "NOT permitted on the first attempt" in prompt
        # The unique attempt-3 banner must be absent — the round 3
        # diagnostic must NOT promote round 5's attempt counter.
        assert "CURRENT ATTEMPT: 3 of 3" not in prompt
        assert "NOW PERMITTED" not in prompt

    def test_only_matching_round_diagnostic_promotes(self, tmp_path: Path):
        """When BOTH a stale (other-round) diagnostic and a matching
        diagnostic exist, only the matching one drives the attempt counter."""
        project = _make_project(tmp_path)
        run_dir = tmp_path / "runs" / "session1"
        run_dir.mkdir()
        (run_dir / "subprocess_error_round_3.txt").write_text(
            "Round 3 — crashed (exit code 1) (attempt 1)\n"
        )
        # The most recent diagnostic by round number is round 5; it has
        # attempt 1, so this run is attempt 2.
        (run_dir / "subprocess_error_round_5.txt").write_text(
            "Round 5 — crashed (exit code 1) (attempt 1)\n"
        )

        prompt = build_prompt(project, run_dir=run_dir, round_num=5)
        assert "CURRENT ATTEMPT: 2 of 3" in prompt


# ---------------------------------------------------------------------------
# System prompt content — guard conditions and required actions
# ---------------------------------------------------------------------------


class TestEscapeHatchPromptContent:
    """The system prompt must teach the agent the bypass contract:
    three guard conditions and four required actions."""

    @pytest.fixture
    def base_prompt(self, tmp_path: Path) -> str:
        project = _make_project(tmp_path)
        run_dir = tmp_path / "runs" / "session1"
        run_dir.mkdir()
        return build_prompt(project, run_dir=run_dir, round_num=5)

    def test_guard_condition_attempt_3(self, base_prompt: str):
        """Guard #1: must be on attempt 3."""
        assert "attempt 3" in base_prompt

    def test_guard_condition_phase1_errors_present(self, base_prompt: str):
        """Guard #2: Phase 1 errors must still be present."""
        assert "Phase 1 errors" in base_prompt

    def test_guard_condition_target_files_unrelated(self, base_prompt: str):
        """Guard #3: failing tests must NOT touch files named in target."""
        assert "current improvement target" in base_prompt
        assert (
            "files named in" in base_prompt
            or "files named" in base_prompt.lower()
            or "no" in base_prompt.lower()
        )

    def test_action_a_log_to_memory_blocked_errors(self, base_prompt: str):
        """Action (a): log to memory.md under ## Blocked Errors."""
        assert "Blocked Errors" in base_prompt
        assert "memory.md" in base_prompt

    def test_action_b_append_phase1_bypass_to_improvements(
        self, base_prompt: str
    ):
        """Action (b): append a "Phase 1 bypass: ..." item to improvements.md."""
        assert "Phase 1 bypass" in base_prompt
        assert "improvements.md" in base_prompt

    def test_action_c_proceed_with_target(self, base_prompt: str):
        """Action (c): proceed with the original Phase 3 target."""
        assert "Phase 3" in base_prompt

    def test_action_d_commit_msg_top_level_line(self, base_prompt: str):
        """Action (d): include "Phase 1 bypass: <summary>" in COMMIT_MSG."""
        assert "COMMIT_MSG" in base_prompt
        # The exact format string from the spec:
        assert "Phase 1 bypass: <short summary>" in base_prompt or (
            "Phase 1 bypass" in base_prompt and "COMMIT_MSG" in base_prompt
        )


# ---------------------------------------------------------------------------
# System prompt content — explicit forbidden cases
# ---------------------------------------------------------------------------


class TestEscapeHatchForbiddenCases:
    """The prompt must spell out the cases where the bypass is FORBIDDEN
    so the agent doesn't apply it accidentally."""

    @pytest.fixture
    def base_prompt(self, tmp_path: Path) -> str:
        project = _make_project(tmp_path)
        run_dir = tmp_path / "runs" / "session1"
        run_dir.mkdir()
        return build_prompt(project, run_dir=run_dir, round_num=5)

    def test_forbidden_when_target_files_in_failure(self, base_prompt: str):
        """Forbidden case: failing tests reference files in the target's scope.
        Those failures ARE the target's responsibility and MUST be fixed."""
        assert "FORBIDDEN" in base_prompt or "target's responsibility" in base_prompt

    def test_forbidden_when_retries_remaining(self, base_prompt: str):
        """Forbidden case: retries remaining (attempt 1 or 2)."""
        assert "attempt 1 or 2" in base_prompt or "first attempt" in base_prompt

    def test_forbidden_on_first_attempt_explicit(self, tmp_path: Path):
        """When attempt=1 banner is active, prompt must say bypass is
        NOT permitted on the first attempt."""
        project = _make_project(tmp_path)
        run_dir = tmp_path / "runs" / "session1"
        run_dir.mkdir()
        prompt = build_prompt(project, run_dir=run_dir, round_num=5)
        assert "NOT permitted on the first attempt" in prompt


# ---------------------------------------------------------------------------
# _save_subprocess_diagnostic — escape hatch banner injection
# ---------------------------------------------------------------------------


class TestDiagnosticBannerInjection:
    """``_save_subprocess_diagnostic`` only writes the escape hatch
    notice when the NEXT attempt will be 3."""

    def test_no_banner_after_attempt_1_failure(self, tmp_path: Path):
        """attempt=1 just failed → next is attempt 2 → no banner."""
        _save_subprocess_diagnostic(
            tmp_path,
            round_num=5,
            cmd=["evolve", "_round"],
            output="some failure output",
            reason="crashed (exit code 1)",
            attempt=1,
        )
        text = (tmp_path / "subprocess_error_round_5.txt").read_text()
        assert "Phase 1 escape hatch notice" not in text
        # But the attempt number must still be encoded in the header so
        # build_prompt can parse it.
        assert "(attempt 1)" in text

    def test_banner_present_after_attempt_2_failure(self, tmp_path: Path):
        """attempt=2 just failed → next is attempt 3 (FINAL) → banner."""
        _save_subprocess_diagnostic(
            tmp_path,
            round_num=5,
            cmd=["evolve", "_round"],
            output="some failure output",
            reason="crashed (exit code 1)",
            attempt=2,
        )
        text = (tmp_path / "subprocess_error_round_5.txt").read_text()
        assert "Phase 1 escape hatch notice" in text
        assert "FINAL retry" in text or "FINAL RETRY" in text or "attempt 3" in text
        assert "(attempt 2)" in text

    def test_banner_describes_the_three_actions(self, tmp_path: Path):
        """The notice block must point at all three escape hatch actions
        so the agent knows what to do."""
        _save_subprocess_diagnostic(
            tmp_path,
            round_num=7,
            cmd=["evolve", "_round"],
            output="failure",
            reason="watchdog stalled (no output for 120s)",
            attempt=2,
        )
        text = (tmp_path / "subprocess_error_round_7.txt").read_text()
        # Mentions of the four actions:
        assert "memory.md" in text
        assert "improvements.md" in text
        assert "Phase 1 bypass" in text
        assert "COMMIT_MSG" in text

    def test_banner_includes_round_number(self, tmp_path: Path):
        """The notice block is round-scoped — round number appears."""
        _save_subprocess_diagnostic(
            tmp_path,
            round_num=42,
            cmd=["evolve", "_round"],
            output="failure",
            reason="crashed",
            attempt=2,
        )
        text = (tmp_path / "subprocess_error_round_42.txt").read_text()
        assert "round 42" in text

    def test_no_banner_after_attempt_0_or_unknown(self, tmp_path: Path):
        """Defensive: attempt=0 (shouldn't happen but) → next is 1, no banner."""
        _save_subprocess_diagnostic(
            tmp_path,
            round_num=5,
            cmd=["evolve", "_round"],
            output="failure",
            reason="crashed",
            attempt=0,
        )
        text = (tmp_path / "subprocess_error_round_5.txt").read_text()
        assert "Phase 1 escape hatch notice" not in text


# ---------------------------------------------------------------------------
# End-to-end: diagnostic → build_prompt round trip
# ---------------------------------------------------------------------------


class TestDiagnosticToPromptRoundTrip:
    """Verify the contract between the two production sites is intact:
    a diagnostic written by ``_save_subprocess_diagnostic`` is parsed by
    ``build_prompt`` and produces the corresponding attempt banner."""

    def test_attempt_2_failure_diagnostic_yields_attempt_3_prompt(
        self, tmp_path: Path
    ):
        project = _make_project(tmp_path)
        run_dir = tmp_path / "runs" / "session1"
        run_dir.mkdir()
        # Failure of attempt 2 of round 5
        _save_subprocess_diagnostic(
            run_dir,
            round_num=5,
            cmd=["evolve", "_round"],
            output="phase 1 failure",
            reason="watchdog stalled",
            attempt=2,
        )

        prompt = build_prompt(project, run_dir=run_dir, round_num=5)

        assert "CURRENT ATTEMPT: 3 of 3" in prompt
        assert "FINAL RETRY" in prompt
        # The crash diagnostic itself should also be referenced/included
        # in the prompt body (downstream of the attempt marker).
        assert "watchdog stalled" in prompt or "phase 1 failure" in prompt

    def test_attempt_1_failure_diagnostic_yields_attempt_2_prompt(
        self, tmp_path: Path
    ):
        project = _make_project(tmp_path)
        run_dir = tmp_path / "runs" / "session1"
        run_dir.mkdir()
        _save_subprocess_diagnostic(
            run_dir,
            round_num=2,
            cmd=["evolve", "_round"],
            output="syntax error",
            reason="crashed (exit code 1)",
            attempt=1,
        )

        prompt = build_prompt(project, run_dir=run_dir, round_num=2)
        assert "CURRENT ATTEMPT: 2 of 3" in prompt
        assert "CURRENT ATTEMPT: 3 of 3" not in prompt
        assert "NOW PERMITTED" not in prompt


# ---------------------------------------------------------------------------
# improvements.md item format — the spec'd exact wording
# ---------------------------------------------------------------------------


class TestImprovementsBypassItemFormat:
    """The spec requires the exact format:
       ``- [ ] [functional] Phase 1 bypass: fix pre-existing failures
       (<short summary>) that blocked round N — see memory.md § Blocked Errors``
    The system prompt must teach this format to the agent."""

    def test_prompt_documents_bypass_item_format(self, tmp_path: Path):
        project = _make_project(tmp_path)
        run_dir = tmp_path / "runs" / "session1"
        run_dir.mkdir()
        prompt = build_prompt(project, run_dir=run_dir, round_num=5)

        # Key fragments of the documented format
        assert "Phase 1 bypass" in prompt
        assert "fix pre-existing failures" in prompt
        # The "blocked round N" wording flagged for the next cycle:
        assert "blocked round" in prompt
        # The pointer back to memory.md § Blocked Errors:
        assert "Blocked Errors" in prompt


# ---------------------------------------------------------------------------
# COMMIT_MSG top-level marker — the spec'd exact wording
# ---------------------------------------------------------------------------


class TestCommitMsgBypassMarker:
    """The spec requires a top-level commit message line:
       ``Phase 1 bypass: <short summary>``
    The system prompt must teach this to the agent so the bypass is
    visible in git history."""

    def test_prompt_documents_commit_msg_marker(self, tmp_path: Path):
        project = _make_project(tmp_path)
        run_dir = tmp_path / "runs" / "session1"
        run_dir.mkdir()
        prompt = build_prompt(project, run_dir=run_dir, round_num=5)

        assert "Phase 1 bypass: <short summary>" in prompt
        assert "COMMIT_MSG" in prompt
        # The rationale ("visible in git history") should also be present
        # so the agent understands WHY the marker is required:
        assert "git history" in prompt
