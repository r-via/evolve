"""Extended tests for tui.py — PlainTUI and RichTUI method coverage."""

from unittest.mock import patch

from tui import PlainTUI, RichTUI, JsonTUI, _has_rich, get_tui
import tui as _tui_mod


class TestPlainTUIExtended:
    """Cover all PlainTUI methods not yet tested."""

    @classmethod
    def setup_class(cls):
        cls.ui = PlainTUI()

    def test_no_check(self, capsys):
        self.ui.no_check()
        out = capsys.readouterr().out
        assert "no check" in out.lower() or "manual" in out.lower()

    def test_agent_working(self, capsys):
        self.ui.agent_working()
        # just should not crash

    def test_agent_tool(self, capsys):
        self.ui.agent_tool("Bash", "ls -la")
        out = capsys.readouterr().out
        assert "Bash" in out

    def test_agent_done(self, capsys):
        self.ui.agent_done(5, "/tmp/log.md")
        out = capsys.readouterr().out
        assert "5" in out

    def test_agent_text(self, capsys):
        self.ui.agent_text("hello world")
        # should not crash

    def test_git_status_pushed(self, capsys):
        self.ui.git_status("feat: test", pushed=True)
        out = capsys.readouterr().out
        assert "feat: test" in out

    def test_git_status_push_failed(self, capsys):
        self.ui.git_status("feat: test", pushed=False, error="rejected")
        out = capsys.readouterr().out
        assert "feat: test" in out

    def test_git_status_no_changes(self, capsys):
        self.ui.git_status("chore: nothing", pushed=None)
        out = capsys.readouterr().out
        assert "no changes" in out.lower()

    def test_max_rounds(self, capsys):
        self.ui.max_rounds(10, 7, 3)
        out = capsys.readouterr().out
        assert "10" in out

    def test_round_failed(self, capsys):
        self.ui.round_failed(3, 1)
        out = capsys.readouterr().out
        assert "3" in out

    def test_no_progress(self, capsys):
        self.ui.no_progress()
        # should not crash

    def test_run_dir_info(self, capsys):
        self.ui.run_dir_info("/tmp/runs/session")
        out = capsys.readouterr().out
        assert "/tmp/runs/session" in out

    def test_party_mode(self, capsys):
        self.ui.party_mode()
        # should not crash

    def test_warn(self, capsys):
        self.ui.warn("something bad")
        out = capsys.readouterr().out
        assert "something bad" in out

    def test_error(self, capsys):
        self.ui.error("fatal error")
        out = capsys.readouterr().out
        assert "fatal error" in out

    def test_info(self, capsys):
        self.ui.info("info message")
        out = capsys.readouterr().out
        assert "info message" in out

    def test_party_results_with_files(self, capsys):
        self.ui.party_results("/tmp/proposal.md", "/tmp/report.md")
        out = capsys.readouterr().out
        assert "proposal" in out.lower() or "/tmp" in out

    def test_party_results_no_files(self, capsys):
        self.ui.party_results(None, None)
        # should not crash

    def test_uncommitted(self, capsys):
        self.ui.uncommitted()
        # should not crash

    def test_sdk_rate_limited(self, capsys):
        self.ui.sdk_rate_limited(60, 1, 5)
        out = capsys.readouterr().out
        assert "60" in out or "rate" in out.lower()

    def test_status_no_improvements(self, capsys):
        self.ui.status_no_improvements()
        # should not crash

    def test_round_header_no_target(self, capsys):
        self.ui.round_header(1, 10)
        out = capsys.readouterr().out
        assert "ROUND 1/10" in out

    def test_check_result_running(self, capsys):
        self.ui.check_result("check", "pytest", passed=None)
        out = capsys.readouterr().out
        assert "pytest" in out

    def test_completion_summary_converged(self, capsys):
        self.ui.completion_summary(
            status="CONVERGED", round_num=8, duration_s=754.0,
            improvements=6, bugs_fixed=2, tests_passing=47,
            report_path="runs/s1/evolution_report.md",
        )
        out = capsys.readouterr().out
        assert "CONVERGED" in out
        assert "8 rounds" in out
        assert "6 improvements" in out
        assert "2 bugs fixed" in out
        assert "47 tests passing" in out
        assert "evolution_report.md" in out

    def test_completion_summary_max_rounds(self, capsys):
        self.ui.completion_summary(
            status="MAX_ROUNDS", round_num=20, duration_s=60.0,
            improvements=3, bugs_fixed=0, tests_passing=None,
            report_path="runs/s1/evolution_report.md",
        )
        out = capsys.readouterr().out
        assert "MAX_ROUNDS" in out
        assert "3 improvements" in out
        # tests_passing=None should not appear
        assert "tests passing" not in out

    def test_completion_summary_duration_format(self, capsys):
        self.ui.completion_summary(
            status="CONVERGED", round_num=1, duration_s=125.0,
            improvements=1, bugs_fixed=0, tests_passing=10,
            report_path="report.md",
        )
        out = capsys.readouterr().out
        assert "2m 05s" in out


