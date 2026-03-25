"""Extended tests for loop.py — _ensure_git, _git_commit, resume logic, _run_party_mode."""

import textwrap
from pathlib import Path
from unittest.mock import patch, MagicMock, call

import pytest

_real_import = __builtins__.__import__ if hasattr(__builtins__, '__import__') else __import__


def _make_import_blocker(*blocked_names):
    """Return a mock __import__ that blocks specific module names."""
    def mock_import(name, *args, **kwargs):
        if name in blocked_names:
            raise ImportError(f"mocked: {name}")
        return _real_import(name, *args, **kwargs)
    return mock_import


from loop import (
    _count_checked,
    _count_unchecked,
    _count_blocked,
    _get_current_improvement,
    _ensure_git,
    _git_commit,
    _run_party_mode,
    _run_monitored_subprocess,
    _save_subprocess_diagnostic,
    _generate_evolution_report,
    _parse_report_summary,
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
        assert _get_current_improvement(f, yolo=False) is None

    def test_all_needs_package_with_yolo(self, tmp_path: Path):
        f = tmp_path / "imp.md"
        f.write_text(textwrap.dedent("""\
            - [ ] [functional] [needs-package] blocked one
            - [ ] [performance] [needs-package] blocked two
        """))
        result = _get_current_improvement(f, yolo=True)
        assert result == "[functional] [needs-package] blocked one"


# ---------------------------------------------------------------------------
# _ensure_git
# ---------------------------------------------------------------------------

class TestEnsureGit:
    def test_not_a_git_repo(self, tmp_path: Path):
        """Exits with code 2 if not a git repo."""
        mock_result = MagicMock(returncode=1)
        with patch("loop.subprocess.run", return_value=mock_result):
            with pytest.raises(SystemExit) as exc:
                _ensure_git(tmp_path)
            assert exc.value.code == 2

    def test_clean_git_repo(self, tmp_path: Path):
        """No commit needed if working tree is clean."""
        git_check = MagicMock(returncode=0)  # is a git repo
        status_clean = MagicMock(returncode=0, stdout="")  # clean

        def side_effect(cmd, **kwargs):
            if cmd[1] == "rev-parse":
                return git_check
            if cmd[1] == "status":
                return status_clean
            return MagicMock(returncode=0)

        with patch("loop.subprocess.run", side_effect=side_effect):
            _ensure_git(tmp_path)  # should not raise

    def test_uncommitted_changes_triggers_commit(self, tmp_path: Path):
        """Uncommitted changes trigger git add + commit."""
        calls = []

        def side_effect(cmd, **kwargs):
            calls.append(cmd)
            if "rev-parse" in cmd:
                return MagicMock(returncode=0)
            if "status" in cmd:
                return MagicMock(returncode=0, stdout="M file.py\n")
            return MagicMock(returncode=0)

        with patch("loop.subprocess.run", side_effect=side_effect):
            _ensure_git(tmp_path)

        # Should have called git add -A and git commit
        cmd_strs = [" ".join(str(c) for c in cmd) for cmd in calls]
        assert any("add" in s for s in cmd_strs)
        assert any("commit" in s for s in cmd_strs)


# ---------------------------------------------------------------------------
# _git_commit
# ---------------------------------------------------------------------------

class TestGitCommit:
    def test_nothing_to_commit(self, tmp_path: Path):
        """If no staged changes, skip commit."""
        calls = []

        def side_effect(cmd, **kwargs):
            calls.append(cmd)
            if "diff" in cmd:
                return MagicMock(returncode=0)  # no diff = nothing to commit
            return MagicMock(returncode=0)

        with patch("loop.subprocess.run", side_effect=side_effect):
            _git_commit(tmp_path, "test msg")

        # Should NOT have called git commit
        cmd_strs = [" ".join(str(c) for c in cmd) for cmd in calls]
        assert not any("commit" in s and "diff" not in s for s in cmd_strs)

    def test_commit_and_push_success(self, tmp_path: Path):
        calls = []

        def side_effect(cmd, **kwargs):
            calls.append(cmd)
            if "diff" in cmd:
                return MagicMock(returncode=1)  # has changes
            if "push" in cmd:
                return MagicMock(returncode=0, stderr="")
            return MagicMock(returncode=0)

        with patch("loop.subprocess.run", side_effect=side_effect):
            _git_commit(tmp_path, "feat: test")

        cmd_strs = [" ".join(str(c) for c in cmd) for cmd in calls]
        assert any("commit" in s for s in cmd_strs)
        assert any("push" in s for s in cmd_strs)

    def test_commit_push_failure(self, tmp_path: Path):
        """Push failure should not crash, just report."""
        def side_effect(cmd, **kwargs):
            if "diff" in cmd:
                return MagicMock(returncode=1)
            if "push" in cmd:
                return MagicMock(returncode=1, stderr="remote rejected")
            return MagicMock(returncode=0)

        with patch("loop.subprocess.run", side_effect=side_effect):
            _git_commit(tmp_path, "feat: test")  # should not raise

    def test_commit_push_no_upstream_retries_with_set_upstream(self, tmp_path: Path):
        """Push with 'no upstream branch' error retries with -u origin <branch>."""
        calls = []

        def side_effect(cmd, **kwargs):
            calls.append(cmd)
            if "diff" in cmd:
                return MagicMock(returncode=1)
            if "branch" in cmd and "--show-current" in cmd:
                return MagicMock(returncode=0, stdout="evolve/forever-123\n")
            if "push" in cmd and "-u" in cmd:
                return MagicMock(returncode=0, stderr="")
            if "push" in cmd:
                return MagicMock(
                    returncode=1,
                    stderr="fatal: The current branch has no upstream branch.",
                )
            return MagicMock(returncode=0)

        with patch("loop.subprocess.run", side_effect=side_effect):
            _git_commit(tmp_path, "feat: test")

        cmd_strs = [" ".join(str(c) for c in cmd) for cmd in calls]
        assert any("-u" in s and "origin" in s for s in cmd_strs)


# ---------------------------------------------------------------------------
# _run_party_mode — early exits
# ---------------------------------------------------------------------------

class TestRunPartyMode:
    def test_no_agents_dir(self, tmp_path: Path):
        """Skips gracefully when no agents directory exists."""
        run_dir = tmp_path / "runs" / "session"
        run_dir.mkdir(parents=True)
        # Patch Path(__file__).parent to avoid finding real agents
        with patch("loop.Path") as mock_path:
            # Make project agents_dir.is_dir() return False
            mock_path_inst = MagicMock()
            mock_path_inst.is_dir.return_value = False
            mock_path_inst.parent.__truediv__.return_value.is_dir.return_value = False
            mock_path.return_value = mock_path_inst
            # Just call with a real tmp_path that has no agents/
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
# _generate_evolution_report
# ---------------------------------------------------------------------------

class TestGenerateEvolutionReport:
    def _setup_project(self, tmp_path: Path, improvements_text: str = "") -> tuple:
        project_dir = tmp_path / "proj"
        project_dir.mkdir()
        runs_dir = project_dir / "runs"
        runs_dir.mkdir()
        run_dir = runs_dir / "20260324_120000"
        run_dir.mkdir()
        imp_path = runs_dir / "improvements.md"
        imp_path.write_text(improvements_text or "# Improvements\n- [x] [functional] done one\n- [ ] [functional] pending\n")
        return project_dir, run_dir

    def test_basic_report_converged(self, tmp_path: Path):
        project_dir, run_dir = self._setup_project(tmp_path)
        with patch("loop.subprocess.run", return_value=MagicMock(returncode=1, stdout="")):
            _generate_evolution_report(project_dir, run_dir, max_rounds=10, final_round=3, converged=True)
        report = (run_dir / "evolution_report.md").read_text()
        assert "# Evolution Report" in report
        assert "CONVERGED" in report
        assert "3/10" in report
        assert "1 improvements completed" in report

    def test_basic_report_max_rounds(self, tmp_path: Path):
        project_dir, run_dir = self._setup_project(tmp_path)
        with patch("loop.subprocess.run", return_value=MagicMock(returncode=1, stdout="")):
            _generate_evolution_report(project_dir, run_dir, max_rounds=5, final_round=5, converged=False)
        report = (run_dir / "evolution_report.md").read_text()
        assert "MAX_ROUNDS" in report
        assert "5/5" in report
        assert "1 improvements remaining" in report

    def test_report_with_check_results(self, tmp_path: Path):
        project_dir, run_dir = self._setup_project(tmp_path)
        (run_dir / "check_round_1.txt").write_text("Round 1 post-fix check: PASS\n42 passed\n")
        with patch("loop.subprocess.run", return_value=MagicMock(returncode=1, stdout="")):
            _generate_evolution_report(project_dir, run_dir, max_rounds=10, final_round=1, converged=True)
        report = (run_dir / "evolution_report.md").read_text()
        assert "42 passed" in report

    def test_report_with_conversation_log(self, tmp_path: Path):
        project_dir, run_dir = self._setup_project(tmp_path)
        (run_dir / "conversation_loop_1.md").write_text(
            "feat(parser): add validation\nEdit → src/parser.py\nWrite → src/validator.py\n"
        )
        with patch("loop.subprocess.run", return_value=MagicMock(returncode=1, stdout="")):
            _generate_evolution_report(project_dir, run_dir, max_rounds=10, final_round=1, converged=True)
        report = (run_dir / "evolution_report.md").read_text()
        assert "feat(parser): add validation" in report
        assert "src/parser.py" in report

    def test_report_no_rounds(self, tmp_path: Path):
        """Report with 0 final_round shouldn't crash."""
        project_dir, run_dir = self._setup_project(tmp_path)
        with patch("loop.subprocess.run", return_value=MagicMock(returncode=1, stdout="")):
            _generate_evolution_report(project_dir, run_dir, max_rounds=10, final_round=0, converged=False)
        report = (run_dir / "evolution_report.md").read_text()
        assert "# Evolution Report" in report

    def test_report_arrow_format_test_counts(self, tmp_path: Path):
        """Tests column shows arrow format (prev→current) when counts change."""
        project_dir, run_dir = self._setup_project(tmp_path)
        (run_dir / "check_round_1.txt").write_text("Round 1 PASS\n42 passed\n")
        (run_dir / "check_round_2.txt").write_text("Round 2 PASS\n45 passed\n")
        with patch("loop.subprocess.run", return_value=MagicMock(returncode=1, stdout="")):
            _generate_evolution_report(project_dir, run_dir, max_rounds=10, final_round=2, converged=True)
        report = (run_dir / "evolution_report.md").read_text()
        # Round 1 has no previous, shows "42 passed"
        assert "42 passed" in report
        # Round 2 should show arrow format "42→45"
        assert "42\u219245" in report

    def test_report_no_arrow_when_counts_unchanged(self, tmp_path: Path):
        """Tests column shows plain format when counts don't change between rounds."""
        project_dir, run_dir = self._setup_project(tmp_path)
        (run_dir / "check_round_1.txt").write_text("Round 1 PASS\n42 passed\n")
        (run_dir / "check_round_2.txt").write_text("Round 2 PASS\n42 passed\n")
        with patch("loop.subprocess.run", return_value=MagicMock(returncode=1, stdout="")):
            _generate_evolution_report(project_dir, run_dir, max_rounds=10, final_round=2, converged=True)
        report = (run_dir / "evolution_report.md").read_text()
        # Both rounds should show "42 passed" (no arrow since unchanged)
        assert report.count("42 passed") == 2
        assert "\u2192" not in report

    def test_report_deduplicates_files(self, tmp_path: Path):
        """Files changed are deduplicated per round."""
        project_dir, run_dir = self._setup_project(tmp_path)
        (run_dir / "conversation_loop_1.md").write_text(
            "Edit → src/foo.py\nEdit → src/foo.py\nEdit → src/bar.py\n"
        )
        with patch("loop.subprocess.run", return_value=MagicMock(returncode=1, stdout="")):
            _generate_evolution_report(project_dir, run_dir, max_rounds=10, final_round=1, converged=True)
        report = (run_dir / "evolution_report.md").read_text()
        # src/foo.py should appear only once in the files column
        # Find the timeline row for round 1
        for line in report.splitlines():
            if line.startswith("| 1 |"):
                assert line.count("src/foo.py") == 1
                assert "src/bar.py" in line
                break
        else:
            raise AssertionError("Timeline row for round 1 not found")  # pragma: no cover

    def test_detect_last_round_malformed_name(self, tmp_path: Path):
        """Malformed conversation file name doesn't crash."""
        runs = tmp_path / "runs"
        session = runs / "20260101_000000"
        session.mkdir(parents=True)
        (session / "conversation_loop_abc.md").write_text("bad name")

        convos = sorted(session.glob("conversation_loop_*.md"))
        last = convos[-1].stem
        try:
            last_round = int(last.rsplit("_", 1)[1])
        except (ValueError, IndexError):
            last_round = None
        assert last_round is None


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
        cmd = [self._python, "-c", "print('hello')"]
        returncode, output, stalled = _run_monitored_subprocess(
            cmd, str(tmp_path), self.ui, round_num=1, watchdog_timeout=10,
        )
        assert returncode == 0
        assert "hello" in output
        assert stalled is False

    def test_failing_subprocess(self, tmp_path: Path):
        """A subprocess that exits with error returns non-zero code."""
        cmd = [self._python, "-c", "import sys; print('boom'); sys.exit(42)"]
        returncode, output, stalled = _run_monitored_subprocess(
            cmd, str(tmp_path), self.ui, round_num=1, watchdog_timeout=10,
        )
        assert returncode == 42
        assert "boom" in output
        assert stalled is False

    def test_stalled_subprocess_killed(self, tmp_path: Path):
        """A subprocess producing no output is killed by the watchdog."""
        # sleep for 60s but watchdog is 2s — should be killed quickly
        cmd = [self._python, "-c", "import time; time.sleep(60)"]
        returncode, output, stalled = _run_monitored_subprocess(
            cmd, str(tmp_path), self.ui, round_num=1, watchdog_timeout=2,
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
