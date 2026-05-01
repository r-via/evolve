"""Tests for the convergence-gate orchestrator backstop.

Covers SPEC.md § "Convergence" — after the agent writes CONVERGED, the
orchestrator re-verifies the two documented gates independently:

1. Spec freshness gate — ``mtime(improvements.md) >= mtime(spec_file)``
   AND no ``[stale: spec changed]`` items.
2. Backlog gate — every ``- [ ]`` line carries ``[needs-package]`` or
   ``[blocked:``.

When either gate fails, the backstop unlinks CONVERGED, emits
``ui.error``, and saves a ``PREMATURE CONVERGED`` diagnostic so the next
round picks up the dedicated ``CRITICAL — Premature CONVERGED`` header
via ``agent.py``'s ``build_prompt``.

These tests exercise the two helpers (`_detect_premature_converged`
and `_enforce_convergence_backstop`) across exhaustive gate
combinations, plus the agent-side header rendering in
``agent.build_prompt``.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from evolve.infrastructure.claude_sdk.prompt_builder import build_prompt
from evolve.application.run_loop import _enforce_convergence_backstop
from evolve.infrastructure.filesystem.state_manager import _detect_premature_converged


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _touch(path: Path, mtime: float) -> None:
    """Set a file's mtime to a specific value."""
    os.utime(path, (mtime, mtime))


def _make_project(tmp_path: Path, *, spec_text: str = "# spec\n",
                  improvements_text: str = "- [x] done\n",
                  spec_name: str = "README.md",
                  improvements_newer: bool = True) -> tuple[Path, Path, Path]:
    """Create a minimal project layout with spec and improvements.md.

    Returns ``(project_dir, spec_path, improvements_path)``.  When
    ``improvements_newer`` is True the spec's mtime is set 10s before the
    improvements file (default "fresh" scenario); when False the spec is
    set 10s after (stale backlog scenario).
    """
    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    (project_dir / "runs").mkdir()
    spec_path = project_dir / spec_name
    improvements_path = project_dir / "runs" / "improvements.md"
    spec_path.write_text(spec_text)
    improvements_path.write_text(improvements_text)

    base = time.time()
    if improvements_newer:
        _touch(spec_path, base - 10.0)
        _touch(improvements_path, base)
    else:
        _touch(spec_path, base)
        _touch(improvements_path, base - 10.0)
    return project_dir, spec_path, improvements_path


# ---------------------------------------------------------------------------
# _detect_premature_converged — exhaustive gate combinations
# ---------------------------------------------------------------------------