class TestRichTUIExtended:
    """Cover RichTUI methods if rich is available."""

    @classmethod
    def setup_class(cls):
        cls._rich = _has_rich()
        cls.ui = RichTUI() if cls._rich else None

    def test_rich_available(self):
        """Just verify we can check for rich."""
        assert isinstance(self._rich, bool)

    def test_rich_tui_instantiation(self):
        if self._rich:
            assert self.ui is not None

    def test_rich_round_header(self, capsys):
        if self._rich:
            self.ui.round_header(1, 10, target="test", checked=3, total=5)

    def test_rich_all_methods_callable(self):
        """Verify all RichTUI methods can be called without crashing."""
        if not self._rich:
            return
        ui = self.ui
        ui.round_header(1, 10, target="test", checked=3, total=5)
        ui.blocked_message(2)
        ui.check_result("check", "pytest", passed=True)
        ui.check_result("verify", "pytest", passed=False)
        ui.check_result("check", "pytest", timeout=True)
        ui.check_result("check", "pytest", passed=None)
        ui.no_check()
        ui.agent_working()
        ui.agent_tool("Bash", "ls")
        ui.agent_done(5, "/tmp/log.md")
        ui.agent_text("hello")
        ui.git_status("feat: test", pushed=True)
        ui.git_status("feat: test", pushed=False, error="err")
        ui.git_status("feat: test", pushed=None)
        ui.progress_summary(5, 3)
        ui.converged(5, "done")
        ui.max_rounds(10, 7, 3)
        ui.round_failed(3, 1)
        ui.no_progress()
        ui.run_dir_info("/tmp/run")
        ui.party_mode()
        ui.warn("warning")
        ui.error("error")
        ui.info("info")
        ui.party_results("/tmp/p.md", "/tmp/r.md")
        ui.party_results(None, None)
        ui.uncommitted()
        ui.sdk_rate_limited(60, 1, 5)
        ui.status_header("/tmp/proj", True)
        ui.status_improvements(5, 2, 1)
        ui.status_no_improvements()
        ui.status_memory(3)
        ui.status_session("20260101", 5, 3, True, "done")
        ui.status_session("20260101", 5, 3, False)
        ui.status_flush()

    def test_rich_history_empty(self, capsys):
        if not self._rich:
            return
        self.ui.history_empty("/tmp/proj")

    def test_rich_history_table(self, capsys):
        if not self._rich:
            return
        rows = [
            {"name": "20260101_000000", "rounds": "3/10", "status": "CONVERGED",
             "checked": 3, "unchecked": 0},
            {"name": "20260102_000000", "rounds": "5/10", "status": "IN_PROGRESS",
             "checked": 2, "unchecked": 3},
        ]
        self.ui.history_table("/tmp/proj", rows, 2, 8, 5)

    def test_rich_completion_summary(self, capsys):
        if not self._rich:
            return
        self.ui.completion_summary(
            status="CONVERGED", round_num=8, duration_s=754.0,
            improvements=6, bugs_fixed=2, tests_passing=47,
            report_path="runs/s1/evolution_report.md",
        )
        out = capsys.readouterr().out
        assert "CONVERGED" in out
        assert "8 rounds" in out
        assert "6 improvements" in out
        assert "evolution_report.md" in out

    def test_rich_completion_summary_no_tests(self, capsys):
        if not self._rich:
            return
        self.ui.completion_summary(
            status="MAX_ROUNDS", round_num=10, duration_s=60.0,
            improvements=2, bugs_fixed=0, tests_passing=None,
            report_path="report.md",
        )
        out = capsys.readouterr().out
        assert "MAX_ROUNDS" in out
        assert "tests passing" not in out


