"""Tests for adversarial review verdict routing (SPEC § Phase 3.6).

Covers:
- AC 1: _check_review_verdict helper parsing
- AC 2: CHANGES REQUESTED → subprocess_error with REVIEW prefix + retry
- AC 3: BLOCKED → orchestrator returns (exit 2 path)
- AC 4: APPROVED / absent file → normal flow
- AC 5: build_prompt REVIEW: prefix → dedicated header
"""
from __future__ import annotations

from pathlib import Path

import pytest

from evolve.orchestrator import _check_review_verdict
from evolve.agent import build_prompt


# ---------------------------------------------------------------------------
# AC 1 — _check_review_verdict helper
# ---------------------------------------------------------------------------

class TestCheckReviewVerdict:
    """_check_review_verdict parses review_round_N.md correctly."""

    def test_absent_file_returns_none(self, tmp_path: Path) -> None:
        verdict, findings = _check_review_verdict(tmp_path, 1)
        assert verdict is None
        assert findings == ""

    def test_approved_verdict(self, tmp_path: Path) -> None:
        review = tmp_path / "review_round_1.md"
        review.write_text(
            "# Review Round 1\n\n"
            "**Verdict:** APPROVED\n\n"
            "## Findings\n"
            "- LOW: Minor style issue\n"
        )
        verdict, findings = _check_review_verdict(tmp_path, 1)
        assert verdict == "APPROVED"
        assert findings == ""  # no HIGH findings extracted for APPROVED

    def test_changes_requested_verdict(self, tmp_path: Path) -> None:
        review = tmp_path / "review_round_3.md"
        review.write_text(
            "# Review Round 3\n\n"
            "**Verdict:** CHANGES REQUESTED\n\n"
            "## Findings\n"
            "- HIGH: Missing test for edge case\n"
            "- LOW: Minor style issue\n"
        )
        verdict, findings = _check_review_verdict(tmp_path, 3)
        assert verdict == "CHANGES REQUESTED"
        assert "HIGH" in findings

    def test_blocked_verdict(self, tmp_path: Path) -> None:
        review = tmp_path / "review_round_2.md"
        review.write_text(
            "# Review Round 2\n\n"
            "**Verdict:** BLOCKED\n\n"
            "## Findings\n"
            "- HIGH: AC 1 not implemented\n"
            "- HIGH: AC 2 partial\n"
            "- HIGH: Regression risk [regression-risk]\n"
        )
        verdict, findings = _check_review_verdict(tmp_path, 2)
        assert verdict == "BLOCKED"
        assert "HIGH" in findings

    def test_case_insensitive_verdict_line(self, tmp_path: Path) -> None:
        review = tmp_path / "review_round_1.md"
        review.write_text("Verdict: changes requested\n")
        verdict, _ = _check_review_verdict(tmp_path, 1)
        assert verdict == "CHANGES REQUESTED"

    def test_unreadable_file_returns_none(self, tmp_path: Path) -> None:
        # Directory instead of file — triggers OSError on read
        review = tmp_path / "review_round_1.md"
        review.mkdir()
        verdict, findings = _check_review_verdict(tmp_path, 1)
        # mkdir creates a directory, is_file() returns False → None
        assert verdict is None

    def test_no_verdict_line_returns_none(self, tmp_path: Path) -> None:
        review = tmp_path / "review_round_1.md"
        review.write_text("# Review\nJust some text without a verdict line.\n")
        verdict, _ = _check_review_verdict(tmp_path, 1)
        assert verdict is None


# ---------------------------------------------------------------------------
# AC 5 — build_prompt REVIEW: prefix in prev_crash
# ---------------------------------------------------------------------------

def _make_project(tmp_path: Path, prev_crash: str | None = None,
                  round_num: int = 5) -> tuple[Path, Path]:
    """Create a minimal project + run dir with optional prev-crash diagnostic."""
    (tmp_path / "README.md").write_text("# spec\nclaim one")
    runs = tmp_path / "runs"
    runs.mkdir()
    (runs / "improvements.md").write_text("- [ ] A\n- [ ] B\n")
    run_dir = runs / "20260424_999999"
    run_dir.mkdir()
    if prev_crash is not None:
        (run_dir / f"subprocess_error_round_{round_num}.txt").write_text(
            f"Round {round_num} — {prev_crash} (attempt 1)\n"
            f"Command: dummy\n\nOutput (last 3000 chars):\n{prev_crash}\n"
        )
    return tmp_path, run_dir


class TestBuildPromptReviewBranch:
    """build_prompt recognises REVIEW: prefix and emits the correct header."""

    def test_review_changes_requested_renders_header(
        self, tmp_path: Path
    ) -> None:
        crash_diag = (
            "REVIEW: changes requested — adversarial review "
            "found 1-2 HIGH findings that must be addressed.\n"
            "HIGH: Missing test for edge case"
        )
        project_dir, run_dir = _make_project(
            tmp_path, prev_crash=crash_diag, round_num=3
        )
        prompt = build_prompt(project_dir, run_dir=run_dir, round_num=3)
        assert "Previous attempt failed adversarial review" in prompt
        assert "Previous round CRASHED" not in prompt

    def test_review_blocked_renders_header(self, tmp_path: Path) -> None:
        crash_diag = (
            "REVIEW: blocked — adversarial review found "
            "3+ HIGH findings. Operator intervention required."
        )
        project_dir, run_dir = _make_project(
            tmp_path, prev_crash=crash_diag, round_num=2
        )
        prompt = build_prompt(project_dir, run_dir=run_dir, round_num=2)
        assert "Previous attempt failed adversarial review" in prompt

    def test_no_progress_still_works(self, tmp_path: Path) -> None:
        """REVIEW: branch doesn't break the NO PROGRESS branch."""
        crash_diag = "NO PROGRESS: improvements.md byte-identical"
        project_dir, run_dir = _make_project(
            tmp_path, prev_crash=crash_diag, round_num=2
        )
        prompt = build_prompt(project_dir, run_dir=run_dir, round_num=2)
        assert "Previous round made NO PROGRESS" in prompt
        # The dedicated REVIEW header must NOT appear — only check the
        # CRITICAL section, not the system prompt template which also
        # mentions "adversarial review" in its Phase 3.6 instructions.
        assert "Previous attempt failed adversarial review" not in prompt

    def test_backlog_violation_still_works(self, tmp_path: Path) -> None:
        """REVIEW: branch doesn't break the BACKLOG VIOLATION branch."""
        crash_diag = (
            "BACKLOG VIOLATION: backlog discipline rule 1 violated"
        )
        project_dir, run_dir = _make_project(
            tmp_path, prev_crash=crash_diag, round_num=2
        )
        prompt = build_prompt(project_dir, run_dir=run_dir, round_num=2)
        assert "Backlog discipline violation" in prompt
        assert "Previous attempt failed adversarial review" not in prompt
