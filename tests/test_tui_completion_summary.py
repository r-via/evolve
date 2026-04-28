"""Completion-summary + cost-display edge cases for all three TUI variants.

Split out of test_tui_extended.py per SPEC § "Hard rule: source files MUST
NOT exceed 500 lines" — the original was 824 lines.
"""

from evolve.tui import PlainTUI, RichTUI, JsonTUI, _has_rich


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