class TestPlainTUIHistoryAndStatus:
    """Cover PlainTUI history and status_session edge cases."""

    @classmethod
    def setup_class(cls):
        cls.ui = PlainTUI()

    def test_status_session_converged_with_reason(self, capsys):
        self.ui.status_session("20260101_000000", 5, 3, converged=True, reason="All done")
        out = capsys.readouterr().out
        assert "YES" in out
        assert "All done" in out

    def test_status_session_converged_no_reason(self, capsys):
        self.ui.status_session("20260101_000000", 5, 3, converged=True, reason="")
        out = capsys.readouterr().out
        assert "YES" in out

    def test_history_empty(self, capsys):
        self.ui.history_empty("/tmp/proj")
        out = capsys.readouterr().out
        assert "/tmp/proj" in out
        assert "No evolution history" in out

    def test_history_table(self, capsys):
        rows = [
            {"name": "20260101_000000", "rounds": "3/10", "status": "CONVERGED",
             "checked": 3, "unchecked": 0},
            {"name": "20260102_000000", "rounds": "5/10", "status": "IN_PROGRESS",
             "checked": 2, "unchecked": 3},
        ]
        self.ui.history_table("/tmp/proj", rows, 2, 8, 5)
        out = capsys.readouterr().out
        assert "Evolution History" in out
        assert "20260101_000000" in out
        assert "20260102_000000" in out
        assert "CONVERGED" in out
        assert "2 sessions" in out
        assert "8 rounds" in out
        assert "5 improvements" in out

    def test_status_memory_zero(self, capsys):
        self.ui.status_memory(0)
        out = capsys.readouterr().out
        assert "empty" in out.lower()


class TestHasRichImportError:
    """Test _has_rich when rich is not importable."""

    def test_has_rich_returns_false_on_import_error(self):
        with patch.dict("sys.modules", {"rich": None}):
            # Force re-evaluation by calling the function directly
            # Since _has_rich imports rich each call, patching sys.modules works
            from tui import _has_rich as check_rich
            result = check_rich()
            assert result is False

    def test_get_tui_falls_back_to_plain(self):
        """get_tui returns PlainTUI when rich is unavailable and json is off."""
        old_json = _tui_mod._use_json
        try:
            _tui_mod._use_json = False
            with patch("tui._has_rich", return_value=False):
                ui = get_tui()
                assert isinstance(ui, PlainTUI)
        finally:
            _tui_mod._use_json = old_json


