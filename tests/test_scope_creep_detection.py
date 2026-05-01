"""Tests for the rebuild-and-implement scope-creep detection.

A round that both (a) adds new ``[ ]`` items to improvements.md AND
(b) modifies non-improvements files in the same commit is violating
the one-round-one-kind rule: Phase 2 rebuild and Phase 3
implementation are separate round kinds.  The orchestrator flags
the mix as ``SCOPE CREEP`` and the retry prompt instructs the
agent to split the work.
"""

from __future__ import annotations

from pathlib import Path

from evolve.infrastructure.claude_sdk.prompt_builder import build_prompt


class TestScopeCreepPromptSection:
    """``build_prompt`` renders a dedicated 'split the work' section
    when the orchestrator emits a ``SCOPE CREEP`` diagnostic."""

    def test_scope_creep_section_rendered(self, tmp_path: Path):
        run_dir = tmp_path / "runs" / "session"
        run_dir.mkdir(parents=True)
        (tmp_path / "README.md").write_text("# Test\n")

        diag = run_dir / "subprocess_error_round_5.txt"
        diag.write_text(
            "Round 5 — SCOPE CREEP (attempt 1)\n"
            "Reason: SCOPE CREEP: Phase 2 rebuild mixed with "
            "implementation: 2 new ``[ ]`` item(s) added AND "
            "non-improvements files touched (evolve/agent.py, "
            "tests/test_foo.py)."
        )

        prompt = build_prompt(
            tmp_path,
            check_output="",
            check_cmd=None,
            allow_installs=False,
            run_dir=run_dir,
            round_num=5,
        )

        assert "## CRITICAL — Scope creep" in prompt
        assert "rebuild mixed with implementation" in prompt
        # Concrete remediation steps are present.
        assert "git reset" in prompt
        assert "Stage ONLY" in prompt
        assert "split the work" in prompt
        # The punitive / wrong-problem wording should NOT leak in.
        assert "Start with Edit/Write immediately" not in prompt

    def test_non_scope_creep_diagnostic_uses_other_path(self, tmp_path: Path):
        """A non-scope-creep diagnostic (e.g. generic CRASHED) does
        NOT trigger the scope-creep section — just a sanity check
        that the prefix match is on the full ``SCOPE CREEP`` token.
        """
        run_dir = tmp_path / "runs" / "session"
        run_dir.mkdir(parents=True)
        (tmp_path / "README.md").write_text("# Test\n")

        diag = run_dir / "subprocess_error_round_5.txt"
        # A generic crash — no SCOPE CREEP token.
        diag.write_text(
            "Round 5 — crashed (attempt 1)\n"
            "Output: Traceback (most recent call last)...\n"
        )

        prompt = build_prompt(
            tmp_path,
            check_output="",
            check_cmd=None,
            allow_installs=False,
            run_dir=run_dir,
            round_num=5,
        )

        assert "## CRITICAL — Scope creep" not in prompt
        # The generic "Previous round CRASHED" section fires instead.
        assert "Previous round CRASHED" in prompt
