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

from evolve.infrastructure.claude_sdk.draft_review import _build_draft_prompt
from evolve.infrastructure.claude_sdk.prompt_builder import build_prompt
from evolve.application.run_loop import (
    _BACKLOG_VIOLATION_HEADER,
    _BACKLOG_VIOLATION_PREFIX,
)
from evolve.infrastructure.filesystem.improvement_parser import (
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


class TestRule2AntiVariante:
    """Rule 2 is agent-enforced via prompts/system.md.  Lock the documented
    rule text (anti-variante / merge-into-existing) into both the source
    template and the rendered build_prompt output, so any future rewrite
    that silently drops the rule breaks this test.

    See SPEC.md § "Backlog discipline" Rule 2.
    """

    PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "draft.md"

    @staticmethod
    def _collapse_ws(text: str) -> str:
        # Collapse all runs of whitespace (incl. newlines) into single spaces
        # so wrap-insensitive substring checks are possible.
        import re as _re
        return _re.sub(r"\s+", " ", text)

    def test_system_md_contains_rule_2_text(self) -> None:
        text = self.PROMPT_PATH.read_text()
        assert "Rule 2" in text
        assert "Anti-variante" in text
        # The actionable part: "extend the existing item's description"
        # instead of duplicating.  The phrase wraps across a line break in
        # the source, so normalise whitespace before asserting.
        flat = self._collapse_ws(text)
        assert "extend the existing item" in flat
        # The scan-pending-items directive must explicitly cover both pending
        # lists (checked AND unchecked) so the rule catches completed precedents.
        assert "checked AND unchecked" in flat

    def test_rule_2_is_rendered_into_build_prompt(self, tmp_path: Path) -> None:
        project_dir, run_dir = _make_project(tmp_path)
        prompt = _build_draft_prompt(project_dir, run_dir, spec="README.md")
        assert "Rule 2" in prompt
        assert "Anti-variante" in prompt
        assert "extend the existing item" in self._collapse_ws(prompt)


class TestRule3PriorityAwareInsertion:
    """Rule 3: new item gets a [P1]/[P2]/[P3] tag and is inserted at
    TOP / middle / BOTTOM accordingly.  See SPEC.md § "Backlog discipline"
    Rule 3.
    """

    PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "draft.md"

    def test_system_md_documents_all_three_priorities(self) -> None:
        text = self.PROMPT_PATH.read_text()
        assert "Rule 3" in text
        assert "Priority-aware" in text
        # All three tags must be documented at the rule's expected positions.
        assert "[P1]" in text and "TOP" in text
        assert "[P2]" in text and "middle" in text
        assert "[P3]" in text and "BOTTOM" in text
        # Default must be [P2] for untagged items (SPEC.md documents this).
        assert "default" in text and "[P2]" in text

    def test_rule_3_is_rendered_into_build_prompt(self, tmp_path: Path) -> None:
        project_dir, run_dir = _make_project(tmp_path)
        prompt = _build_draft_prompt(project_dir, run_dir, spec="README.md")
        assert "Rule 3" in prompt
        assert "Priority-aware" in prompt
        assert "[P1]" in prompt
        assert "[P2]" in prompt
        assert "[P3]" in prompt
        assert "TOP" in prompt
        assert "BOTTOM" in prompt

    def test_improvements_md_current_pending_ordering_is_consistent(self) -> None:
        """Cross-check: the real improvements.md (this repo) follows rule 3
        insertion positions for the few explicitly priority-tagged pending
        items.  [P1] pending items (if any) precede [P2] / [P3] within the
        pending slice of the file.  Purely observational — if the repo's
        improvements.md is ever hand-edited to add an out-of-order item,
        this test gently flags it.
        """
        improvements_path = (
            Path(__file__).resolve().parent.parent / "runs" / "improvements.md"
        )
        if not improvements_path.is_file():
            pytest.skip("runs/improvements.md not present in this checkout")
        pending_lines = [
            ln
            for ln in improvements_path.read_text().splitlines()
            if ln.lstrip().startswith("- [ ]")
        ]
        # Map each pending line to its priority rank (P1=0, P2=1, P3=2,
        # untagged defaults to P2 per SPEC).
        def rank(line: str) -> int:
            if "[P1]" in line:
                return 0
            if "[P3]" in line:
                return 2
            return 1  # [P2] or untagged

        ranks = [rank(ln) for ln in pending_lines]
        # Any P1 items must precede every P3 item.  (P2 items may interleave
        # with P3 per SPEC — middle-vs-bottom is not strict given untagged
        # defaults.)
        first_p3 = next((i for i, r in enumerate(ranks) if r == 2), None)
        last_p1 = next(
            (i for i in range(len(ranks) - 1, -1, -1) if ranks[i] == 0), None
        )
        if first_p3 is not None and last_p1 is not None:
            assert last_p1 < first_p3, (
                "P1 items must precede P3 items in improvements.md "
                f"(last P1 at index {last_p1}, first P3 at index {first_p3})"
            )


class TestRule4AntiStutter:
    """Rule 4: if the last 3 rounds each added a [P3] item, the current
    round MAY NOT add a 4th consecutive [P3] item.  Agent-enforced — the
    system prompt instructs the agent to read the last 3
    conversation_loop_*.md files.  See SPEC.md § "Backlog discipline"
    Rule 4.

    The target requires fixture conversation logs for rule 4 input.  The
    fixtures below populate a temp run_dir with three prior
    conversation_loop logs that each contain a tell-tale [P3] addition,
    and verify build_prompt still forwards the rule text (so the agent
    has both the rule and the evidence to apply it).
    """

    PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "draft.md"

    def test_system_md_contains_rule_4_text(self) -> None:
        text = self.PROMPT_PATH.read_text()
        assert "Rule 4" in text
        assert "Anti-stutter" in text
        # The trigger: last 3 conversation logs each added a [P3] item.
        assert "last 3" in text or "last three" in text
        assert "[P3]" in text
        # The instruction must tell the agent HOW to detect — reading the
        # conversation_loop_*.md files.
        assert "conversation_loop_" in text

    def test_rule_4_is_rendered_into_build_prompt(self, tmp_path: Path) -> None:
        project_dir, run_dir = _make_project(tmp_path)
        prompt = _build_draft_prompt(project_dir, run_dir, spec="README.md")
        assert "Rule 4" in prompt
        assert "Anti-stutter" in prompt

    def test_build_prompt_references_prior_conversation_logs(
        self, tmp_path: Path
    ) -> None:
        """Fixture conversation logs populate run_dir; build_prompt renders
        round-numbered log references that the agent can then load per
        Rule 4.  This verifies the prompt's {prev_round_1} / {prev_round_2}
        substitutions in the self-monitoring section resolve correctly.
        """
        project_dir, run_dir = _make_project(tmp_path)
        # Create three prior conversation_loop logs, each with a P3 addition
        # marker as the fixture input rule 4 expects.
        for k in (1, 2, 3):
            (run_dir / f"conversation_loop_{k}.md").write_text(
                textwrap.dedent(
                    f"""\
                    # Round {k} conversation

                    ... work ...

                    Edit improvements.md: add
                    - [ ] [P3] trivial refactor {k}
                    """
                )
            )
        prompt = build_prompt(project_dir, run_dir=run_dir, round_num=4)
        # The prompt's stuck-loop / self-monitoring section references the
        # two most recent prior rounds by number via substitutions — verify
        # those resolved to concrete integers, not placeholders.
        assert "{prev_round_1}" not in prompt
        assert "{prev_round_2}" not in prompt
        # Sanity check: substitutions landed at the documented values.
        assert "conversation_loop_3.md" in prompt  # round_num - 1
        assert "conversation_loop_2.md" in prompt  # round_num - 2

    def test_fourth_consecutive_p3_fixture_is_detectable(
        self, tmp_path: Path
    ) -> None:
        """Drive-by verification that the rule's trigger signature is
        observable by a plain grep of the fixture logs — this is the
        exact check the agent performs per the prompt's instruction.
        """
        project_dir, run_dir = _make_project(tmp_path)
        for k in (13, 14, 15):
            (run_dir / f"conversation_loop_{k}.md").write_text(
                f"Round {k}\n- [ ] [P3] cosmetic {k}\n"
            )
        # Count P3 additions across the last 3 logs — matches what
        # Rule 4's agent-side check performs.
        p3_rounds = [
            k
            for k in (13, 14, 15)
            if "[P3]" in (run_dir / f"conversation_loop_{k}.md").read_text()
        ]
        assert len(p3_rounds) == 3, (
            "Rule 4 fixture should show 3 consecutive [P3] additions; "
            "a 4th would be blocked by the anti-stutter rule."
        )


class TestAllFourRulesCoPresentInPrompt:
    """Top-level sanity check: one test, one grep — all four rule
    headings are present in the system prompt so none can be silently
    deleted in a refactor.  Complements the per-rule tests above.
    """

    def test_prompt_has_all_four_rule_headings(self, tmp_path: Path) -> None:
        project_dir, run_dir = _make_project(tmp_path)
        prompt = _build_draft_prompt(project_dir, run_dir, spec="README.md")
        for label in (
            "Rule 1",
            "Rule 2",
            "Rule 3",
            "Rule 4",
            "Empty-queue gate",
            "Anti-variante",
            "Priority-aware",
            "Anti-stutter",
        ):
            assert label in prompt, f"missing rule marker: {label!r}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