class TestJsonTUIExtended:
    """Cover remaining JsonTUI methods not tested in test_tui.py."""

    @classmethod
    def setup_class(cls):
        cls.ui = JsonTUI()

    def _parse_line(self, capsys) -> dict:
        import json
        out = capsys.readouterr().out.strip()
        lines = [l for l in out.splitlines() if l.strip()]
        assert len(lines) >= 1
        return json.loads(lines[-1])

    def test_agent_text(self, capsys):
        self.ui.agent_text("hello world")
        obj = self._parse_line(capsys)
        assert obj["type"] == "agent_text"
        assert obj["text"] == "hello world"

    def test_no_progress(self, capsys):
        self.ui.no_progress()
        obj = self._parse_line(capsys)
        assert obj["type"] == "no_progress"

    def test_run_dir_info(self, capsys):
        self.ui.run_dir_info("/tmp/run")
        obj = self._parse_line(capsys)
        assert obj["type"] == "run_dir_info"
        assert obj["run_dir"] == "/tmp/run"

    def test_info(self, capsys):
        self.ui.info("info msg")
        obj = self._parse_line(capsys)
        assert obj["type"] == "info"
        assert obj["message"] == "info msg"

    def test_status_no_improvements(self, capsys):
        self.ui.status_no_improvements()
        obj = self._parse_line(capsys)
        assert obj["type"] == "status_no_improvements"

    def test_history_empty(self, capsys):
        self.ui.history_empty("/tmp/proj")
        obj = self._parse_line(capsys)
        assert obj["type"] == "history_empty"
        assert obj["project_dir"] == "/tmp/proj"

    def test_history_table(self, capsys):
        rows = [{"name": "s1", "rounds": "3/10", "status": "CONVERGED",
                 "checked": 3, "unchecked": 0}]
        self.ui.history_table("/tmp/proj", rows, 1, 3, 3)
        obj = self._parse_line(capsys)
        assert obj["type"] == "history"
        assert obj["num_sessions"] == 1

    def test_completion_summary(self, capsys):
        self.ui.completion_summary(
            status="CONVERGED", round_num=8, duration_s=754.0,
            improvements=6, bugs_fixed=2, tests_passing=47,
            report_path="runs/20260325/evolution_report.md",
        )
        obj = self._parse_line(capsys)
        assert obj["type"] == "completion_summary"
        assert obj["status"] == "CONVERGED"
        assert obj["round"] == 8
        assert obj["improvements"] == 6
        assert obj["bugs_fixed"] == 2
        assert obj["tests_passing"] == 47
        assert "evolution_report.md" in obj["report_path"]

    def test_completion_summary_max_rounds(self, capsys):
        self.ui.completion_summary(
            status="MAX_ROUNDS", round_num=20, duration_s=120.0,
            improvements=3, bugs_fixed=1, tests_passing=None,
            report_path="runs/s1/evolution_report.md",
        )
        obj = self._parse_line(capsys)
        assert obj["status"] == "MAX_ROUNDS"
        assert obj["tests_passing"] is None


# ---------------------------------------------------------------------------
# Completion summary edge cases — all three TUI implementations
# ---------------------------------------------------------------------------

