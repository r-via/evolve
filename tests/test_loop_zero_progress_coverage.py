"""Coverage tests for _run_rounds zero-progress detection.

Extracted from test_loop_coverage.py to keep modules under the 500-line cap.
Mirrors the original TestRunRounds setup (setup_method + _setup_project).
"""

from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from evolve.orchestrator import _run_rounds


class TestRunRoundsZeroProgress:
    """Zero-progress detection (improvements byte-identical, no COMMIT_MSG, etc.)."""

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

    def test_zero_progress_improvements_unchanged(self, tmp_path: Path):
        """Zero-progress when improvements.md is byte-identical to pre-round state."""
        project_dir, run_dir, imp_path = self._setup_project(tmp_path)
        ui = self.ui
        diagnostics = []

        def mock_monitored(cmd, cwd, ui_, round_num, watchdog_timeout=120):
            # Create conversation log but do NOT modify improvements.md
            convo = run_dir / f"conversation_loop_{round_num}.md"
            convo.write_text("# Round conversation with activity")
            return 0, "output", False

        def mock_save_diag(run_dir_, round_num_, cmd_, output_, reason, attempt):
            diagnostics.append(reason)

        with patch("evolve.orchestrator._run_monitored_subprocess", side_effect=mock_monitored), \
             patch("evolve.orchestrator._is_self_evolving", return_value=True), \
             patch("evolve.orchestrator._save_subprocess_diagnostic", side_effect=mock_save_diag), \
             patch("evolve.orchestrator._generate_evolution_report"), \
             pytest.raises(SystemExit) as exc:
            _run_rounds(
                project_dir, run_dir, imp_path, ui,
                start_round=1, max_rounds=1, check_cmd=None,
                allow_installs=False, timeout=300, model="claude-opus-4-6",
            )
        assert exc.value.code == 4  # circuit breaker — identical signatures across attempts
        # All retries should report improvements.md unchanged
        assert any("improvements.md byte-identical" in d for d in diagnostics)
        assert any("NO PROGRESS" in d for d in diagnostics)

    def test_zero_progress_no_commit_msg(self, tmp_path: Path):
        """Zero-progress when agent commits without writing COMMIT_MSG.

        The ``no_commit_msg`` check fires only when HEAD MOVED during
        the attempt AND the new HEAD's message matches the fallback
        template.  The mock must therefore create a real commit per
        attempt so the HEAD-moved gate is satisfied.
        """
        project_dir, run_dir, imp_path = self._setup_project(tmp_path)
        ui = self.ui
        diagnostics = []

        import os
        import subprocess as sp
        _git_env = {
            **os.environ, "GIT_AUTHOR_NAME": "test", "GIT_AUTHOR_EMAIL": "test@test.com",
            "GIT_COMMITTER_NAME": "test", "GIT_COMMITTER_EMAIL": "test@test.com",
        }
        sp.run(["git", "init"], cwd=str(project_dir), capture_output=True)
        sp.run(["git", "add", "-A"], cwd=str(project_dir), capture_output=True)
        sp.run(
            ["git", "commit", "-m", "initial commit"],
            cwd=str(project_dir), capture_output=True, env=_git_env,
        )

        def mock_monitored(cmd, cwd, ui_, round_num, watchdog_timeout=120):
            convo = run_dir / f"conversation_loop_{round_num}.md"
            convo.write_text("# Round conversation")
            existing = imp_path.read_text()
            imp_path.write_text(existing + f"- [ ] [functional] item {round_num}\n")
            # Simulate the "agent didn't write COMMIT_MSG, orchestrator
            # fell back to chore(evolve): round N" path by creating the
            # fallback commit per attempt.  This is what the check
            # under test is designed to detect.
            sp.run(["git", "add", "-A"], cwd=str(cwd), capture_output=True)
            sp.run(
                ["git", "commit", "-m", f"chore(evolve): round {round_num}",
                 "--allow-empty"],
                cwd=str(cwd), capture_output=True, env=_git_env,
            )
            return 0, "output", False

        def mock_save_diag(run_dir_, round_num_, cmd_, output_, reason, attempt):
            diagnostics.append(reason)

        with patch("evolve.orchestrator._run_monitored_subprocess", side_effect=mock_monitored), \
             patch("evolve.orchestrator._is_self_evolving", return_value=True), \
             patch("evolve.orchestrator._save_subprocess_diagnostic", side_effect=mock_save_diag), \
             patch("evolve.orchestrator._generate_evolution_report"), \
             pytest.raises(SystemExit) as exc:
            _run_rounds(
                project_dir, run_dir, imp_path, ui,
                start_round=1, max_rounds=1, check_cmd=None,
                allow_installs=False, timeout=300, model="claude-opus-4-6",
            )
        # Heterogeneous signatures (attempt 1 sees backlog violation,
        # attempts 2-3 see only no_commit_msg once the item already
        # exists in the growing imp) → circuit breaker does NOT fire →
        # classic exit 2.
        assert exc.value.code == 2
        # Should detect fallback commit message
        assert any("no COMMIT_MSG written" in d for d in diagnostics)
        assert any("NO PROGRESS" in d for d in diagnostics)

    def test_zero_progress_both_conditions(self, tmp_path: Path):
        """Zero-progress reports both conditions when both are true."""
        project_dir, run_dir, imp_path = self._setup_project(tmp_path)
        ui = self.ui
        diagnostics = []

        import os
        import subprocess as sp
        _git_env = {
            **os.environ, "GIT_AUTHOR_NAME": "test", "GIT_AUTHOR_EMAIL": "test@test.com",
            "GIT_COMMITTER_NAME": "test", "GIT_COMMITTER_EMAIL": "test@test.com",
        }
        sp.run(["git", "init"], cwd=str(project_dir), capture_output=True)
        sp.run(["git", "add", "-A"], cwd=str(project_dir), capture_output=True)
        sp.run(
            ["git", "commit", "-m", "initial commit"],
            cwd=str(project_dir), capture_output=True, env=_git_env,
        )

        def mock_monitored(cmd, cwd, ui_, round_num, watchdog_timeout=120):
            # Don't modify improvements.md — but make a fallback commit
            # so the ``no_commit_msg`` HEAD-moved gate is satisfied.
            convo = run_dir / f"conversation_loop_{round_num}.md"
            convo.write_text("# Round conversation")
            sp.run(
                ["git", "commit", "-m", f"chore(evolve): round {round_num}",
                 "--allow-empty"],
                cwd=str(cwd), capture_output=True, env=_git_env,
            )
            return 0, "output", False

        def mock_save_diag(run_dir_, round_num_, cmd_, output_, reason, attempt):
            diagnostics.append(reason)

        with patch("evolve.orchestrator._run_monitored_subprocess", side_effect=mock_monitored), \
             patch("evolve.orchestrator._is_self_evolving", return_value=True), \
             patch("evolve.orchestrator._save_subprocess_diagnostic", side_effect=mock_save_diag), \
             patch("evolve.orchestrator._generate_evolution_report"), \
             pytest.raises(SystemExit) as exc:
            _run_rounds(
                project_dir, run_dir, imp_path, ui,
                start_round=1, max_rounds=1, check_cmd=None,
                allow_installs=False, timeout=300, model="claude-opus-4-6",
            )
        assert exc.value.code == 4  # circuit breaker — identical signatures across attempts
        # Both conditions should be present in the diagnostic
        assert any("no COMMIT_MSG written" in d and "improvements.md byte-identical" in d for d in diagnostics)

    def test_zero_progress_both_conditions_reason_string_format(self, tmp_path: Path):
        """Zero-progress reason string uses ' AND ' to join both conditions."""
        project_dir, run_dir, imp_path = self._setup_project(tmp_path)
        ui = self.ui
        diagnostics = []

        import os
        import subprocess as sp
        _git_env = {
            **os.environ, "GIT_AUTHOR_NAME": "test", "GIT_AUTHOR_EMAIL": "test@test.com",
            "GIT_COMMITTER_NAME": "test", "GIT_COMMITTER_EMAIL": "test@test.com",
        }
        sp.run(["git", "init"], cwd=str(project_dir), capture_output=True)
        sp.run(["git", "add", "-A"], cwd=str(project_dir), capture_output=True)
        sp.run(
            ["git", "commit", "-m", "initial commit"],
            cwd=str(project_dir), capture_output=True, env=_git_env,
        )

        def mock_monitored(cmd, cwd, ui_, round_num, watchdog_timeout=120):
            convo = run_dir / f"conversation_loop_{round_num}.md"
            convo.write_text("# Round conversation")
            sp.run(
                ["git", "commit", "-m", f"chore(evolve): round {round_num}",
                 "--allow-empty"],
                cwd=str(cwd), capture_output=True, env=_git_env,
            )
            return 0, "output", False

        def mock_save_diag(run_dir_, round_num_, cmd_, output_, reason, attempt):
            diagnostics.append(reason)

        with patch("evolve.orchestrator._run_monitored_subprocess", side_effect=mock_monitored), \
             patch("evolve.orchestrator._is_self_evolving", return_value=True), \
             patch("evolve.orchestrator._save_subprocess_diagnostic", side_effect=mock_save_diag), \
             patch("evolve.orchestrator._generate_evolution_report"), \
             pytest.raises(SystemExit):
            _run_rounds(
                project_dir, run_dir, imp_path, ui,
                start_round=1, max_rounds=1, check_cmd=None,
                allow_installs=False, timeout=300, model="claude-opus-4-6",
            )
        # Verify the exact format: "NO PROGRESS: <reason1> AND <reason2>"
        combined = [d for d in diagnostics if " AND " in d]
        assert len(combined) > 0, "Expected at least one diagnostic with ' AND ' join"
        for d in combined:
            assert d.startswith("NO PROGRESS: ")
            assert "no COMMIT_MSG written (fallback commit message)" in d
            assert "improvements.md byte-identical to pre-round state" in d

    def test_zero_progress_triggers_debug_retry_count(self, tmp_path: Path):
        """Zero-progress triggers the correct number of debug retries (MAX_DEBUG_RETRIES + 1 total)."""
        project_dir, run_dir, imp_path = self._setup_project(tmp_path)
        ui = self.ui
        attempt_log = []

        def mock_monitored(cmd, cwd, ui_, round_num, watchdog_timeout=120):
            convo = run_dir / f"conversation_loop_{round_num}.md"
            convo.write_text("# Round conversation")
            return 0, "output", False

        def mock_save_diag(run_dir_, round_num_, cmd_, output_, reason, attempt):
            attempt_log.append(attempt)

        with patch("evolve.orchestrator._run_monitored_subprocess", side_effect=mock_monitored), \
             patch("evolve.orchestrator._is_self_evolving", return_value=True), \
             patch("evolve.orchestrator._save_subprocess_diagnostic", side_effect=mock_save_diag), \
             patch("evolve.orchestrator._generate_evolution_report"), \
             pytest.raises(SystemExit) as exc:
            _run_rounds(
                project_dir, run_dir, imp_path, ui,
                start_round=1, max_rounds=1, check_cmd=None,
                allow_installs=False, timeout=300, model="claude-opus-4-6",
            )
        assert exc.value.code == 4  # circuit breaker — identical signatures across attempts
        # Should have MAX_DEBUG_RETRIES + 1 attempts (1 original + 2 retries)
        from evolve.orchestrator import MAX_DEBUG_RETRIES
        assert len(attempt_log) == MAX_DEBUG_RETRIES + 1
        assert attempt_log == list(range(1, MAX_DEBUG_RETRIES + 2))

    def test_zero_progress_improvements_unchanged_only_no_git(self, tmp_path: Path):
        """Zero-progress triggers on improvements.md unchanged even without git repo (no COMMIT_MSG check)."""
        project_dir, run_dir, imp_path = self._setup_project(tmp_path)
        ui = self.ui
        diagnostics = []

        def mock_monitored(cmd, cwd, ui_, round_num, watchdog_timeout=120):
            convo = run_dir / f"conversation_loop_{round_num}.md"
            convo.write_text("# Round conversation")
            # Do NOT modify improvements.md — triggers byte-identical
            return 0, "output", False

        def mock_save_diag(run_dir_, round_num_, cmd_, output_, reason, attempt):
            diagnostics.append(reason)

        with patch("evolve.orchestrator._run_monitored_subprocess", side_effect=mock_monitored), \
             patch("evolve.orchestrator._is_self_evolving", return_value=True), \
             patch("evolve.orchestrator._save_subprocess_diagnostic", side_effect=mock_save_diag), \
             patch("evolve.orchestrator._generate_evolution_report"), \
             pytest.raises(SystemExit) as exc:
            _run_rounds(
                project_dir, run_dir, imp_path, ui,
                start_round=1, max_rounds=1, check_cmd=None,
                allow_installs=False, timeout=300, model="claude-opus-4-6",
            )
        assert exc.value.code == 4  # circuit breaker — identical signatures across attempts
        # Should still detect byte-identical even without git
        assert any("improvements.md byte-identical" in d for d in diagnostics)
        # Should NOT contain "no COMMIT_MSG" since git log would fail
        # (no git repo = git log fails silently, no_commit_msg stays False)
        assert all("no COMMIT_MSG written" not in d for d in diagnostics if "AND" not in d)

    def test_zero_progress_no_commit_msg_only_reason_string(self, tmp_path: Path):
        """When only COMMIT_MSG is missing (improvements changed), reason has only COMMIT_MSG part."""
        project_dir, run_dir, imp_path = self._setup_project(tmp_path)
        ui = self.ui
        diagnostics = []

        import os
        import subprocess as sp
        _git_env = {
            **os.environ, "GIT_AUTHOR_NAME": "test", "GIT_AUTHOR_EMAIL": "test@test.com",
            "GIT_COMMITTER_NAME": "test", "GIT_COMMITTER_EMAIL": "test@test.com",
        }
        sp.run(["git", "init"], cwd=str(project_dir), capture_output=True)
        sp.run(["git", "add", "-A"], cwd=str(project_dir), capture_output=True)
        sp.run(
            ["git", "commit", "-m", "initial commit"],
            cwd=str(project_dir), capture_output=True, env=_git_env,
        )

        def mock_monitored(cmd, cwd, ui_, round_num, watchdog_timeout=120):
            convo = run_dir / f"conversation_loop_{round_num}.md"
            convo.write_text("# Round conversation")
            # Modify improvements.md so byte-identical does NOT trigger
            existing = imp_path.read_text()
            imp_path.write_text(existing + f"\n- [ ] [functional] item {round_num}\n")
            # Simulate orchestrator fallback commit so HEAD actually
            # moves this attempt — the new no_commit_msg gate requires
            # a real HEAD movement to fire.
            sp.run(["git", "add", "-A"], cwd=str(cwd), capture_output=True)
            sp.run(
                ["git", "commit", "-m", f"chore(evolve): round {round_num}"],
                cwd=str(cwd), capture_output=True, env=_git_env,
            )
            return 0, "output", False

        def mock_save_diag(run_dir_, round_num_, cmd_, output_, reason, attempt):
            diagnostics.append(reason)

        with patch("evolve.orchestrator._run_monitored_subprocess", side_effect=mock_monitored), \
             patch("evolve.orchestrator._is_self_evolving", return_value=True), \
             patch("evolve.orchestrator._save_subprocess_diagnostic", side_effect=mock_save_diag), \
             patch("evolve.orchestrator._generate_evolution_report"), \
             pytest.raises(SystemExit):
            _run_rounds(
                project_dir, run_dir, imp_path, ui,
                start_round=1, max_rounds=1, check_cmd=None,
                allow_installs=False, timeout=300, model="claude-opus-4-6",
            )
        # Only COMMIT_MSG reason, no " AND " join
        commit_msg_only = [d for d in diagnostics if "no COMMIT_MSG written" in d and " AND " not in d]
        assert len(commit_msg_only) > 0, "Expected diagnostic with only COMMIT_MSG reason"
