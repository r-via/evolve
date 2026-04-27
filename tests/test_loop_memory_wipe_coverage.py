"""Coverage tests for _run_rounds memory-wipe detection.

Extracted from test_loop_coverage.py to keep modules under the 500-line cap.
Mirrors the original TestRunRounds setup (setup_method + _setup_project).
"""

from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from evolve.orchestrator import (
    _MEMORY_COMPACTION_MARKER,
    _MEMORY_WIPE_THRESHOLD,
    _run_rounds,
)


class TestRunRoundsMemoryWipe:
    """Memory-wipe detection (>50% shrink without compaction marker)."""

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

    def _setup_git_with_commit(self, project_dir: Path, msg: str) -> None:
        """Init a git repo in project_dir and seed one commit with msg as full body."""
        import subprocess as sp
        import os
        env = {**os.environ,
               "GIT_AUTHOR_NAME": "test", "GIT_AUTHOR_EMAIL": "test@test.com",
               "GIT_COMMITTER_NAME": "test", "GIT_COMMITTER_EMAIL": "test@test.com"}
        sp.run(["git", "init"], cwd=str(project_dir), capture_output=True)
        sp.run(["git", "add", "-A"], cwd=str(project_dir), capture_output=True, env=env)
        sp.run(["git", "commit", "-m", msg, "--allow-empty"],
               cwd=str(project_dir), capture_output=True, env=env)

    def test_memory_wipe_triggers_retry_when_no_compaction_marker(self, tmp_path: Path):
        """>50% memory.md shrink without 'memory: compaction' in commit → MEMORY WIPED retry."""
        project_dir, run_dir, imp_path = self._setup_project(tmp_path)
        ui = self.ui
        diagnostics: list[str] = []
        # Seed a non-trivial memory.md (500+ bytes) so a wipe crosses the 50% threshold
        memory_path = project_dir / "runs" / "memory.md"
        memory_path.write_text("# Agent Memory\n\n" + ("line of memory context\n" * 40))
        mem_before = memory_path.stat().st_size
        assert mem_before > 500

        # Git commit WITHOUT "memory: compaction" marker
        self._setup_git_with_commit(project_dir, "feat: unrelated change\n\nbody only")

        attempt_counter = {"n": 0}

        def mock_monitored(cmd, cwd, ui_, round_num, watchdog_timeout=120):
            attempt_counter["n"] += 1
            convo = run_dir / f"conversation_loop_{round_num}.md"
            convo.write_text("# Round conversation")
            # Real improvement progress so imp_unchanged / no_commit_msg do NOT trigger
            existing = imp_path.read_text()
            imp_path.write_text(existing + f"- [ ] [functional] new {attempt_counter['n']}\n")
            # Restore memory to full size before each subprocess pass, then wipe —
            # exercises the 50% shrink path on EVERY attempt so the MEMORY WIPED
            # diagnostic is emitted whenever the agent re-commits a wipe.
            memory_path.write_text("# Agent Memory\n\n" + ("line of memory context\n" * 40))
            # Silently wipe memory.md (>50% shrink) — but orchestrator's snapshot
            # was taken BEFORE mock_monitored ran, so it sees full→wiped shrink.
            # To actually trigger, we simulate "before" full state and "after"
            # wipe by letting the orchestrator snapshot then the mock wipe.
            memory_path.write_text("# Agent Memory\n")
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
        # Diagnostic uses the MEMORY WIPED prefix (not NO PROGRESS) so the
        # agent prompt builder renders the dedicated header.
        assert any(d.startswith("MEMORY WIPED: ") for d in diagnostics), diagnostics
        # Threshold text is derived from the _MEMORY_WIPE_THRESHOLD constant —
        # any future change to the threshold will propagate here via the
        # constant rather than requiring a hand-edit of the magic percentage.
        threshold_pct = int(_MEMORY_WIPE_THRESHOLD * 100)
        assert any(f"memory.md shrunk by >{threshold_pct}%" in d for d in diagnostics)
        assert any(_MEMORY_COMPACTION_MARKER in d for d in diagnostics)

    def test_memory_wipe_allowed_when_commit_has_compaction_marker(self, tmp_path: Path):
        """>50% memory.md shrink WITH 'memory: compaction' in commit → no retry."""
        project_dir, run_dir, imp_path = self._setup_project(tmp_path)
        ui = self.ui
        diagnostics: list[str] = []
        memory_path = project_dir / "runs" / "memory.md"
        memory_path.write_text("# Agent Memory\n\n" + ("line of memory\n" * 40))

        # Git commit body explicitly declares compaction — uses the
        # _MEMORY_COMPACTION_MARKER constant so any future rename of the
        # marker propagates here without test edits.
        self._setup_git_with_commit(
            project_dir,
            f"chore(memory): compact\n\n{_MEMORY_COMPACTION_MARKER}\n\nTrimmed old entries.",
        )

        def mock_monitored(cmd, cwd, ui_, round_num, watchdog_timeout=120):
            convo = run_dir / f"conversation_loop_{round_num}.md"
            convo.write_text("# Round conversation")
            existing = imp_path.read_text()
            imp_path.write_text(existing + f"- [ ] [functional] new {round_num}\n")
            # Wipe memory.md — but commit says it's an intentional compaction
            memory_path.write_text("# Agent Memory\n")
            (run_dir / "CONVERGED").write_text("done")
            imp_path.write_text("- [x] [functional] do something\n")
            return 0, "output", False

        def mock_save_diag(run_dir_, round_num_, cmd_, output_, reason, attempt):
            diagnostics.append(reason)

        with patch("evolve.orchestrator._run_monitored_subprocess", side_effect=mock_monitored), \
             patch("evolve.orchestrator._is_self_evolving", return_value=True), \
             patch("evolve.orchestrator._save_subprocess_diagnostic", side_effect=mock_save_diag), \
             patch("evolve.orchestrator._generate_evolution_report"), \
             patch("evolve.orchestrator._run_party_mode"), \
             pytest.raises(SystemExit) as exc:
            _run_rounds(
                project_dir, run_dir, imp_path, ui,
                start_round=1, max_rounds=1, check_cmd=None,
                allow_installs=False, timeout=300, model="claude-opus-4-6",
            )
        # Convergence path — no MEMORY WIPED diagnostic should have been written
        assert exc.value.code == 0
        assert not any("MEMORY WIPED" in d for d in diagnostics), diagnostics

    def test_memory_wipe_not_triggered_on_small_shrink(self, tmp_path: Path):
        """memory.md shrinking by less than the threshold does NOT trigger wipe retry."""
        project_dir, run_dir, imp_path = self._setup_project(tmp_path)
        ui = self.ui
        diagnostics: list[str] = []
        memory_path = project_dir / "runs" / "memory.md"
        pre_size = 1000
        memory_path.write_text("x" * pre_size)
        self._setup_git_with_commit(project_dir, "feat: thing")

        # Post-size stays strictly ABOVE the threshold cutoff so the test
        # stays aligned with _MEMORY_WIPE_THRESHOLD: if the threshold is
        # ever raised to e.g. 0.75, this test's post-size must still be
        # above pre_size * threshold to remain a "small shrink".
        post_size = int(pre_size * _MEMORY_WIPE_THRESHOLD) + 50
        assert post_size >= pre_size * _MEMORY_WIPE_THRESHOLD  # sanity

        def mock_monitored(cmd, cwd, ui_, round_num, watchdog_timeout=120):
            convo = run_dir / f"conversation_loop_{round_num}.md"
            convo.write_text("# Round conversation")
            existing = imp_path.read_text()
            imp_path.write_text(existing + f"- [ ] [functional] new {round_num}\n")
            # Trim to just above the threshold — below the wipe cutoff
            memory_path.write_text("x" * post_size)
            (run_dir / "CONVERGED").write_text("done")
            imp_path.write_text("- [x] [functional] do something\n")
            return 0, "output", False

        def mock_save_diag(run_dir_, round_num_, cmd_, output_, reason, attempt):
            diagnostics.append(reason)

        with patch("evolve.orchestrator._run_monitored_subprocess", side_effect=mock_monitored), \
             patch("evolve.orchestrator._is_self_evolving", return_value=True), \
             patch("evolve.orchestrator._save_subprocess_diagnostic", side_effect=mock_save_diag), \
             patch("evolve.orchestrator._generate_evolution_report"), \
             patch("evolve.orchestrator._run_party_mode"), \
             pytest.raises(SystemExit) as exc:
            _run_rounds(
                project_dir, run_dir, imp_path, ui,
                start_round=1, max_rounds=1, check_cmd=None,
                allow_installs=False, timeout=300, model="claude-opus-4-6",
            )
        assert exc.value.code == 0
        assert not any("MEMORY WIPED" in d for d in diagnostics), diagnostics

    def test_memory_wipe_not_triggered_when_memory_absent(self, tmp_path: Path):
        """No memory.md pre-round → no wipe check (mem_size_before == 0)."""
        project_dir, run_dir, imp_path = self._setup_project(tmp_path)
        ui = self.ui
        diagnostics: list[str] = []
        # Do NOT create memory.md
        self._setup_git_with_commit(project_dir, "feat: first")

        def mock_monitored(cmd, cwd, ui_, round_num, watchdog_timeout=120):
            convo = run_dir / f"conversation_loop_{round_num}.md"
            convo.write_text("# Round conversation")
            existing = imp_path.read_text()
            imp_path.write_text(existing + f"- [ ] [functional] new {round_num}\n")
            (run_dir / "CONVERGED").write_text("done")
            imp_path.write_text("- [x] [functional] do something\n")
            return 0, "output", False

        def mock_save_diag(run_dir_, round_num_, cmd_, output_, reason, attempt):
            diagnostics.append(reason)

        with patch("evolve.orchestrator._run_monitored_subprocess", side_effect=mock_monitored), \
             patch("evolve.orchestrator._is_self_evolving", return_value=True), \
             patch("evolve.orchestrator._save_subprocess_diagnostic", side_effect=mock_save_diag), \
             patch("evolve.orchestrator._generate_evolution_report"), \
             patch("evolve.orchestrator._run_party_mode"), \
             pytest.raises(SystemExit) as exc:
            _run_rounds(
                project_dir, run_dir, imp_path, ui,
                start_round=1, max_rounds=1, check_cmd=None,
                allow_installs=False, timeout=300, model="claude-opus-4-6",
            )
        assert exc.value.code == 0
        assert not any("MEMORY WIPED" in d for d in diagnostics)

    def test_memory_wipe_prompt_header(self, tmp_path: Path):
        """agent.build_prompt renders the dedicated 'silently wiped memory.md' header
        when the diagnostic starts with 'MEMORY WIPED'."""
        from evolve.agent import build_prompt
        run_dir = tmp_path / "runs" / "session"
        run_dir.mkdir(parents=True)
        project_dir = tmp_path
        (project_dir / "README.md").write_text("# project")
        (project_dir / "runs" / "improvements.md").write_text("- [ ] [functional] do x\n")
        (project_dir / "runs" / "memory.md").write_text("# memory\n")
        # Simulate the orchestrator's diagnostic file — threshold percentage
        # and marker string are derived from module constants so any future
        # change propagates via the single source of truth.
        threshold_pct = int(_MEMORY_WIPE_THRESHOLD * 100)
        (run_dir / "subprocess_error_round_2.txt").write_text(
            f"Round 2 — MEMORY WIPED: memory.md shrunk by >{threshold_pct}% "
            f"(2000\u21925 bytes) "
            f"without '{_MEMORY_COMPACTION_MARKER}' in commit message (attempt 1)\n"
            "Output (last 3000 chars):\n...last output...\n"
        )

        prompt = build_prompt(
            project_dir=project_dir,
            run_dir=run_dir,
            round_num=2,
            check_cmd=None,
            check_output=None,
            allow_installs=False,
        )
        assert "CRITICAL — Previous round silently wiped memory.md" in prompt
        # Should NOT also render the generic NO PROGRESS header
        assert "Previous round made NO PROGRESS" not in prompt
        # Guidance about append-only / compaction marker should be surfaced
        assert _MEMORY_COMPACTION_MARKER in prompt

    def test_memory_wipe_constants_are_single_source_of_truth(self):
        """The documented contract in SPEC.md § 'Byte-size sanity gate' is
        encoded in two module-level constants, not scattered magic values.

        A single targeted test to catch drift: if anyone changes the marker
        string or the threshold in loop.py, this test keeps the value
        contract explicit and surfaces the change via a single failure
        rather than several scattered assertion mismatches.
        """
        # Marker is the literal SPEC string, not a variant.
        assert _MEMORY_COMPACTION_MARKER == "memory: compaction"
        # Threshold is a fraction in (0, 1) — any integer or out-of-range
        # value would break the shrink comparison in _run_rounds.
        assert isinstance(_MEMORY_WIPE_THRESHOLD, float)
        assert 0.0 < _MEMORY_WIPE_THRESHOLD < 1.0
        # Current SPEC contract: "shrunk by more than 50%" → threshold 0.5.
        assert _MEMORY_WIPE_THRESHOLD == 0.5