class TestCompletionSummaryEdgeCases:
    """Test completion_summary across RichTUI, PlainTUI, and JsonTUI with
    edge cases: zero improvements, zero bugs, very long/short durations,
    tests_passing=None, and large numbers."""

    # -- PlainTUI -----------------------------------------------------------

    def test_plain_zero_improvements(self, capsys):
        ui = PlainTUI()
        ui.completion_summary(
            status="CONVERGED", round_num=1, duration_s=5.0,
            improvements=0, bugs_fixed=0, tests_passing=0,
            report_path="runs/s1/evolution_report.md",
        )
        out = capsys.readouterr().out
        assert "0 improvements" in out
        assert "0 bugs fixed" in out
        assert "0 tests passing" in out

    def test_plain_very_long_duration(self, capsys):
        ui = PlainTUI()
        # 3 hours 25 minutes 59 seconds = 12359 seconds
        ui.completion_summary(
            status="CONVERGED", round_num=100, duration_s=12359.0,
            improvements=50, bugs_fixed=10, tests_passing=500,
            report_path="runs/s1/evolution_report.md",
        )
        out = capsys.readouterr().out
        assert "205m 59s" in out
        assert "100 rounds" in out
        assert "50 improvements" in out

    def test_plain_short_duration_under_60s(self, capsys):
        ui = PlainTUI()
        ui.completion_summary(
            status="CONVERGED", round_num=1, duration_s=42.0,
            improvements=1, bugs_fixed=0, tests_passing=10,
            report_path="report.md",
        )
        out = capsys.readouterr().out
        # Under 60s should show just seconds, no "0m"
        assert "42s" in out
        assert "0m" not in out

    def test_plain_zero_duration(self, capsys):
        ui = PlainTUI()
        ui.completion_summary(
            status="CONVERGED", round_num=1, duration_s=0.0,
            improvements=0, bugs_fixed=0, tests_passing=None,
            report_path="report.md",
        )
        out = capsys.readouterr().out
        assert "0s" in out
        assert "tests passing" not in out

    def test_plain_tests_passing_none_omitted(self, capsys):
        ui = PlainTUI()
        ui.completion_summary(
            status="MAX_ROUNDS", round_num=5, duration_s=60.0,
            improvements=2, bugs_fixed=1, tests_passing=None,
            report_path="report.md",
        )
        out = capsys.readouterr().out
        assert "tests passing" not in out
        assert "2 improvements" in out
        assert "1 bugs fixed" in out

    def test_plain_report_path_displayed(self, capsys):
        ui = PlainTUI()
        ui.completion_summary(
            status="CONVERGED", round_num=1, duration_s=10.0,
            improvements=1, bugs_fixed=0, tests_passing=5,
            report_path="/very/long/path/to/runs/20260325/evolution_report.md",
        )
        out = capsys.readouterr().out
        assert "/very/long/path/to/runs/20260325/evolution_report.md" in out

    # -- RichTUI ------------------------------------------------------------

    def test_rich_zero_improvements(self, capsys):
        if not _has_rich():
            return
        ui = RichTUI()
        ui.completion_summary(
            status="CONVERGED", round_num=1, duration_s=5.0,
            improvements=0, bugs_fixed=0, tests_passing=0,
            report_path="runs/s1/evolution_report.md",
        )
        out = capsys.readouterr().out
        assert "0 improvements" in out
        assert "0 bugs fixed" in out
        assert "0 tests passing" in out

    def test_rich_very_long_duration(self, capsys):
        if not _has_rich():
            return
        ui = RichTUI()
        ui.completion_summary(
            status="CONVERGED", round_num=100, duration_s=12359.0,
            improvements=50, bugs_fixed=10, tests_passing=500,
            report_path="runs/s1/evolution_report.md",
        )
        out = capsys.readouterr().out
        assert "205m 59s" in out
        assert "100 rounds" in out

    def test_rich_short_duration_under_60s(self, capsys):
        if not _has_rich():
            return
        ui = RichTUI()
        ui.completion_summary(
            status="CONVERGED", round_num=1, duration_s=42.0,
            improvements=1, bugs_fixed=0, tests_passing=10,
            report_path="report.md",
        )
        out = capsys.readouterr().out
        # Strip ANSI escape codes before substring checks — with
        # force_terminal=True, Rich emits sequences like \x1b[0m which
        # contain the literal substring "0m" and would false-match the
        # "no minutes" assertion below.
        import re
        stripped = re.sub(r"\x1b\[[0-9;]*m", "", out)
        assert "42s" in stripped
        assert "0m" not in stripped

    def test_rich_zero_duration(self, capsys):
        if not _has_rich():
            return
        ui = RichTUI()
        ui.completion_summary(
            status="CONVERGED", round_num=1, duration_s=0.0,
            improvements=0, bugs_fixed=0, tests_passing=None,
            report_path="report.md",
        )
        out = capsys.readouterr().out
        assert "0s" in out
        assert "tests passing" not in out

    def test_rich_tests_passing_none_omitted(self, capsys):
        if not _has_rich():
            return
        ui = RichTUI()
        ui.completion_summary(
            status="MAX_ROUNDS", round_num=5, duration_s=60.0,
            improvements=2, bugs_fixed=1, tests_passing=None,
            report_path="report.md",
        )
        out = capsys.readouterr().out
        assert "tests passing" not in out

    def test_rich_converged_green_border(self, capsys):
        """CONVERGED status should use green styling (✅ icon present)."""
        if not _has_rich():
            return
        ui = RichTUI()
        ui.completion_summary(
            status="CONVERGED", round_num=3, duration_s=180.0,
            improvements=5, bugs_fixed=1, tests_passing=20,
            report_path="report.md",
        )
        out = capsys.readouterr().out
        assert "CONVERGED" in out
        assert "Evolution Complete" in out

    def test_rich_max_rounds_yellow_border(self, capsys):
        """MAX_ROUNDS status should use warning styling (⚠️ icon present)."""
        if not _has_rich():
            return
        ui = RichTUI()
        ui.completion_summary(
            status="MAX_ROUNDS", round_num=20, duration_s=600.0,
            improvements=8, bugs_fixed=3, tests_passing=100,
            report_path="report.md",
        )
        out = capsys.readouterr().out
        assert "MAX_ROUNDS" in out
        assert "Evolution Complete" in out

    # -- JsonTUI ------------------------------------------------------------

    def _parse_json(self, capsys) -> dict:
        import json
        out = capsys.readouterr().out.strip()
        lines = [l for l in out.splitlines() if l.strip()]
        assert len(lines) >= 1
        return json.loads(lines[-1])

    def test_json_zero_improvements(self, capsys):
        ui = JsonTUI()
        ui.completion_summary(
            status="CONVERGED", round_num=1, duration_s=5.0,
            improvements=0, bugs_fixed=0, tests_passing=0,
            report_path="runs/s1/evolution_report.md",
        )
        obj = self._parse_json(capsys)
        assert obj["type"] == "completion_summary"
        assert obj["improvements"] == 0
        assert obj["bugs_fixed"] == 0
        assert obj["tests_passing"] == 0

    def test_json_very_long_duration(self, capsys):
        ui = JsonTUI()
        ui.completion_summary(
            status="CONVERGED", round_num=100, duration_s=12359.0,
            improvements=50, bugs_fixed=10, tests_passing=500,
            report_path="runs/s1/evolution_report.md",
        )
        obj = self._parse_json(capsys)
        assert obj["duration_s"] == 12359.0
        assert obj["round"] == 100
        assert obj["improvements"] == 50

    def test_json_short_duration(self, capsys):
        ui = JsonTUI()
        ui.completion_summary(
            status="CONVERGED", round_num=1, duration_s=0.5,
            improvements=1, bugs_fixed=0, tests_passing=10,
            report_path="report.md",
        )
        obj = self._parse_json(capsys)
        assert obj["duration_s"] == 0.5

    def test_json_zero_duration(self, capsys):
        ui = JsonTUI()
        ui.completion_summary(
            status="CONVERGED", round_num=1, duration_s=0.0,
            improvements=0, bugs_fixed=0, tests_passing=None,
            report_path="report.md",
        )
        obj = self._parse_json(capsys)
        assert obj["duration_s"] == 0.0
        assert obj["tests_passing"] is None

    def test_json_tests_passing_none(self, capsys):
        ui = JsonTUI()
        ui.completion_summary(
            status="MAX_ROUNDS", round_num=5, duration_s=60.0,
            improvements=2, bugs_fixed=1, tests_passing=None,
            report_path="report.md",
        )
        obj = self._parse_json(capsys)
        assert obj["tests_passing"] is None
        assert obj["status"] == "MAX_ROUNDS"

    def test_json_all_fields_present(self, capsys):
        """Verify the JSON event contains all expected fields."""
        ui = JsonTUI()
        ui.completion_summary(
            status="CONVERGED", round_num=8, duration_s=754.0,
            improvements=6, bugs_fixed=2, tests_passing=47,
            report_path="runs/s1/evolution_report.md",
        )
        obj = self._parse_json(capsys)
        expected_keys = {"type", "timestamp", "status", "round", "duration_s",
                         "improvements", "bugs_fixed", "tests_passing", "report_path"}
        assert expected_keys.issubset(set(obj.keys())), (
            f"Missing keys: {expected_keys - set(obj.keys())}"
        )

    def test_json_report_path_preserved(self, capsys):
        """Report path should be passed through exactly as given."""
        ui = JsonTUI()
        path = "/very/long/path/to/runs/20260325/evolution_report.md"
        ui.completion_summary(
            status="CONVERGED", round_num=1, duration_s=10.0,
            improvements=1, bugs_fixed=0, tests_passing=5,
            report_path=path,
        )
        obj = self._parse_json(capsys)
        assert obj["report_path"] == path