class TestDetectPrematureConverged:
    """Verify gate logic for every documented combination."""

    def test_queue_empty_and_spec_fresh_not_premature(self, tmp_path: Path):
        """All items checked, spec older → not premature."""
        _, spec, imp = _make_project(
            tmp_path,
            improvements_text="- [x] [functional] done\n- [x] [functional] done2\n",
            improvements_newer=True,
        )
        is_premature, reason = _detect_premature_converged(imp, spec)
        assert is_premature is False
        assert reason == ""

    def test_only_tagged_blockers_remaining_not_premature(self, tmp_path: Path):
        """`- [ ]` items all tagged [needs-package]/[blocked:] → not premature."""
        _, spec, imp = _make_project(
            tmp_path,
            improvements_text=(
                "- [x] done\n"
                "- [ ] [functional] [needs-package] install foo\n"
                "- [ ] [functional] [blocked: dep] later\n"
            ),
            improvements_newer=True,
        )
        is_premature, reason = _detect_premature_converged(imp, spec)
        assert is_premature is False, reason

    def test_one_unresolved_item_premature(self, tmp_path: Path):
        """A single plain `- [ ]` line → premature, backlog gate cited."""
        _, spec, imp = _make_project(
            tmp_path,
            improvements_text="- [x] done\n- [ ] [functional] next thing\n",
            improvements_newer=True,
        )
        is_premature, reason = _detect_premature_converged(imp, spec)
        assert is_premature is True
        assert "backlog gate" in reason
        assert "1 unresolved" in reason
        assert "next thing" in reason

    def test_stale_spec_changed_items_premature(self, tmp_path: Path):
        """`[stale: spec changed]` present → premature via spec freshness gate."""
        _, spec, imp = _make_project(
            tmp_path,
            improvements_text=(
                "- [x] done\n"
                "- [ ] [stale: spec changed] [functional] rebuild\n"
            ),
            improvements_newer=True,
        )
        is_premature, reason = _detect_premature_converged(imp, spec)
        assert is_premature is True
        assert "spec freshness gate" in reason
        assert "[stale: spec changed]" in reason

    def test_stale_phrase_in_checked_item_description_not_premature(
        self, tmp_path: Path
    ):
        """``[stale: spec changed]`` inside a ``[x]`` description must NOT trigger the gate.

        Regression test: the gate previously did a naive substring match on
        the full file text, which matched the phrase inside completed item
        descriptions like "Implement Phase 2 … mark items as
        ``[stale: spec changed]``".
        """
        _, spec, imp = _make_project(
            tmp_path,
            improvements_text=(
                "- [x] [functional] Implement Phase 2 spec freshness gate: "
                "mark every unchecked item as `[stale: spec changed]` and "
                "rebuild improvements.md\n"
            ),
            improvements_newer=True,
        )
        is_premature, reason = _detect_premature_converged(imp, spec)
        assert is_premature is False
        assert reason == ""

    def test_spec_newer_than_improvements_premature(self, tmp_path: Path):
        """mtime(spec) > mtime(improvements.md) → premature via freshness."""
        _, spec, imp = _make_project(
            tmp_path,
            improvements_text="- [x] done\n",
            improvements_newer=False,  # spec is newer
        )
        is_premature, reason = _detect_premature_converged(imp, spec)
        assert is_premature is True
        assert "spec freshness gate" in reason
        assert "mtime" in reason

    def test_both_gates_fail_concatenates_reasons(self, tmp_path: Path):
        """Spec newer + unresolved items → both reasons joined by AND."""
        _, spec, imp = _make_project(
            tmp_path,
            improvements_text="- [x] done\n- [ ] unresolved item\n",
            improvements_newer=False,
        )
        is_premature, reason = _detect_premature_converged(imp, spec)
        assert is_premature is True
        assert "spec freshness gate" in reason
        assert "backlog gate" in reason
        assert " AND " in reason

    def test_missing_improvements_file(self, tmp_path: Path):
        """Missing improvements.md → not premature (no way to check)."""
        project_dir = tmp_path / "proj"
        project_dir.mkdir()
        spec = project_dir / "README.md"
        spec.write_text("# spec\n")
        imp = project_dir / "runs" / "improvements.md"  # does not exist
        is_premature, reason = _detect_premature_converged(imp, spec)
        assert is_premature is False
        assert reason == ""

    def test_missing_spec_file(self, tmp_path: Path):
        """Missing spec file → freshness gate skipped, backlog still evaluated."""
        project_dir = tmp_path / "proj"
        project_dir.mkdir()
        (project_dir / "runs").mkdir()
        spec = project_dir / "README.md"  # does not exist
        imp = project_dir / "runs" / "improvements.md"
        imp.write_text("- [x] done\n")
        is_premature, reason = _detect_premature_converged(imp, spec)
        assert is_premature is False

    def test_unresolved_sample_truncates_to_3(self, tmp_path: Path):
        """Sample field lists at most 3 unresolved items."""
        items = "\n".join(f"- [ ] [functional] item {i}" for i in range(10))
        _, spec, imp = _make_project(
            tmp_path,
            improvements_text=items + "\n",
            improvements_newer=True,
        )
        is_premature, reason = _detect_premature_converged(imp, spec)
        assert is_premature is True
        assert "10 unresolved" in reason
        # Sample contains at most 3 — item 0..2, not items 3..9
        assert "item 0" in reason
        assert "item 1" in reason
        assert "item 2" in reason
        assert "item 4" not in reason


# ---------------------------------------------------------------------------
# _enforce_convergence_backstop — side-effect tests
# ---------------------------------------------------------------------------


