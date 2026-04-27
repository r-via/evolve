"""Tests for the "party mode only in ``--forever``" scope.

Party mode drafts the next-cycle spec proposal via a multi-agent
brainstorm.  That proposal is only useful if the forever loop
is going to run another cycle; without ``--forever`` the session
ends on convergence (exit 0) and the proposal has no consumer —
so party mode should be skipped entirely to avoid the wasted Opus
call.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from evolve.orchestrator import _run_rounds


class TestPartyModeForeverOnly:
    """Orchestrator runs party mode iff ``forever=True``."""

    def setup_method(self):
        self.ui = MagicMock()

    def _setup(self, tmp_path: Path) -> tuple[Path, Path, Path]:
        project_dir = tmp_path / "proj"
        project_dir.mkdir()
        run_dir = project_dir / "runs" / "session"
        run_dir.mkdir(parents=True)
        imp_path = project_dir / "runs" / "improvements.md"
        imp_path.write_text("- [ ] [functional] do X\n")
        return project_dir, run_dir, imp_path

    def test_non_forever_convergence_skips_party_mode(self, tmp_path: Path):
        """Convergence without ``--forever`` exits 0 and does NOT call
        ``_run_party_mode``.
        """
        project_dir, run_dir, imp_path = self._setup(tmp_path)

        def mock_monitored(cmd, cwd, ui_, round_num, watchdog_timeout=120):
            convo = run_dir / f"conversation_loop_{round_num}.md"
            convo.write_text("# Round")
            (run_dir / "CONVERGED").write_text("All done")
            imp_path.write_text("- [x] [functional] do X\n")
            return 0, "output", False

        with patch("evolve.orchestrator._run_monitored_subprocess", side_effect=mock_monitored), \
             patch("evolve.diagnostics._generate_evolution_report"), \
             patch("evolve.orchestrator._run_party_mode") as mock_party, \
             pytest.raises(SystemExit) as exc:
            _run_rounds(
                project_dir, run_dir, imp_path, self.ui,
                start_round=1, max_rounds=10, check_cmd="pytest",
                allow_installs=False, timeout=20, model="claude-opus-4-6",
                forever=False,
            )
        assert exc.value.code == 0
        mock_party.assert_not_called()

    def test_forever_convergence_runs_party_mode(self, tmp_path: Path):
        """Convergence in ``--forever`` mode DOES invoke party mode
        (its proposal is consumed by the next cycle).
        """
        project_dir, run_dir, imp_path = self._setup(tmp_path)

        def mock_monitored(cmd, cwd, ui_, round_num, watchdog_timeout=120):
            convo = run_dir / f"conversation_loop_{round_num}.md"
            convo.write_text("# Round")
            (run_dir / "CONVERGED").write_text("All done")
            imp_path.write_text("- [x] [functional] do X\n")
            return 0, "output", False

        # After the convergence call, ``_forever_restart`` would
        # normally kick off a second cycle; we mock it to raise so
        # the test terminates cleanly.
        def stop_forever_restart(*args, **kwargs):
            raise SystemExit(99)

        with patch("evolve.orchestrator._run_monitored_subprocess", side_effect=mock_monitored), \
             patch("evolve.diagnostics._generate_evolution_report"), \
             patch("evolve.orchestrator._run_party_mode") as mock_party, \
             patch("evolve.orchestrator._forever_restart", side_effect=stop_forever_restart), \
             pytest.raises(SystemExit) as exc:
            _run_rounds(
                project_dir, run_dir, imp_path, self.ui,
                start_round=1, max_rounds=10, check_cmd="pytest",
                allow_installs=False, timeout=20, model="claude-opus-4-6",
                forever=True,
            )
        # Exit 99 because _forever_restart raised — proves the flow
        # reached the forever branch.
        assert exc.value.code == 99
        mock_party.assert_called_once()
