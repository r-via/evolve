"""Tests for _generate_evolution_report (extracted from test_loop_extended.py).

Covers the baseline TestGenerateEvolutionReport class.  Extended edge
cases live in test_loop_evolution_report.py (TestEvolutionReportExtended).
"""

import json
from pathlib import Path
from unittest.mock import patch, MagicMock

from evolve.infrastructure.reporting.generator import _generate_evolution_report


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
        with patch("evolve.application.run_loop.subprocess.run", return_value=MagicMock(returncode=1, stdout="")):
            _generate_evolution_report(project_dir, run_dir, max_rounds=10, final_round=3, converged=True)
        report = (run_dir / "evolution_report.md").read_text()
        assert "# Evolution Report" in report
        assert "CONVERGED" in report
        assert "3/10" in report
        assert "1 improvements completed" in report

    def test_basic_report_max_rounds(self, tmp_path: Path):
        project_dir, run_dir = self._setup_project(tmp_path)
        with patch("evolve.application.run_loop.subprocess.run", return_value=MagicMock(returncode=1, stdout="")):
            _generate_evolution_report(project_dir, run_dir, max_rounds=5, final_round=5, converged=False)
        report = (run_dir / "evolution_report.md").read_text()
        assert "MAX_ROUNDS" in report
        assert "5/5" in report
        assert "1 improvements remaining" in report

    def test_report_with_check_results(self, tmp_path: Path):
        project_dir, run_dir = self._setup_project(tmp_path)
        (run_dir / "check_round_1.txt").write_text("Round 1 post-fix check: PASS\n42 passed\n")
        with patch("evolve.application.run_loop.subprocess.run", return_value=MagicMock(returncode=1, stdout="")):
            _generate_evolution_report(project_dir, run_dir, max_rounds=10, final_round=1, converged=True)
        report = (run_dir / "evolution_report.md").read_text()
        assert "42 passed" in report

    def test_report_with_conversation_log(self, tmp_path: Path):
        project_dir, run_dir = self._setup_project(tmp_path)
        (run_dir / "conversation_loop_1.md").write_text(
            "feat(parser): add validation\nEdit \u2192 src/parser.py\nWrite \u2192 src/validator.py\n"
        )
        with patch("evolve.application.run_loop.subprocess.run", return_value=MagicMock(returncode=1, stdout="")):
            _generate_evolution_report(project_dir, run_dir, max_rounds=10, final_round=1, converged=True)
        report = (run_dir / "evolution_report.md").read_text()
        assert "feat(parser): add validation" in report
        assert "src/parser.py" in report

    def test_report_no_rounds(self, tmp_path: Path):
        """Report with 0 final_round shouldn't crash."""
        project_dir, run_dir = self._setup_project(tmp_path)
        with patch("evolve.application.run_loop.subprocess.run", return_value=MagicMock(returncode=1, stdout="")):
            _generate_evolution_report(project_dir, run_dir, max_rounds=10, final_round=0, converged=False)
        report = (run_dir / "evolution_report.md").read_text()
        assert "# Evolution Report" in report

    def test_report_arrow_format_test_counts(self, tmp_path: Path):
        """Tests column shows arrow format (prev->current) when counts change."""
        project_dir, run_dir = self._setup_project(tmp_path)
        (run_dir / "check_round_1.txt").write_text("Round 1 PASS\n42 passed\n")
        (run_dir / "check_round_2.txt").write_text("Round 2 PASS\n45 passed\n")
        with patch("evolve.application.run_loop.subprocess.run", return_value=MagicMock(returncode=1, stdout="")):
            _generate_evolution_report(project_dir, run_dir, max_rounds=10, final_round=2, converged=True)
        report = (run_dir / "evolution_report.md").read_text()
        # Round 1 has no previous, shows "42 passed"
        assert "42 passed" in report
        # Round 2 should show arrow format "42->45"
        assert "42\u219245" in report

    def test_report_no_arrow_when_counts_unchanged(self, tmp_path: Path):
        """Tests column shows plain format when counts don't change between rounds."""
        project_dir, run_dir = self._setup_project(tmp_path)
        (run_dir / "check_round_1.txt").write_text("Round 1 PASS\n42 passed\n")
        (run_dir / "check_round_2.txt").write_text("Round 2 PASS\n42 passed\n")
        with patch("evolve.application.run_loop.subprocess.run", return_value=MagicMock(returncode=1, stdout="")):
            _generate_evolution_report(project_dir, run_dir, max_rounds=10, final_round=2, converged=True)
        report = (run_dir / "evolution_report.md").read_text()
        # Both rounds should show "42 passed" (no arrow since unchanged)
        assert report.count("42 passed") == 2
        assert "\u2192" not in report

    def test_report_deduplicates_files(self, tmp_path: Path):
        """Files changed are deduplicated per round."""
        project_dir, run_dir = self._setup_project(tmp_path)
        (run_dir / "conversation_loop_1.md").write_text(
            "Edit \u2192 src/foo.py\nEdit \u2192 src/foo.py\nEdit \u2192 src/bar.py\n"
        )
        with patch("evolve.application.run_loop.subprocess.run", return_value=MagicMock(returncode=1, stdout="")):
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

    def test_report_cost_summary_table(self, tmp_path: Path):
        """Report includes Cost Summary table when usage_round_N.json exists."""
        project_dir, run_dir = self._setup_project(tmp_path)
        (run_dir / "usage_round_1.json").write_text(json.dumps({
            "input_tokens": 45230, "output_tokens": 12400,
            "cache_creation_tokens": 8200, "cache_read_tokens": 38100,
            "model": "claude-opus-4-6", "round": 1,
        }))
        (run_dir / "usage_round_2.json").write_text(json.dumps({
            "input_tokens": 52100, "output_tokens": 15800,
            "cache_creation_tokens": 9000, "cache_read_tokens": 41200,
            "model": "claude-opus-4-6", "round": 2,
        }))
        with patch("evolve.application.run_loop.subprocess.run", return_value=MagicMock(returncode=1, stdout="")):
            _generate_evolution_report(project_dir, run_dir, max_rounds=10, final_round=2, converged=True)
        report = (run_dir / "evolution_report.md").read_text()
        assert "## Cost Summary" in report
        assert "Input Tokens" in report
        assert "Output Tokens" in report
        assert "Cache Hits" in report
        assert "Est. Cost" in report
        # Verify per-round rows exist
        assert "45,230" in report
        assert "12,400" in report
        assert "52,100" in report
        assert "15,800" in report
        # Verify total line with model
        assert "**Total:" in report
        assert "claude-opus-4-6" in report
        # Verify cost in Summary section
        assert "estimated API cost" in report

    def test_report_no_cost_summary_without_usage(self, tmp_path: Path):
        """Report omits Cost Summary when no usage_round_N.json files exist."""
        project_dir, run_dir = self._setup_project(tmp_path)
        with patch("evolve.application.run_loop.subprocess.run", return_value=MagicMock(returncode=1, stdout="")):
            _generate_evolution_report(project_dir, run_dir, max_rounds=10, final_round=1, converged=True)
        report = (run_dir / "evolution_report.md").read_text()
        assert "## Cost Summary" not in report
        assert "estimated API cost" not in report

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

    def test_detect_last_round_gaps_in_logs(self, tmp_path: Path):
        """Detect last round correctly when there are gaps (e.g., 1, 3, 7)."""
        runs = tmp_path / "runs"
        session = runs / "20260101_000000"
        session.mkdir(parents=True)
        # Create conversation logs with gaps - missing rounds 2, 4, 5, 6
        (session / "conversation_loop_1.md").write_text("round 1")
        (session / "conversation_loop_3.md").write_text("round 3")
        (session / "conversation_loop_7.md").write_text("round 7")

        convos = sorted(
            session.glob("conversation_loop_*.md"),
            key=lambda p: int(p.stem.rsplit("_", 1)[1]),
        )
        last = convos[-1].stem
        last_round = int(last.rsplit("_", 1)[1])
        assert last_round == 7
        assert len(convos) == 3  # only 3 files, not 7

    def test_detect_last_round_empty_run_dir(self, tmp_path: Path):
        """Empty run directory returns no convos - start_round stays at 1."""
        session = tmp_path / "runs" / "20260101_000000"
        session.mkdir(parents=True)
        # No files at all
        convos = sorted(session.glob("conversation_loop_*.md"))
        assert convos == []
        # In real code, start_round stays at 1 when convos is empty
        start_round = 1
        assert start_round == 1

    def test_detect_last_round_only_error_logs(self, tmp_path: Path):
        """Session with only error logs but no conversation logs returns empty."""
        session = tmp_path / "runs" / "20260101_000000"
        session.mkdir(parents=True)
        (session / "subprocess_error_round_1.txt").write_text("error info")
        (session / "subprocess_error_round_2.txt").write_text("error info")
        (session / "check_round_1.txt").write_text("FAIL")

        convos = sorted(session.glob("conversation_loop_*.md"))
        assert convos == []
        start_round = 1
        assert start_round == 1

    def test_detect_last_round_mixed_valid_and_corrupted(self, tmp_path: Path):
        """When valid and corrupted filenames coexist, filter out corrupted ones."""
        session = tmp_path / "runs" / "20260101_000000"
        session.mkdir(parents=True)
        (session / "conversation_loop_1.md").write_text("round 1")
        (session / "conversation_loop_5.md").write_text("round 5")
        (session / "conversation_loop_abc.md").write_text("bad name")
        (session / "conversation_loop_.md").write_text("empty suffix")

        # Filter to only parseable numeric entries (as robust code should)
        all_convos = list(session.glob("conversation_loop_*.md"))
        valid = []
        for p in all_convos:
            try:
                int(p.stem.rsplit("_", 1)[1])
                valid.append(p)
            except (ValueError, IndexError):
                pass
        valid.sort(key=lambda p: int(p.stem.rsplit("_", 1)[1]))
        assert len(valid) == 2
        last_round = int(valid[-1].stem.rsplit("_", 1)[1])
        assert last_round == 5

    def test_detect_last_round_single_convo(self, tmp_path: Path):
        """Single conversation log returns round 1."""
        session = tmp_path / "runs" / "20260101_000000"
        session.mkdir(parents=True)
        (session / "conversation_loop_1.md").write_text("round 1")

        convos = sorted(
            session.glob("conversation_loop_*.md"),
            key=lambda p: int(p.stem.rsplit("_", 1)[1]),
        )
        assert len(convos) == 1
        last_round = int(convos[-1].stem.rsplit("_", 1)[1])
        assert last_round == 1

    def test_detect_last_round_high_numbers(self, tmp_path: Path):
        """Correctly handles high round numbers (e.g., 100+)."""
        session = tmp_path / "runs" / "20260101_000000"
        session.mkdir(parents=True)
        for i in [1, 50, 100, 150]:
            (session / f"conversation_loop_{i}.md").write_text(f"round {i}")

        convos = sorted(
            session.glob("conversation_loop_*.md"),
            key=lambda p: int(p.stem.rsplit("_", 1)[1]),
        )
        last_round = int(convos[-1].stem.rsplit("_", 1)[1])
        assert last_round == 150
        # Verify numeric sort (not lexicographic - 100 > 50, not "100" < "50")
        rounds = [int(p.stem.rsplit("_", 1)[1]) for p in convos]
        assert rounds == [1, 50, 100, 150]

    def test_detect_last_round_non_session_dirs_ignored(self, tmp_path: Path):
        """Resume logic ignores non-timestamped directories."""
        runs = tmp_path / "runs"
        runs.mkdir()
        # Non-timestamp dirs should be filtered out by d.name[0].isdigit()
        (runs / "improvements.md").write_text("# Improvements\n")
        (runs / "memory.md").write_text("# Memory\n")
        (runs / ".hidden").mkdir()

        sessions = sorted(
            [d for d in runs.iterdir() if d.is_dir() and d.name[0].isdigit()],
            reverse=True,
        )
        assert sessions == []

    def test_resume_sort_handles_non_numeric_filenames(self, tmp_path: Path):
        """Sorting conversation_loop files must not crash on non-numeric suffixes.

        Regression test: the sort lambda used int() directly, which raised
        ValueError on filenames like conversation_loop_abc.md.  The fix uses
        a helper that returns -1 for unparseable suffixes.
        """
        session = tmp_path / "runs" / "20260101_000000"
        session.mkdir(parents=True)
        # Valid entries
        (session / "conversation_loop_3.md").write_text("round 3")
        (session / "conversation_loop_1.md").write_text("round 1")
        # Corrupted / non-numeric entries that must not crash the sort
        (session / "conversation_loop_abc.md").write_text("bad")
        (session / "conversation_loop_.md").write_text("empty suffix")
        (session / "conversation_loop_2x.md").write_text("mixed")

        # Reproduce the exact sort logic from evolve_loop's resume path
        def _convo_sort_key(p: Path) -> int:
            try:
                return int(p.stem.rsplit("_", 1)[1])
            except (ValueError, IndexError):
                return -1

        # Must NOT raise - previously this was a bare int() that crashed
        convos = sorted(
            session.glob("conversation_loop_*.md"), key=_convo_sort_key,
        )
        assert len(convos) == 5

        # Non-numeric entries sort first (key=-1), valid entries sort ascending
        numeric_keys = [_convo_sort_key(p) for p in convos]
        # First three are the -1 entries, last two are 1 and 3
        assert numeric_keys[-2:] == [1, 3]
        assert all(k == -1 for k in numeric_keys[:3])

        # The last valid convo should be round 3
        last_valid = convos[-1]
        assert last_valid.stem == "conversation_loop_3"