# ---------------------------------------------------------------------------
# Cost display in round_header and completion_summary — all TUI variants
# ---------------------------------------------------------------------------

class TestCostDisplayPlain:
    """PlainTUI cost display in round_header and completion_summary."""

    def test_round_header_with_cost(self, capsys):
        ui = PlainTUI()
        ui.round_header(3, 10, target="test", checked=1, total=3,
                        estimated_cost_usd=3.80)
        out = capsys.readouterr().out
        assert "ROUND 3/10" in out
        assert "~$3.80" in out

    def test_round_header_no_cost(self, capsys):
        ui = PlainTUI()
        ui.round_header(1, 10)
        out = capsys.readouterr().out
        assert "ROUND 1/10" in out
        assert "$" not in out

    def test_completion_summary_with_cost(self, capsys):
        ui = PlainTUI()
        ui.completion_summary(
            status="CONVERGED", round_num=8, duration_s=754.0,
            improvements=6, bugs_fixed=2, tests_passing=47,
            report_path="report.md", estimated_cost_usd=12.40,
        )
        out = capsys.readouterr().out
        assert "~$12.40 estimated cost" in out

    def test_completion_summary_no_cost(self, capsys):
        ui = PlainTUI()
        ui.completion_summary(
            status="CONVERGED", round_num=8, duration_s=754.0,
            improvements=6, bugs_fixed=2, tests_passing=47,
            report_path="report.md",
        )
        out = capsys.readouterr().out
        assert "estimated cost" not in out


