"""Branch-coverage tests for ``_run_rounds`` integration paths.

Split off from ``tests/test_loop_branches.py`` to keep both files under the
SPEC § "Hard rule: source files MUST NOT exceed 500 lines" cap. Drives
``_run_rounds`` through specific branches not covered elsewhere:
check-file parsing, git-log timeouts, memory-wipe detection,
spec-freshness gate, no-progress diagnostic, CONVERGED rejection.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import evolve.application.run_loop as _orch


# ---------------------------------------------------------------------------
# _run_rounds — check_file parsing + README audit
# at CONVERGED + CONVERGED rejected when spec is newer than improvements.md
# (covers lines 1538-1539, 1561-1564, 1691-1692, 1698-1699, 1736-1742,
# 1754-1755, 1646)
# ---------------------------------------------------------------------------

class TestRunRoundsIntegrationBranches:
    """Drive _run_rounds through specific branches not covered elsewhere."""

    def _setup(self, tmp_path: Path):
        project_dir = tmp_path / "proj"
        project_dir.mkdir()
        run_dir = project_dir / "runs" / "session"
        run_dir.mkdir(parents=True)
        imp_path = project_dir / "runs" / "improvements.md"
        imp_path.write_text("- [ ] [functional] do something\n")
        return project_dir, run_dir, imp_path

    def test_check_file_parsing_branch(self, tmp_path: Path):
        """check_round_N.txt is read and parsed in _run_rounds (1691-1692)."""
        project_dir, run_dir, imp_path = self._setup(tmp_path)
        ui = MagicMock()

        def mock_monitored(cmd, cwd, ui_, round_num, watchdog_timeout=120):
            convo = run_dir / f"conversation_loop_{round_num}.md"
            convo.write_text("# Round")
            imp_path.write_text("- [x] [functional] do something\n")
            # Seed the check_round_N.txt file so the parsing branch fires
            (run_dir / f"check_round_{round_num}.txt").write_text(
                "Round 1 post-fix check: PASS\n42 passed\n"
            )
            (run_dir / "CONVERGED").write_text("Done")
            return 0, "out", False

        with patch("evolve.application.run_loop._run_monitored_subprocess", side_effect=mock_monitored), \
             patch("evolve.infrastructure.reporting.generator._generate_evolution_report"), \
             patch("evolve.application.run_loop._run_party_mode"), \
             pytest.raises(SystemExit):
            _orch._run_rounds(
                project_dir, run_dir, imp_path, ui,
                start_round=1, max_rounds=1, check_cmd="pytest",
                allow_installs=False, timeout=300, model="claude-opus-4-6",
            )
        # state.json should have been written with tests=42
        state = (run_dir / "state.json").read_text()
        assert "42" in state

    def test_git_log_timeout_gracefully_handled(self, tmp_path: Path):
        """TimeoutExpired in commit-msg check (1538-1539) swallowed."""
        project_dir, run_dir, imp_path = self._setup(tmp_path)
        ui = MagicMock()

        def mock_monitored(cmd, cwd, ui_, round_num, watchdog_timeout=120):
            convo = run_dir / f"conversation_loop_{round_num}.md"
            convo.write_text("# Round")
            imp_path.write_text("- [x] [functional] do something\n")
            (run_dir / "CONVERGED").write_text("Done")
            return 0, "out", False

        real_run = subprocess.run

        def flaky_run(cmd, *args, **kwargs):
            # Specifically raise on the "git log -1 --format=%s" call
            if isinstance(cmd, list) and "log" in cmd and "--format=%s" in cmd:
                raise subprocess.TimeoutExpired(cmd=cmd, timeout=10)
            return real_run(cmd, *args, **kwargs)

        with patch("evolve.application.run_loop._run_monitored_subprocess", side_effect=mock_monitored), \
             patch("evolve.infrastructure.reporting.generator._generate_evolution_report"), \
             patch("evolve.application.run_loop._run_party_mode"), \
             patch("evolve.application.run_loop.subprocess.run", side_effect=flaky_run), \
             pytest.raises(SystemExit):
            _orch._run_rounds(
                project_dir, run_dir, imp_path, ui,
                start_round=1, max_rounds=1, check_cmd="pytest",
                allow_installs=False, timeout=300, model="claude-opus-4-6",
            )
        # Got through without crashing
        assert (run_dir / "state.json").is_file()

    def test_memory_wipe_commit_body_timeout(self, tmp_path: Path):
        """TimeoutExpired reading commit body (1561-1564) swallowed → wipe assumed."""
        project_dir, run_dir, imp_path = self._setup(tmp_path)
        ui = MagicMock()

        # Pre-seed memory.md with enough content that a <half shrink is a wipe
        mem = project_dir / "runs" / "memory.md"
        mem.write_text("x" * 1000)

        def mock_monitored(cmd, cwd, ui_, round_num, watchdog_timeout=120):
            convo = run_dir / f"conversation_loop_{round_num}.md"
            convo.write_text("# Round")
            # Shrink memory.md to trigger wipe detection
            mem.write_text("x" * 100)
            imp_path.write_text("- [x] [functional] do something\n")
            (run_dir / "CONVERGED").write_text("Done")
            return 0, "out", False

        real_run = subprocess.run

        def flaky_run(cmd, *args, **kwargs):
            if isinstance(cmd, list) and "log" in cmd and "--format=%B" in cmd:
                raise subprocess.TimeoutExpired(cmd=cmd, timeout=10)
            return real_run(cmd, *args, **kwargs)

        with patch("evolve.application.run_loop._run_monitored_subprocess", side_effect=mock_monitored), \
             patch("evolve.infrastructure.reporting.generator._generate_evolution_report"), \
             patch("evolve.application.run_loop._run_party_mode"), \
             patch("evolve.application.run_loop._save_subprocess_diagnostic"), \
             patch("evolve.application.run_loop.subprocess.run", side_effect=flaky_run), \
             pytest.raises(SystemExit):
            _orch._run_rounds(
                project_dir, run_dir, imp_path, ui,
                start_round=1, max_rounds=1, check_cmd="pytest",
                allow_installs=False, timeout=300, model="claude-opus-4-6",
            )

    def test_converged_rejected_when_spec_newer(self, tmp_path: Path):
        """CONVERGED is unlinked when spec mtime > improvements mtime (1754-1755)."""
        project_dir, run_dir, imp_path = self._setup(tmp_path)
        ui = MagicMock()

        spec_path = project_dir / "SPEC.md"
        spec_path.write_text("# Spec\n")

        def mock_monitored(cmd, cwd, ui_, round_num, watchdog_timeout=120):
            convo = run_dir / f"conversation_loop_{round_num}.md"
            convo.write_text("# Round")
            # Mark item done and write CONVERGED
            imp_path.write_text("- [x] [functional] done\n")
            (run_dir / "CONVERGED").write_text("Done")
            # Now make the spec NEWER than improvements.md. Set an older mtime
            # on improvements.md and a newer mtime on SPEC.md.
            import os, time as _time
            old = _time.time() - 1000
            new = _time.time()
            os.utime(imp_path, (old, old))
            os.utime(spec_path, (new, new))
            return 0, "out", False

        # When spec is newer, _check_spec_freshness marks items stale —
        # which means "add a next item". So the loop will continue past the
        # rejected CONVERGED. Use max_rounds=1 so it exits at max via SystemExit.
        with patch("evolve.application.run_loop._run_monitored_subprocess", side_effect=mock_monitored), \
             patch("evolve.infrastructure.reporting.generator._generate_evolution_report"), \
             patch("evolve.application.run_loop._run_party_mode"), \
             pytest.raises(SystemExit):
            _orch._run_rounds(
                project_dir, run_dir, imp_path, ui,
                start_round=1, max_rounds=1, check_cmd="pytest",
                allow_installs=False, timeout=300, model="claude-opus-4-6",
                spec="SPEC.md",
            )

    def test_no_progress_diagnostic_written(self, tmp_path: Path):
        """No-progress round (line 1646) triggers _save_subprocess_diagnostic."""
        project_dir, run_dir, imp_path = self._setup(tmp_path)
        ui = MagicMock()

        def mock_monitored(cmd, cwd, ui_, round_num, watchdog_timeout=120):
            # Don't touch improvements.md, don't write COMMIT_MSG — zero progress
            convo = run_dir / f"conversation_loop_{round_num}.md"
            convo.write_text("# Round")
            return 0, "out", False

        save_calls = []

        def spy_save(*args, **kwargs):
            save_calls.append(kwargs.get("reason", ""))

        with patch("evolve.application.run_loop._run_monitored_subprocess", side_effect=mock_monitored), \
             patch("evolve.application.run_loop._save_subprocess_diagnostic", side_effect=spy_save), \
             patch("evolve.infrastructure.reporting.generator._generate_evolution_report"), \
             pytest.raises(SystemExit):
            _orch._run_rounds(
                project_dir, run_dir, imp_path, ui,
                start_round=1, max_rounds=1, check_cmd="pytest",
                allow_installs=False, timeout=300, model="claude-opus-4-6",
            )

        # At least one save call with the "no progress" reason
        assert any("no progress" in r.lower() for r in save_calls)

    def test_spec_freshness_gate_prints_when_stale(self, tmp_path: Path, capsys):
        """Line 1431 prints when spec is newer than improvements.md."""
        project_dir, run_dir, imp_path = self._setup(tmp_path)
        ui = MagicMock()

        spec_path = project_dir / "SPEC.md"
        spec_path.write_text("# Spec\n")

        # spec newer than improvements.md from the very first round
        import os, time as _time
        old = _time.time() - 10000
        new = _time.time()
        os.utime(imp_path, (old, old))
        os.utime(spec_path, (new, new))

        def mock_monitored(cmd, cwd, ui_, round_num, watchdog_timeout=120):
            convo = run_dir / f"conversation_loop_{round_num}.md"
            convo.write_text("# Round")
            imp_path.write_text("- [x] [functional] done\n")
            (run_dir / "CONVERGED").write_text("Done")
            # Keep spec newer than improvements.md
            os.utime(imp_path, (old, old))
            return 0, "out", False

        with patch("evolve.application.run_loop._run_monitored_subprocess", side_effect=mock_monitored), \
             patch("evolve.infrastructure.reporting.generator._generate_evolution_report"), \
             patch("evolve.application.run_loop._run_party_mode"), \
             pytest.raises(SystemExit):
            _orch._run_rounds(
                project_dir, run_dir, imp_path, ui,
                start_round=1, max_rounds=1, check_cmd="pytest",
                allow_installs=False, timeout=300, model="claude-opus-4-6",
                spec="SPEC.md",
            )

        captured = capsys.readouterr()
        assert "spec freshness gate" in captured.out
