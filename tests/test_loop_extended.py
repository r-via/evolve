"""Extended tests for loop.py — remaining helpers after the round-11 split.

TestEnsureGit and TestGitCommit moved to test_loop_git.py.
TestGenerateEvolutionReport moved to test_evolution_report_basic.py.
"""

import textwrap
from pathlib import Path
from unittest.mock import patch, MagicMock

_real_import = __builtins__.__import__ if hasattr(__builtins__, '__import__') else __import__


def _make_import_blocker(*blocked_names):
    """Return a mock __import__ that blocks specific module names."""
    def mock_import(name, *args, **kwargs):
        if name in blocked_names:
            raise ImportError(f"mocked: {name}")
        return _real_import(name, *args, **kwargs)
    return mock_import


from evolve.application.run_loop import (
    _parse_report_summary,
    _run_monitored_subprocess,
    _save_subprocess_diagnostic,
)
from evolve.infrastructure.claude_sdk.party import _run_party_mode
from evolve.infrastructure.filesystem.improvement_parser import _count_blocked
from evolve.infrastructure.filesystem.improvement_parser import (
    _count_checked,
    _count_unchecked,
    _get_current_improvement,
)


# ---------------------------------------------------------------------------
# _count_blocked — edge cases
# ---------------------------------------------------------------------------

class TestCountBlockedExtended:
    def test_missing_file(self, tmp_path: Path):
        assert _count_blocked(tmp_path / "nonexistent.md") == 0

    def test_empty_file(self, tmp_path: Path):
        f = tmp_path / "imp.md"
        f.write_text("")
        assert _count_blocked(f) == 0

    def test_checked_needs_package_not_counted(self, tmp_path: Path):
        """Already-checked [needs-package] items should not be counted as blocked."""
        f = tmp_path / "imp.md"
        f.write_text("- [x] [functional] [needs-package] already done\n")
        assert _count_blocked(f) == 0

    def test_mixed_items(self, tmp_path: Path):
        f = tmp_path / "imp.md"
        f.write_text(textwrap.dedent("""\
            - [x] [functional] done
            - [ ] [functional] [needs-package] blocked 1
            - [ ] [functional] normal pending
            - [ ] [performance] [needs-package] blocked 2
            - [x] [performance] [needs-package] done pkg
        """))
        assert _count_blocked(f) == 2


# ---------------------------------------------------------------------------
# _count_checked / _count_unchecked — more edge cases
# ---------------------------------------------------------------------------

class TestCountersExtended:
    def test_empty_file_checked(self, tmp_path: Path):
        f = tmp_path / "imp.md"
        f.write_text("# Improvements\n")
        assert _count_checked(f) == 0

    def test_empty_file_unchecked(self, tmp_path: Path):
        f = tmp_path / "imp.md"
        f.write_text("# Improvements\n")
        assert _count_unchecked(f) == 0

    def test_many_items(self, tmp_path: Path):
        lines = ["# Improvements\n"]
        for i in range(20):
            if i % 3 == 0:
                lines.append(f"- [x] item {i}\n")
            else:
                lines.append(f"- [ ] item {i}\n")
        f = tmp_path / "imp.md"
        f.write_text("".join(lines))
        assert _count_checked(f) == 7   # 0,3,6,9,12,15,18
        assert _count_unchecked(f) == 13


# ---------------------------------------------------------------------------
# _get_current_improvement — more edge cases
# ---------------------------------------------------------------------------

class TestGetCurrentImprovementExtended:
    def test_empty_file(self, tmp_path: Path):
        f = tmp_path / "imp.md"
        f.write_text("# Improvements\n")
        assert _get_current_improvement(f) is None

    def test_all_needs_package_no_yolo(self, tmp_path: Path):
        f = tmp_path / "imp.md"
        f.write_text(textwrap.dedent("""\
            - [ ] [functional] [needs-package] blocked one
            - [ ] [performance] [needs-package] blocked two
        """))
        assert _get_current_improvement(f, allow_installs=False) is None

    def test_all_needs_package_with_yolo(self, tmp_path: Path):
        f = tmp_path / "imp.md"
        f.write_text(textwrap.dedent("""\
            - [ ] [functional] [needs-package] blocked one
            - [ ] [performance] [needs-package] blocked two
        """))
        result = _get_current_improvement(f, allow_installs=True)
        assert result == "[functional] [needs-package] blocked one"


# ---------------------------------------------------------------------------
# _run_party_mode — early exits
# ---------------------------------------------------------------------------

