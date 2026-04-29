import evolve.infrastructure.claude_sdk.runtime as _rt_mod
"""Coverage tests for _run_rounds — miscellaneous orchestration cases.

Extracted from test_loop_coverage.py to keep modules under the 500-line cap.
Covers: convergence override of unchanged-imp, forever-mode skip, blocked
improvements exit, error log cleanup, install-flag forwarding, effort flag
plumbing, and initial-analysis missing-improvement path.
"""

import sys
import textwrap
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from evolve.orchestrator import MAX_DEBUG_RETRIES, _run_rounds, run_single_round


class TestRunRoundsMisc:
    """Misc orchestration tests for _run_rounds."""

    def setup_method(self):
        self.ui = MagicMock()

    def _setup_project(self, tmp_path):
        project_dir = tmp_path / "proj"
        project_dir.mkdir()
        run_dir = project_dir / "runs" / "session"
        run_dir.mkdir(parents=True)
        imp_path = project_dir / "runs" / "improvements.md"
        imp_path.write_text("- [ ] [functional] do something\n")
        return project_dir, run_dir, imp_path

    def test_zero_progress_not_triggered_on_real_progress(self, tmp_path: Path):
        """Zero-progress detection does NOT trigger when improvements.md changes."""
        project_dir, run_dir, imp_path = self._setup_project(tmp_path)
        ui = self.ui

        def mock_monitored(cmd, cwd, ui_, round_num, watchdog_timeout=120):
            convo = run_dir / f"conversation_loop_{round_num}.md"
            convo.write_text("# Round conversation")
            # Check off the improvement — real progress.  The remaining
            # `- [ ]` item carries a ``[blocked: ...]`` tag so the
            # convergence-gate orchestrator backstop recognizes it as a
            # resolved-for-convergence blocker (rather than an unresolved
            # item) — see SPEC.md § "Convergence".
            imp_path.write_text(
                "- [x] [functional] do something\n"
                "- [ ] [functional] [blocked: upstream dep missing] next\n"
            )
            (run_dir / "CONVERGED").write_text("All done")
            return 0, "output", False

        with patch("evolve.orchestrator._run_monitored_subprocess", side_effect=mock_monitored), \
             patch("evolve.orchestrator._is_self_evolving", return_value=True), \
             patch("evolve.orchestrator._generate_evolution_report"), \
             patch("evolve.orchestrator._run_party_mode"), \
             pytest.raises(SystemExit) as exc:
            _run_rounds(
                project_dir, run_dir, imp_path, ui,
                start_round=1, max_rounds=10, check_cmd=None,
                allow_installs=False, timeout=300, model="claude-opus-4-6",
            )
        # Should converge (exit 0), not trigger zero-progress
        assert exc.value.code == 0

    def test_convergence_overrides_imp_unchanged(self, tmp_path: Path):
        """CONVERGED suppresses imp_unchanged zero-progress signal.

        When all items are already checked and the agent writes CONVERGED
        without modifying improvements.md, the round should converge — not
        be flagged as zero-progress.  Regression test for the case where
        a convergence round legitimately leaves improvements.md unchanged.
        """
        project_dir, run_dir, imp_path = self._setup_project(tmp_path)
        ui = self.ui
        # Start with all items already checked — nothing to change
        imp_path.write_text("- [x] [functional] do something\n")

        def mock_monitored(cmd, cwd, ui_, round_num, watchdog_timeout=120):
            convo = run_dir / f"conversation_loop_{round_num}.md"
            convo.write_text("# Convergence verification round")
            # Write CONVERGED but do NOT touch improvements.md
            (run_dir / "CONVERGED").write_text("All spec claims verified")
            return 0, "output", False

        with patch("evolve.orchestrator._run_monitored_subprocess", side_effect=mock_monitored), \
             patch("evolve.orchestrator._is_self_evolving", return_value=True), \
             patch("evolve.orchestrator._generate_evolution_report"), \
             patch("evolve.orchestrator._run_party_mode"), \
             pytest.raises(SystemExit) as exc:
            _run_rounds(
                project_dir, run_dir, imp_path, ui,
                start_round=1, max_rounds=10, check_cmd=None,
                allow_installs=False, timeout=300, model="claude-opus-4-6",
            )
        # Must converge (exit 0), NOT trigger zero-progress retry
        assert exc.value.code == 0

    def test_forever_mode_skips_failed_round(self, tmp_path: Path):
        """In forever mode, exhausted retries skip to next round."""
        project_dir, run_dir, imp_path = self._setup_project(tmp_path)
        ui = self.ui

        round_attempts = {}

        def mock_monitored(cmd, cwd, ui_, round_num, watchdog_timeout=120):
            round_attempts.setdefault(round_num, 0)
            round_attempts[round_num] += 1
            if round_num == 1:
                return 0, "output", True  # always stalls
            convo = run_dir / f"conversation_loop_{round_num}.md"
            convo.write_text("# Round 2")
            return 0, "output", False

        with patch("evolve.orchestrator._run_monitored_subprocess", side_effect=mock_monitored), \
             patch("evolve.orchestrator._is_self_evolving", return_value=True), \
             patch("evolve.orchestrator._save_subprocess_diagnostic"), \
             patch("evolve.orchestrator._generate_evolution_report"), \
             pytest.raises(SystemExit):
            _run_rounds(
                project_dir, run_dir, imp_path, ui,
                start_round=1, max_rounds=2, check_cmd=None,
                allow_installs=False, timeout=300, model="claude-opus-4-6",
                forever=True,
            )

        assert round_attempts[1] == MAX_DEBUG_RETRIES + 1

    def test_blocked_improvements_exit(self, tmp_path: Path):
        """All remaining items blocked exits with code 1."""
        project_dir, run_dir, imp_path = self._setup_project(tmp_path)
        imp_path.write_text("- [ ] [functional] [needs-package] blocked\n")
        ui = self.ui

        with pytest.raises(SystemExit) as exc:
            _run_rounds(
                project_dir, run_dir, imp_path, ui,
                start_round=1, max_rounds=10, check_cmd=None,
                allow_installs=False, timeout=300, model="claude-opus-4-6",
            )
        assert exc.value.code == 1
        ui.blocked_message.assert_called_once()

    def test_error_log_cleanup_on_success(self, tmp_path: Path):
        """Diagnostic file is cleaned up after a successful round."""
        project_dir, run_dir, imp_path = self._setup_project(tmp_path)

        error_log = run_dir / "subprocess_error_round_1.txt"
        error_log.write_text("previous error")

        def mock_monitored(cmd, cwd, ui_, round_num, watchdog_timeout=120):
            convo = run_dir / f"conversation_loop_{round_num}.md"
            convo.write_text("# Round conversation")
            # Simulate progress by modifying improvements.md
            imp_path.write_text("- [x] [functional] do something\n- [ ] [functional] next\n")
            return 0, "output", False

        with patch("evolve.orchestrator._run_monitored_subprocess", side_effect=mock_monitored), \
             patch("evolve.orchestrator._is_self_evolving", return_value=True), \
             patch("evolve.orchestrator._generate_evolution_report"), \
             pytest.raises(SystemExit):
            _run_rounds(
                project_dir, run_dir, imp_path, self.ui,
                start_round=1, max_rounds=1, check_cmd=None,
                allow_installs=False, timeout=300, model="claude-opus-4-6",
            )

        assert not error_log.is_file()

    def test_allow_installs_flag_passed_to_subprocess(self, tmp_path: Path):
        """--allow-installs flag is included in subprocess command."""
        project_dir, run_dir, imp_path = self._setup_project(tmp_path)

        captured_cmd = None

        def mock_monitored(cmd, cwd, ui_, round_num, watchdog_timeout=120):
            nonlocal captured_cmd
            captured_cmd = cmd
            convo = run_dir / f"conversation_loop_{round_num}.md"
            convo.write_text("# Round")
            return 0, "output", False

        with patch("evolve.orchestrator._run_monitored_subprocess", side_effect=mock_monitored), \
             patch("evolve.orchestrator._is_self_evolving", return_value=True), \
             patch("evolve.orchestrator._generate_evolution_report"), \
             pytest.raises(SystemExit):
            _run_rounds(
                project_dir, run_dir, imp_path, self.ui,
                start_round=1, max_rounds=1, check_cmd="pytest",
                allow_installs=True, timeout=300, model="claude-opus-4-6",
            )

        assert "--allow-installs" in captured_cmd
        assert "--check" in captured_cmd

    def test_effort_flag_forwarded_to_subprocess(self, tmp_path: Path):
        """--effort <level> is included in the subprocess command built by _run_rounds."""
        project_dir, run_dir, imp_path = self._setup_project(tmp_path)

        captured_cmd = None

        def mock_monitored(cmd, cwd, ui_, round_num, watchdog_timeout=120):
            nonlocal captured_cmd
            captured_cmd = cmd
            convo = run_dir / f"conversation_loop_{round_num}.md"
            convo.write_text("# Round")
            return 0, "output", False

        with patch("evolve.orchestrator._run_monitored_subprocess", side_effect=mock_monitored), \
             patch("evolve.orchestrator._is_self_evolving", return_value=True), \
             patch("evolve.orchestrator._generate_evolution_report"), \
             pytest.raises(SystemExit):
            _run_rounds(
                project_dir, run_dir, imp_path, self.ui,
                start_round=1, max_rounds=1, check_cmd="pytest",
                allow_installs=False, timeout=300, model="claude-opus-4-6",
                effort="high",
            )

        assert captured_cmd is not None, "subprocess was never launched"
        assert "--effort" in captured_cmd
        idx = captured_cmd.index("--effort")
        assert captured_cmd[idx + 1] == "high"

    def test_effort_flag_omitted_when_none(self, tmp_path: Path):
        """When effort is None, --effort is NOT included in the subprocess command."""
        project_dir, run_dir, imp_path = self._setup_project(tmp_path)

        captured_cmd = None

        def mock_monitored(cmd, cwd, ui_, round_num, watchdog_timeout=120):
            nonlocal captured_cmd
            captured_cmd = cmd
            convo = run_dir / f"conversation_loop_{round_num}.md"
            convo.write_text("# Round")
            return 0, "output", False

        with patch("evolve.orchestrator._run_monitored_subprocess", side_effect=mock_monitored), \
             patch("evolve.orchestrator._is_self_evolving", return_value=True), \
             patch("evolve.orchestrator._generate_evolution_report"), \
             pytest.raises(SystemExit):
            _run_rounds(
                project_dir, run_dir, imp_path, self.ui,
                start_round=1, max_rounds=1, check_cmd="pytest",
                allow_installs=False, timeout=300, model="claude-opus-4-6",
                effort=None,
            )

        assert captured_cmd is not None, "subprocess was never launched"
        assert "--effort" not in captured_cmd

    def test_effort_each_level_forwarded(self, tmp_path: Path):
        """Each accepted effort level (low/medium/high/max) is forwarded correctly."""
        for level in ("low", "medium", "high", "max"):
            (tmp_path / level).mkdir()
            project_dir, run_dir, imp_path = self._setup_project(tmp_path / level)

            captured_cmd = None

            def mock_monitored(cmd, cwd, ui_, round_num, watchdog_timeout=120):
                nonlocal captured_cmd
                captured_cmd = cmd
                convo = run_dir / f"conversation_loop_{round_num}.md"
                convo.write_text("# Round")
                return 0, "output", False

            with patch("evolve.orchestrator._run_monitored_subprocess", side_effect=mock_monitored), \
             patch("evolve.orchestrator._is_self_evolving", return_value=True), \
                 patch("evolve.orchestrator._generate_evolution_report"), \
                 pytest.raises(SystemExit):
                _run_rounds(
                    project_dir, run_dir, imp_path, MagicMock(),
                    start_round=1, max_rounds=1, check_cmd=None,
                    allow_installs=False, timeout=300, model="claude-opus-4-6",
                    effort=level,
                )

            assert captured_cmd is not None, f"subprocess not launched for effort={level}"
            idx = captured_cmd.index("--effort")
            assert captured_cmd[idx + 1] == level, (
                f"effort={level} not forwarded: got {captured_cmd[idx + 1]}"
            )

    def test_parse_round_args_receives_forwarded_effort(self):
        """_parse_round_args correctly parses --effort from the subprocess argv."""
        from evolve import _parse_round_args

        with patch("sys.argv", [
            "evolve", "_round", "/tmp/proj",
            "--round-num", "1",
            "--effort", "low",
        ]):
            args = _parse_round_args()
        assert args.effort == "low"

    def test_parse_round_args_effort_default_is_medium(self):
        """_parse_round_args defaults effort to 'medium' when --effort is omitted."""
        from evolve import _parse_round_args

        with patch("sys.argv", [
            "evolve", "_round", "/tmp/proj",
            "--round-num", "1",
        ]):
            args = _parse_round_args()
        assert args.effort == "medium"

    def test_effort_end_to_end_argv_to_agent_module(self, tmp_path: Path):
        """Full plumbing: _run_rounds builds argv with --effort, _parse_round_args
        parses it, and run_single_round sets agent.EFFORT to the parsed value."""
        import evolve.agent as _agent_mod
        from evolve import _parse_round_args

        project_dir, run_dir, imp_path = self._setup_project(tmp_path)

        # Step 1: capture the command _run_rounds would build
        captured_cmd = None

        def mock_monitored(cmd, cwd, ui_, round_num, watchdog_timeout=120):
            nonlocal captured_cmd
            captured_cmd = cmd
            convo = run_dir / f"conversation_loop_{round_num}.md"
            convo.write_text("# Round")
            return 0, "output", False

        with patch("evolve.orchestrator._run_monitored_subprocess", side_effect=mock_monitored), \
             patch("evolve.orchestrator._is_self_evolving", return_value=True), \
             patch("evolve.orchestrator._generate_evolution_report"), \
             pytest.raises(SystemExit):
            _run_rounds(
                project_dir, run_dir, imp_path, self.ui,
                start_round=1, max_rounds=1, check_cmd="pytest",
                allow_installs=False, timeout=300, model="claude-opus-4-6",
                effort="medium",
            )

        # Step 2: feed the captured argv through _parse_round_args
        # The captured_cmd is [sys.executable, -m, evolve, _round, proj, ...flags...]
        # _parse_round_args reads sys.argv[2:], so we simulate that by taking
        # everything after the `_round` marker and prepending ["evolve", "_round"].
        round_idx = captured_cmd.index("_round")
        simulated_argv = ["evolve", "_round"] + captured_cmd[round_idx + 1:]
        with patch("sys.argv", simulated_argv):
            args = _parse_round_args()
        assert args.effort == "medium"

        # Step 3: verify run_single_round would set agent.EFFORT
        original = __rt_mod.EFFORT
        try:
            with patch("evolve.agent.analyze_and_fix", return_value=None), \
                 patch("evolve.agent.run_review_agent"):
                run_single_round(
                    project_dir=project_dir,
                    round_num=1,
                    check_cmd=None,
                    allow_installs=False,
                    timeout=60,
                    run_dir=run_dir,
                    model="claude-opus-4-6",
                    effort=args.effort,
                )
            assert __rt_mod.EFFORT == "medium"
        finally:
            __rt_mod.EFFORT = original

    def test_no_current_improvement_initial_analysis(self, tmp_path: Path):
        """When no improvements exist yet, shows 'initial analysis'."""
        project_dir, run_dir, imp_path = self._setup_project(tmp_path)
        imp_path.write_text("# Improvements\n")  # empty

        def mock_monitored(cmd, cwd, ui_, round_num, watchdog_timeout=120):
            convo = run_dir / f"conversation_loop_{round_num}.md"
            convo.write_text("# Round")
            return 0, "output", False

        with patch("evolve.orchestrator._run_monitored_subprocess", side_effect=mock_monitored), \
             patch("evolve.orchestrator._is_self_evolving", return_value=True), \
             patch("evolve.orchestrator._generate_evolution_report"), \
             pytest.raises(SystemExit):
            _run_rounds(
                project_dir, run_dir, imp_path, self.ui,
                start_round=1, max_rounds=1, check_cmd=None,
                allow_installs=False, timeout=300, model="claude-opus-4-6",
            )

        self.ui.round_header.assert_called()