class TestEnforceConvergenceBackstop:
    """Verify backstop unlinks CONVERGED, calls ui.error, writes diagnostic."""

    def _mk_run_dir(self, tmp_path: Path) -> tuple[Path, Path]:
        """Create run_dir and CONVERGED marker.  Returns (run_dir, converged_path)."""
        run_dir = tmp_path / "runs" / "session1"
        run_dir.mkdir(parents=True)
        converged = run_dir / "CONVERGED"
        converged.write_text("All done per agent\n")
        return run_dir, converged

    def test_no_converged_file_returns_false(self, tmp_path: Path):
        """No CONVERGED file → backstop is a no-op."""
        _, spec, imp = _make_project(tmp_path, improvements_text="- [ ] unresolved\n")
        run_dir = tmp_path / "runs" / "s"
        run_dir.mkdir(parents=True)
        ui = MagicMock()
        result = _enforce_convergence_backstop(
            converged_path=run_dir / "CONVERGED",  # does not exist
            improvements_path=imp,
            spec_path=spec,
            run_dir=run_dir,
            round_num=5,
            cmd=["python", "agent.py"],
            output="stdout",
            attempt=1,
            ui=ui,
        )
        assert result is False
        ui.error.assert_not_called()

    def test_gates_pass_returns_false_no_side_effects(self, tmp_path: Path):
        """Both gates pass → backstop returns False, CONVERGED stays."""
        _, spec, imp = _make_project(
            tmp_path, improvements_text="- [x] done\n", improvements_newer=True,
        )
        run_dir, converged = self._mk_run_dir(tmp_path)
        ui = MagicMock()
        result = _enforce_convergence_backstop(
            converged_path=converged,
            improvements_path=imp,
            spec_path=spec,
            run_dir=run_dir,
            round_num=3,
            cmd=["python", "agent.py"],
            output="",
            attempt=1,
            ui=ui,
        )
        assert result is False
        assert converged.is_file()  # preserved
        ui.error.assert_not_called()
        # No diagnostic saved
        assert not (run_dir / "subprocess_error_round_3.txt").is_file()

    def test_gate_fails_unlinks_converged(self, tmp_path: Path):
        """Unresolved item → CONVERGED is deleted."""
        _, spec, imp = _make_project(
            tmp_path,
            improvements_text="- [x] done\n- [ ] still pending\n",
            improvements_newer=True,
        )
        run_dir, converged = self._mk_run_dir(tmp_path)
        ui = MagicMock()
        result = _enforce_convergence_backstop(
            converged_path=converged,
            improvements_path=imp,
            spec_path=spec,
            run_dir=run_dir,
            round_num=7,
            cmd=["python", "agent.py"],
            output="trailing output",
            attempt=2,
            ui=ui,
        )
        assert result is True
        assert not converged.is_file()

    def test_gate_fails_emits_ui_error(self, tmp_path: Path):
        """Gate failure → ui.error called with reason text."""
        _, spec, imp = _make_project(
            tmp_path,
            improvements_text="- [x] done\n- [ ] still pending\n",
        )
        run_dir, converged = self._mk_run_dir(tmp_path)
        ui = MagicMock()
        _enforce_convergence_backstop(
            converged_path=converged,
            improvements_path=imp,
            spec_path=spec,
            run_dir=run_dir,
            round_num=7,
            cmd=[],
            output="",
            attempt=2,
            ui=ui,
        )
        ui.error.assert_called_once()
        msg = ui.error.call_args[0][0]
        assert "Premature CONVERGED" in msg
        assert "backlog gate" in msg

    def test_gate_fails_writes_diagnostic_with_premature_prefix(self, tmp_path: Path):
        """Gate failure → subprocess_error_round_N.txt written with PREMATURE CONVERGED prefix."""
        _, spec, imp = _make_project(
            tmp_path,
            improvements_text="- [x] done\n- [ ] still pending\n",
        )
        run_dir, converged = self._mk_run_dir(tmp_path)
        ui = MagicMock()
        _enforce_convergence_backstop(
            converged_path=converged,
            improvements_path=imp,
            spec_path=spec,
            run_dir=run_dir,
            round_num=9,
            cmd=["python", "agent.py"],
            output="tail of agent output",
            attempt=2,
            ui=ui,
        )
        diag = run_dir / "subprocess_error_round_9.txt"
        assert diag.is_file()
        text = diag.read_text()
        assert "PREMATURE CONVERGED" in text
        assert "backlog gate" in text
        # attempt number is preserved in the reason header
        assert "attempt 2" in text

    def test_diagnostic_triggers_debug_retry_header(self, tmp_path: Path):
        """The diagnostic persists — next round's build_prompt picks it up."""
        _, spec, imp = _make_project(
            tmp_path,
            improvements_text=(
                "- [x] done\n- [ ] unresolved yet\n"
            ),
        )
        run_dir, converged = self._mk_run_dir(tmp_path)
        ui = MagicMock()
        _enforce_convergence_backstop(
            converged_path=converged,
            improvements_path=imp,
            spec_path=spec,
            run_dir=run_dir,
            round_num=4,
            cmd=[],
            output="",
            attempt=1,
            ui=ui,
        )
        # The diagnostic should still exist — debug retry on next round
        assert (run_dir / "subprocess_error_round_4.txt").is_file()