class TestRunPartyMode:
    def test_no_agents_dir(self, tmp_path: Path):
        """Skips gracefully when no agents directory exists.

        ``_run_party_mode`` was extracted to ``evolve/party.py`` during
        the package restructuring, so ``loop.Path`` is no longer the
        import site to patch — the fallback lookup
        ``Path(__file__).parent.parent / "agents"`` now resolves via
        ``evolve.party.Path``.  Patching the wrong module used to leave
        the real repo's ``agents/`` directory discoverable, which
        triggered a full Claude Agent SDK session (158s per run).
        """
        run_dir = tmp_path / "runs" / "session"
        run_dir.mkdir(parents=True)
        with patch("evolve.infrastructure.claude_sdk.party.Path") as mock_path:
            # Any Path(...) returns an object whose is_dir() is False, and
            # whose ``.parent.parent / "agents"`` sub-path is likewise
            # absent — covers both the project-local and the
            # evolve-shipped fallback agents directories.
            mock_path_inst = MagicMock()
            mock_path_inst.is_dir.return_value = False
            mock_path_inst.parent.parent.__truediv__.return_value.is_dir.return_value = False
            mock_path.return_value = mock_path_inst
            _run_party_mode(tmp_path, run_dir)  # should not crash

    def test_agents_present_but_sdk_missing(self, tmp_path: Path):
        """Falls back gracefully when agents exist but SDK is not importable."""
        agents = tmp_path / "agents"
        agents.mkdir()
        (agents / "dev.md").write_text("# Dev persona")
        run_dir = tmp_path / "runs" / "session"
        run_dir.mkdir(parents=True)
        (tmp_path / "README.md").write_text("# Test")
        (tmp_path / "runs" / "improvements.md").write_text("- [x] done\n")

        # Make SDK import fail
        with patch.dict("sys.modules", {"claude_agent_sdk": None}):
            with patch("builtins.__import__", side_effect=_make_import_blocker("claude_agent_sdk")):
                _run_party_mode(tmp_path, run_dir)  # should not crash


# ---------------------------------------------------------------------------
# Resume logic — _detect_last_round inline in evolve_loop
# ---------------------------------------------------------------------------

class TestResumeLogic:
    def test_detect_last_round_from_convos(self, tmp_path: Path):
        """Test the resume detection logic extracts correct round number."""
        runs = tmp_path / "runs"
        session = runs / "20260101_000000"
        session.mkdir(parents=True)
        (session / "conversation_loop_1.md").write_text("round 1")
        (session / "conversation_loop_2.md").write_text("round 2")
        (session / "conversation_loop_3.md").write_text("round 3")

        # Replicate the inline resume detection logic from evolve_loop
        convos = sorted(session.glob("conversation_loop_*.md"))
        last = convos[-1].stem
        last_round = int(last.rsplit("_", 1)[1])
        assert last_round == 3

    def test_detect_last_round_no_convos(self, tmp_path: Path):
        """When no conversation logs exist, start_round stays at 1."""
        runs = tmp_path / "runs"
        session = runs / "20260101_000000"
        session.mkdir(parents=True)

        convos = sorted(session.glob("conversation_loop_*.md"))
        assert len(convos) == 0
        # In the real code, start_round stays at 1 when no convos found


# ---------------------------------------------------------------------------
# _run_monitored_subprocess — watchdog and output streaming
# ---------------------------------------------------------------------------

class TestRunMonitoredSubprocess:
    def setup_method(self):
        """Fresh UI mock per test — avoids per-test MagicMock() boilerplate."""
        self.ui = MagicMock()
        self._python = __import__("sys").executable

    def test_successful_subprocess(self, tmp_path: Path):
        """A fast subprocess returns output and exit code 0."""
        # watchdog_timeout=2 drops the poll interval to 200ms (vs 1s
        # at the production default) — the ``print('hello')`` itself
        # finishes in milliseconds but the poll loop paces the return.
        cmd = [self._python, "-c", "print('hello')"]
        returncode, output, stalled = _run_monitored_subprocess(
            cmd, str(tmp_path), self.ui, round_num=1, watchdog_timeout=2,
        )
        assert returncode == 0
        assert "hello" in output
        assert stalled is False

    def test_failing_subprocess(self, tmp_path: Path):
        """A subprocess that exits with error returns non-zero code."""
        cmd = [self._python, "-c", "import sys; print('boom'); sys.exit(42)"]
        returncode, output, stalled = _run_monitored_subprocess(
            cmd, str(tmp_path), self.ui, round_num=1, watchdog_timeout=2,
        )
        assert returncode == 42
        assert "boom" in output
        assert stalled is False

    def test_stalled_subprocess_killed(self, tmp_path: Path):
        """A subprocess producing no output is killed by the watchdog."""
        # Sleep for 60s but watchdog is 1s.  Poll interval scales with
        # watchdog, so kill happens ~1.1s after start — vs ~3s before
        # the scaled poll was introduced.
        cmd = [self._python, "-c", "import time; time.sleep(60)"]
        returncode, output, stalled = _run_monitored_subprocess(
            cmd, str(tmp_path), self.ui, round_num=1, watchdog_timeout=1,
        )
        assert stalled is True
        self.ui.warn.assert_called_once()
        assert "stalled" in self.ui.warn.call_args[0][0]