class TestCostDisplayRich:
    """RichTUI cost display in round_header and completion_summary."""

    def test_round_header_with_cost(self, capsys):
        if not _has_rich():
            return
        ui = RichTUI()
        ui.round_header(3, 10, target="test", checked=1, total=3,
                        estimated_cost_usd=3.80)
        out = capsys.readouterr().out
        assert "ROUND 3/10" in out
        assert "~$3.80" in out

    def test_round_header_no_cost(self, capsys):
        if not _has_rich():
            return
        ui = RichTUI()
        ui.round_header(1, 10)
        out = capsys.readouterr().out
        assert "ROUND 1/10" in out

    def test_completion_summary_with_cost(self, capsys):
        if not _has_rich():
            return
        ui = RichTUI()
        ui.completion_summary(
            status="CONVERGED", round_num=8, duration_s=754.0,
            improvements=6, bugs_fixed=2, tests_passing=47,
            report_path="report.md", estimated_cost_usd=12.40,
        )
        out = capsys.readouterr().out
        assert "~$12.40 estimated cost" in out

    def test_completion_summary_no_cost(self, capsys):
        if not _has_rich():
            return
        ui = RichTUI()
        ui.completion_summary(
            status="CONVERGED", round_num=8, duration_s=754.0,
            improvements=6, bugs_fixed=2, tests_passing=47,
            report_path="report.md",
        )
        out = capsys.readouterr().out
        assert "estimated cost" not in out


class TestCostDisplayJson:
    """JsonTUI cost display in round_header and completion_summary."""

    def _parse_json(self, capsys) -> dict:
        import json
        out = capsys.readouterr().out.strip()
        lines = [l for l in out.splitlines() if l.strip()]
        assert len(lines) >= 1
        return json.loads(lines[-1])

    def test_round_header_with_cost(self, capsys):
        ui = JsonTUI()
        ui.round_header(3, 10, target="test", checked=1, total=3,
                        estimated_cost_usd=3.80)
        obj = self._parse_json(capsys)
        assert obj["type"] == "round_start"
        assert obj["estimated_cost_usd"] == 3.80

    def test_round_header_no_cost(self, capsys):
        ui = JsonTUI()
        ui.round_header(1, 10)
        obj = self._parse_json(capsys)
        assert obj["estimated_cost_usd"] is None

    def test_completion_summary_with_cost(self, capsys):
        ui = JsonTUI()
        ui.completion_summary(
            status="CONVERGED", round_num=8, duration_s=754.0,
            improvements=6, bugs_fixed=2, tests_passing=47,
            report_path="report.md", estimated_cost_usd=12.40,
        )
        obj = self._parse_json(capsys)
        assert obj["type"] == "completion_summary"
        assert obj["estimated_cost_usd"] == 12.40

    def test_completion_summary_no_cost(self, capsys):
        ui = JsonTUI()
        ui.completion_summary(
            status="CONVERGED", round_num=8, duration_s=754.0,
            improvements=6, bugs_fixed=2, tests_passing=47,
            report_path="report.md",
        )
        obj = self._parse_json(capsys)
        assert obj["estimated_cost_usd"] is None
