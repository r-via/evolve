"""Tests for backlog discipline rule 1 (empty-queue gate) enforcement.

Covers the orchestrator-side helpers (_extract_unchecked_lines,
_detect_backlog_violation) and the agent.py prompt-builder branch that
renders the dedicated diagnostic header when the previous attempt's
diagnostic carries the BACKLOG VIOLATION prefix.

Reference: SPEC.md § "Backlog discipline" rule 1, "Empty-queue gate (HARD)".
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from agent import build_prompt
from loop import (
    _BACKLOG_VIOLATION_HEADER,
    _BACKLOG_VIOLATION_PREFIX,
    _detect_backlog_violation,
    _extract_unchecked_lines,
)


def _make_project(tmp_path: Path, prev_crash: str | None = None,
                   round_num: int = 5) -> tuple[Path, Path]:
    """Create a minimal project + run dir with optional prev-crash diagnostic."""
    (tmp_path / "README.md").write_text("# spec\nclaim one")
    runs = tmp_path / "runs"
    runs.mkdir()
    (runs / "improvements.md").write_text("- [ ] A\n- [ ] B\n")
    run_dir = runs / "20260423_999999"
    run_dir.mkdir()
    if prev_crash is not None:
        (run_dir / f"subprocess_error_round_{round_num}.txt").write_text(
            f"Round {round_num} — {prev_crash} (attempt 1)\n"
            f"Command: dummy\n\nOutput (last 3000 chars):\n{prev_crash}\n"
        )
    return tmp_path, run_dir


class TestExtractUncheckedLines:
    def test_empty_text(self) -> None:
        assert _extract_unchecked_lines("") == []

    def test_only_checked_items(self) -> None:
        text = "- [x] done one\n- [x] done two\n"
        assert _extract_unchecked_lines(text) == []

    def test_mixed(self) -> None:
        text = (
            "# Improvements\n\n"
            "- [x] done\n"
            "- [ ] [functional] pending one\n"
            "- [ ] [performance] pending two\n"
        )
        out = _extract_unchecked_lines(text)
        assert out == [
            "- [ ] [functional] pending one",
            "- [ ] [performance] pending two",
        ]

    def test_indented_lines_are_normalised(self) -> None:
        # Leading whitespace is stripped so equality comparisons are stable.
        text = "  - [ ] indented item\n"
        assert _extract_unchecked_lines(text) == ["- [ ] indented item"]


class TestDetectBacklogViolation:
    def test_no_change_no_violation(self) -> None:
        text = "- [ ] one\n- [ ] two\n"
        violated, new = _detect_backlog_violation(text, text)
        assert violated is False
        assert new == []

    def test_check_off_only_no_violation(self) -> None:
        pre = "- [ ] one\n- [ ] two\n"
        post = "- [x] one\n- [ ] two\n"
        violated, new = _detect_backlog_violation(pre, post)
        assert violated is False
        assert new == []

    def test_add_to_empty_queue_no_violation(self) -> None:
        # Pre had no unchecked items, post has one — legitimate add.
        pre = "- [x] done\n"
        post = "- [x] done\n- [ ] new item\n"
        violated, new = _detect_backlog_violation(pre, post)
        assert violated is False
        assert new == []

    def test_check_off_one_and_add_to_empty_queue_no_violation(self) -> None:
        # Pre: one pending (A). Post: A checked off, new item added.
        # Queue was non-empty pre but is empty (apart from the new item)
        # post — this matches SPEC's "queue was empty when add happened"
        # interpretation: post has only the new item, no other [ ] line.
        pre = "- [ ] A\n"
        post = "- [x] A\n- [ ] B\n"
        violated, new = _detect_backlog_violation(pre, post)
        assert violated is False
        assert new == []

    def test_add_while_queue_non_empty_violation(self) -> None:
        pre = "- [ ] A\n- [ ] B\n"
        post = "- [ ] A\n- [ ] B\n- [ ] C new\n"
        violated, new = _detect_backlog_violation(pre, post)
        assert violated is True
        assert new == ["- [ ] C new"]

    def test_check_off_one_and_add_one_with_others_pending_is_violation(
        self,
    ) -> None:
        # Pre: A, B pending. Post: A checked off, B still pending, C added.
        # Queue was non-empty (B) when C was added → violation.
        pre = "- [ ] A\n- [ ] B\n"
        post = "- [x] A\n- [ ] B\n- [ ] C new\n"
        violated, new = _detect_backlog_violation(pre, post)
        assert violated is True
        assert new == ["- [ ] C new"]

    def test_multiple_new_items_violation(self) -> None:
        pre = "- [ ] A\n"
        post = "- [ ] A\n- [ ] B\n- [ ] C\n"
        violated, new = _detect_backlog_violation(pre, post)
        assert violated is True
        assert set(new) == {"- [ ] B", "- [ ] C"}

    def test_constants_match_spec_documented_strings(self) -> None:
        # The spec documents the exact diagnostic header text.  Lock both
        # constants down so any future drift fails this test.
        assert _BACKLOG_VIOLATION_PREFIX == "BACKLOG VIOLATION"
        assert (
            "Backlog discipline violation: new item added while queue non-empty"
            in _BACKLOG_VIOLATION_HEADER
        )
        assert _BACKLOG_VIOLATION_HEADER.startswith("CRITICAL")


class TestAgentPromptBacklogViolationBranch:
    """Verify build_prompt renders the dedicated header for BACKLOG VIOLATION."""

    def test_backlog_violation_renders_dedicated_header(
        self, tmp_path: Path
    ) -> None:
        crash_diag = (
            "BACKLOG VIOLATION: backlog discipline rule 1 violated: "
            "1 new `- [ ]` item(s) added while queue non-empty"
        )
        project_dir, run_dir = _make_project(
            tmp_path, prev_crash=crash_diag, round_num=5
        )
        prompt = build_prompt(project_dir, run_dir=run_dir, round_num=5)
        assert (
            "## CRITICAL — Backlog discipline violation: "
            "new item added while queue non-empty"
        ) in prompt
        # The dedicated branch must NOT also fall through to the generic
        # CRASHED branch — only one CRITICAL header for the prev_crash.
        assert "Previous round CRASHED" not in prompt

    def test_no_progress_path_unaffected(self, tmp_path: Path) -> None:
        # Sanity check: the existing NO PROGRESS branch still renders
        # correctly after the BACKLOG VIOLATION branch was inserted before
        # it in the if/elif chain.
        crash_diag = "NO PROGRESS: improvements.md byte-identical"
        project_dir, run_dir = _make_project(
            tmp_path, prev_crash=crash_diag, round_num=2
        )
        prompt = build_prompt(project_dir, run_dir=run_dir, round_num=2)
        assert "Previous round made NO PROGRESS" in prompt
        # The dedicated BACKLOG VIOLATION header must NOT render here —
        # match against the unique prev-crash-section heading text.
        assert (
            "## CRITICAL — Backlog discipline violation: "
            "new item added while queue non-empty"
        ) not in prompt

    def test_memory_wiped_branch_still_takes_priority(
        self, tmp_path: Path
    ) -> None:
        crash_diag = "MEMORY WIPED: memory.md shrunk by >50%"
        project_dir, run_dir = _make_project(
            tmp_path, prev_crash=crash_diag, round_num=2
        )
        prompt = build_prompt(project_dir, run_dir=run_dir, round_num=2)
        assert "silently wiped memory.md" in prompt
        assert (
            "## CRITICAL — Backlog discipline violation: "
            "new item added while queue non-empty"
        ) not in prompt


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