# ---------------------------------------------------------------------------
# _save_subprocess_diagnostic
# ---------------------------------------------------------------------------

class TestSaveSubprocessDiagnostic:
    def test_writes_diagnostic_file(self, tmp_path: Path):
        _save_subprocess_diagnostic(
            tmp_path, round_num=3, cmd=["python", "evolve.py", "_round"],
            output="Traceback:\n  File main.py\nSyntaxError",
            reason="crashed (exit code 1)", attempt=2,
        )
        diag = tmp_path / "subprocess_error_round_3.txt"
        assert diag.is_file()
        content = diag.read_text()
        assert "Round 3" in content
        assert "crashed" in content
        assert "attempt 2" in content
        assert "SyntaxError" in content


# ---------------------------------------------------------------------------
# _parse_report_summary
# ---------------------------------------------------------------------------

class TestParseReportSummary:
    """Tests for _parse_report_summary extraction from evolution_report.md."""

    def test_full_report(self, tmp_path: Path):
        """Extracts all stats from a well-formed report."""
        (tmp_path / "evolution_report.md").write_text(
            "## Summary\n- 6 improvements completed\n- 2 bugs fixed\n- 12 files modified\n"
        )
        (tmp_path / "check_round_3.txt").write_text("47 passed in 1.3s\n")
        result = _parse_report_summary(tmp_path)
        assert result["improvements"] == 6
        assert result["bugs_fixed"] == 2
        assert result["tests_passing"] == 47

    def test_no_report_file(self, tmp_path: Path):
        """Returns zeros when evolution_report.md does not exist."""
        result = _parse_report_summary(tmp_path)
        assert result["improvements"] == 0
        assert result["bugs_fixed"] == 0
        assert result["tests_passing"] is None

    def test_empty_report(self, tmp_path: Path):
        """Returns zeros when report is empty."""
        (tmp_path / "evolution_report.md").write_text("")
        result = _parse_report_summary(tmp_path)
        assert result["improvements"] == 0
        assert result["bugs_fixed"] == 0
        assert result["tests_passing"] is None

    def test_malformed_report_no_numbers(self, tmp_path: Path):
        """Returns zeros when report has text but no matching patterns."""
        (tmp_path / "evolution_report.md").write_text(
            "# Report\nSome random text without numbers in expected format.\n"
        )
        result = _parse_report_summary(tmp_path)
        assert result["improvements"] == 0
        assert result["bugs_fixed"] == 0

    def test_partial_report_only_improvements(self, tmp_path: Path):
        """Extracts improvements when bugs line is missing."""
        (tmp_path / "evolution_report.md").write_text(
            "## Summary\n- 3 improvements completed\n"
        )
        result = _parse_report_summary(tmp_path)
        assert result["improvements"] == 3
        assert result["bugs_fixed"] == 0

    def test_partial_report_only_bugs(self, tmp_path: Path):
        """Extracts bugs when improvements line is missing."""
        (tmp_path / "evolution_report.md").write_text(
            "## Summary\n- 5 bugs fixed\n"
        )
        result = _parse_report_summary(tmp_path)
        assert result["improvements"] == 0
        assert result["bugs_fixed"] == 5

    def test_multiple_check_files_uses_latest(self, tmp_path: Path):
        """Uses the last check_round file (sorted) for test count."""
        (tmp_path / "evolution_report.md").write_text("- 1 improvements completed\n")
        (tmp_path / "check_round_1.txt").write_text("10 passed\n")
        (tmp_path / "check_round_5.txt").write_text("42 passed\n")
        (tmp_path / "check_round_3.txt").write_text("30 passed\n")
        result = _parse_report_summary(tmp_path)
        assert result["tests_passing"] == 42

    def test_check_file_no_passed_pattern(self, tmp_path: Path):
        """Returns None for tests_passing when check file has no 'passed' line."""
        (tmp_path / "evolution_report.md").write_text("- 1 improvements completed\n")
        (tmp_path / "check_round_1.txt").write_text("FAILED - exit code 1\n")
        result = _parse_report_summary(tmp_path)
        assert result["tests_passing"] is None

    def test_large_numbers(self, tmp_path: Path):
        """Handles large numbers correctly."""
        (tmp_path / "evolution_report.md").write_text(
            "- 150 improvements completed\n- 42 bugs fixed\n"
        )
        (tmp_path / "check_round_99.txt").write_text("1234 passed in 60s\n")
        result = _parse_report_summary(tmp_path)
        assert result["improvements"] == 150
        assert result["bugs_fixed"] == 42
        assert result["tests_passing"] == 1234
