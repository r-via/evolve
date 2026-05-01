"""Tests for the "backlog drained, CONVERGED skipped" retry path.

When a round ends with every ``[ ]`` item checked off but the agent
did not write ``CONVERGED``, the previous zero-progress heuristic
fired the retry with a generic "NO PROGRESS" diagnostic — which is
both wrong (the round had nothing to implement) and counterproductive
(the agent would fabricate filler work on retry to satisfy "make
progress").  The orchestrator now detects the drained-but-not-
converged state and emits a dedicated ``BACKLOG DRAINED`` diagnostic
that steers the retry toward Phase 4 (write CONVERGED after verifying
README claims) instead of hunting for something to edit.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from evolve.infrastructure.claude_sdk.prompt_builder import build_prompt
from evolve.application.run_loop import _run_rounds


def _setup_project_with_all_checked(tmp_path: Path) -> tuple[Path, Path, Path]:
    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    run_dir = project_dir / "runs" / "session"
    run_dir.mkdir(parents=True)
    imp_path = project_dir / "runs" / "improvements.md"
    imp_path.write_text("- [x] [functional] done\n- [x] [functional] also done\n")
    return project_dir, run_dir, imp_path


class TestBacklogDrainedDiagnostic:
    """Orchestrator writes BACKLOG DRAINED diagnostic, not NO PROGRESS."""

    def setup_method(self):
        self.ui = MagicMock()

    def test_backlog_drained_no_converged_writes_dedicated_diagnostic(
        self, tmp_path: Path
    ):
        """Empty queue + no edits + no CONVERGED → ``BACKLOG DRAINED``
        diagnostic, not the generic NO PROGRESS one.
        """
        project_dir, run_dir, imp_path = _setup_project_with_all_checked(tmp_path)
        diagnostics: list[str] = []

        def mock_monitored(cmd, cwd, ui_, round_num, watchdog_timeout=120):
            # Write a conversation log so convo_size_before check passes,
            # but don't touch improvements.md (already all-checked) and
            # don't write CONVERGED.  This is exactly the state that
            # used to trip NO PROGRESS.
            convo = run_dir / f"conversation_loop_{round_num}.md"
            convo.write_text("# Round convo — nothing to do")
            return 0, "output", False

        def mock_save_diag(run_dir_, round_num_, cmd_, output_, reason, attempt):
            diagnostics.append(reason)

        with patch("evolve.application.run_loop._run_monitored_subprocess", side_effect=mock_monitored), \
             patch("evolve.application.run_loop._save_subprocess_diagnostic", side_effect=mock_save_diag), \
             patch("evolve.infrastructure.reporting.generator._generate_evolution_report"), \
             pytest.raises(SystemExit):
            _run_rounds(
                project_dir, run_dir, imp_path, self.ui,
                start_round=1, max_rounds=1, check_cmd=None,
                allow_installs=False, timeout=20, model="claude-opus-4-6",
            )

        # At least one diagnostic mentions BACKLOG DRAINED.
        assert any("BACKLOG DRAINED" in d for d in diagnostics), (
            f"expected BACKLOG DRAINED diagnostic, got: {diagnostics}"
        )
        # And none says NO PROGRESS — the drained case should have
        # pre-empted the generic detector.
        assert not any(
            "NO PROGRESS:" in d and "BACKLOG DRAINED" not in d
            for d in diagnostics
        ), (
            f"drained case must NOT fall through to NO PROGRESS: {diagnostics}"
        )


class TestBacklogDrainedPromptSection:
    """build_prompt renders a Phase-4-steering block when the diagnostic
    fired, not the punitive NO PROGRESS block."""

    def test_phase_4_steering_in_prompt(self, tmp_path: Path):
        run_dir = tmp_path / "runs" / "session"
        run_dir.mkdir(parents=True)
        (tmp_path / "README.md").write_text("# Test")
        diag = run_dir / "subprocess_error_round_2.txt"
        diag.write_text(
            "Round 2 — NO PROGRESS (attempt 1)\n"
            "Reason: BACKLOG DRAINED: all [ ] items checked off, "
            "but agent did not write CONVERGED"
        )

        prompt = build_prompt(
            tmp_path,
            check_output="",
            check_cmd=None,
            allow_installs=False,
            run_dir=run_dir,
            round_num=2,
        )

        # The drained-specific section appears instead of the generic
        # "CRITICAL — Previous round made NO PROGRESS" block.
        assert "Backlog drained, CONVERGED skipped" in prompt
        assert "Phase 4" in prompt
        assert "CONVERGED" in prompt
        # And the punitive wording does NOT leak in.
        assert "Start with Edit/Write immediately" not in prompt