# ---------------------------------------------------------------------------
# agent.build_prompt — PREMATURE CONVERGED branch
# ---------------------------------------------------------------------------


class TestAgentPromptPrematureConvergedHeader:
    """Verify build_prompt renders the dedicated CRITICAL header."""

    def test_premature_converged_renders_dedicated_header(self, tmp_path: Path):
        """A diagnostic containing ``PREMATURE CONVERGED`` triggers the dedicated branch."""
        # Create a minimal project layout
        project_dir = tmp_path / "proj"
        project_dir.mkdir()
        (project_dir / "README.md").write_text("# spec\n")
        (project_dir / "runs").mkdir()
        (project_dir / "runs" / "improvements.md").write_text(
            "- [ ] foo\n- [ ] bar\n"
        )
        run_dir = project_dir / "runs" / "s"
        run_dir.mkdir(parents=True)
        diag = run_dir / "subprocess_error_round_3.txt"
        diag.write_text(
            "Round 3 — PREMATURE CONVERGED: backlog gate: 2 unresolved "
            "`- [ ]` item(s) without [needs-package]/[blocked:] "
            "tags (sample: - [ ] foo; - [ ] bar) (attempt 1)\n"
        )
        prompt = build_prompt(
            project_dir=project_dir,
            check_output="",
            run_dir=run_dir,
            round_num=4,
        )
        assert "## CRITICAL — Premature CONVERGED" in prompt
        # Must NOT fall through to the generic CRASHED header
        assert "## CRITICAL — Previous round CRASHED" not in prompt
        # Must NOT misidentify as other diagnostic branches — check the
        # dedicated headers, not substrings that appear in the static
        # system-prompt prose (those literals appear in SPEC-related
        # guidance regardless of the diagnostic branch).
        assert "## CRITICAL — Previous round silently wiped memory.md" not in prompt
        assert "## CRITICAL — Backlog discipline violation:" not in prompt
        assert "## CRITICAL — Previous round made NO PROGRESS" not in prompt
        # Core instruction present (case-insensitive — prompt phrasing is
        # "Do NOT write CONVERGED again")
        assert "not write converged again" in prompt.lower()

    def test_other_diagnostic_types_unaffected(self, tmp_path: Path):
        """NO PROGRESS diagnostic still routes to the NO PROGRESS branch."""
        project_dir = tmp_path / "proj"
        project_dir.mkdir()
        (project_dir / "README.md").write_text("# spec\n")
        (project_dir / "runs").mkdir()
        (project_dir / "runs" / "improvements.md").write_text("- [ ] a\n")
        run_dir = project_dir / "runs" / "s"
        run_dir.mkdir(parents=True)
        diag = run_dir / "subprocess_error_round_2.txt"
        diag.write_text(
            "Round 2 — NO PROGRESS: improvements.md byte-identical (attempt 1)\n"
        )
        prompt = build_prompt(
            project_dir=project_dir,
            check_output="",
            run_dir=run_dir,
            round_num=3,
        )
        assert "## CRITICAL — Previous round made NO PROGRESS" in prompt
        assert "Premature CONVERGED" not in prompt


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
