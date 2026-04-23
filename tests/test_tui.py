"""Tests for tui.py — factory function, TUI Protocol parity, JsonTUI."""

import json

from tui import get_tui, RichTUI, PlainTUI, JsonTUI, TUIProtocol, _has_rich
import tui as _tui_mod


class TestTUIProtocol:
    """Verify both implementations satisfy the TUIProtocol."""

    def test_plain_tui_is_protocol(self):
        assert isinstance(PlainTUI(), TUIProtocol)

    def test_rich_tui_is_protocol(self):
        if _has_rich():
            assert isinstance(RichTUI(), TUIProtocol)

    def test_json_tui_is_protocol(self):
        assert isinstance(JsonTUI(), TUIProtocol)

    def test_method_parity(self):
        """RichTUI, PlainTUI, and JsonTUI must have the same public methods."""
        rich_methods = {m for m in dir(RichTUI) if not m.startswith("_")}
        plain_methods = {m for m in dir(PlainTUI) if not m.startswith("_")}
        json_methods = {m for m in dir(JsonTUI) if not m.startswith("_")}
        # All should cover at least all Protocol methods
        protocol_methods = {
            m for m in dir(TUIProtocol)
            if not m.startswith("_") and callable(getattr(TUIProtocol, m, None))
        }
        assert protocol_methods.issubset(rich_methods), (
            f"RichTUI missing: {protocol_methods - rich_methods}"
        )
        assert protocol_methods.issubset(plain_methods), (
            f"PlainTUI missing: {protocol_methods - plain_methods}"
        )
        assert protocol_methods.issubset(json_methods), (
            f"JsonTUI missing: {protocol_methods - json_methods}"
        )
        # Rich, Plain, and Json should match each other
        assert rich_methods == plain_methods, (
            f"Mismatch: only in Rich={rich_methods - plain_methods}, "
            f"only in Plain={plain_methods - rich_methods}"
        )
        assert rich_methods == json_methods, (
            f"Mismatch: only in Rich={rich_methods - json_methods}, "
            f"only in Json={json_methods - rich_methods}"
        )


class TestGetTUI:
    def test_returns_protocol_instance(self):
        ui = get_tui()
        assert isinstance(ui, TUIProtocol)

    def test_returns_rich_when_available(self):
        if _has_rich():
            assert isinstance(get_tui(), RichTUI)

    def test_plain_fallback(self):
        """PlainTUI can always be instantiated."""
        ui = PlainTUI()
        assert isinstance(ui, TUIProtocol)


class TestPlainTUIMethods:
    """Smoke-test PlainTUI methods don't crash."""

    @classmethod
    def setup_class(cls):
        cls.ui = PlainTUI()

    def test_round_header(self, capsys):
        self.ui.round_header(1, 10, target="test", checked=3, total=5)
        out = capsys.readouterr().out
        assert "ROUND 1/10" in out

    def test_check_result_pass(self, capsys):
        self.ui.check_result("check", "pytest", passed=True)
        assert "PASS" in capsys.readouterr().out

    def test_check_result_fail(self, capsys):
        self.ui.check_result("check", "pytest", passed=False)
        assert "FAIL" in capsys.readouterr().out

    def test_check_result_timeout(self, capsys):
        self.ui.check_result("check", "pytest", timeout=True)
        assert "TIMEOUT" in capsys.readouterr().out

    def test_progress_summary(self, capsys):
        self.ui.progress_summary(5, 3)
        out = capsys.readouterr().out
        assert "5 done" in out
        assert "3 remaining" in out

    def test_converged(self, capsys):
        self.ui.converged(5, "all done")
        assert "CONVERGED" in capsys.readouterr().out

    def test_blocked_message(self, capsys):
        self.ui.blocked_message(3)
        assert "3 remaining" in capsys.readouterr().out

    def test_status_flow(self, capsys):
        self.ui.status_header("/tmp/proj", True)
        self.ui.status_improvements(5, 2, 1)
        self.ui.status_memory(3)
        self.ui.status_session("20260101_000000", 5, 3, False)
        self.ui.status_flush()
        out = capsys.readouterr().out
        assert "/tmp/proj" in out
        assert "5 done" in out

    def test_budget_reached(self, capsys):
        self.ui.budget_reached(5, 10.0, 10.24)
        out = capsys.readouterr().out
        assert "budget" in out.lower() or "Budget" in out

    def test_structural_change_required(self, capsys):
        marker = {
            "reason": "extracted git.py",
            "verify": "python -m evolve --help",
            "resume": "evolve start . --resume",
            "round": "3",
            "timestamp": "2026-04-23T21:00:00Z",
        }
        self.ui.structural_change_required(marker)
        out = capsys.readouterr().out
        assert "Structural Change" in out
        assert "extracted git.py" in out
        assert "python -m evolve --help" in out
        assert "evolve start . --resume" in out


class TestGetTUIJson:
    """Test that get_tui returns JsonTUI when _use_json is True."""

    def test_returns_json_tui_when_flag_set(self):
        old = _tui_mod._use_json
        try:
            _tui_mod._use_json = True
            ui = get_tui()
            assert isinstance(ui, JsonTUI)
        finally:
            _tui_mod._use_json = old

    def test_returns_non_json_when_flag_unset(self):
        old = _tui_mod._use_json
        try:
            _tui_mod._use_json = False
            ui = get_tui()
            assert not isinstance(ui, JsonTUI)
        finally:
            _tui_mod._use_json = old


class TestJsonTUIMethods:
    """Smoke-test JsonTUI methods emit valid JSON lines."""

    @classmethod
    def setup_class(cls):
        cls.ui = JsonTUI()

    def _parse_line(self, capsys) -> dict:
        out = capsys.readouterr().out.strip()
        lines = [l for l in out.splitlines() if l.strip()]
        assert len(lines) >= 1, f"Expected JSON output, got: {out!r}"
        return json.loads(lines[-1])

    def test_round_header(self, capsys):
        self.ui.round_header(1, 10, target="test", checked=3, total=5)
        obj = self._parse_line(capsys)
        assert obj["type"] == "round_start"
        assert obj["round"] == 1
        assert obj["max_rounds"] == 10
        assert obj["target"] == "test"
        assert obj["checked"] == 3
        assert obj["total"] == 5
        assert "timestamp" in obj

    def test_check_result_pass(self, capsys):
        self.ui.check_result("check", "pytest", passed=True)
        obj = self._parse_line(capsys)
        assert obj["type"] == "check_result"
        assert obj["passed"] is True
        assert obj["cmd"] == "pytest"

    def test_check_result_timeout(self, capsys):
        self.ui.check_result("check", "pytest", timeout=True)
        obj = self._parse_line(capsys)
        assert obj["type"] == "check_result"
        assert obj["timeout"] is True

    def test_agent_tool(self, capsys):
        self.ui.agent_tool("Edit", "src/main.py")
        obj = self._parse_line(capsys)
        assert obj["type"] == "agent_tool"
        assert obj["tool"] == "Edit"
        assert obj["input"] == "src/main.py"

    def test_converged(self, capsys):
        self.ui.converged(5, "All README claims verified")
        obj = self._parse_line(capsys)
        assert obj["type"] == "converged"
        assert obj["round"] == 5
        assert "All README" in obj["reason"]

    def test_progress_summary(self, capsys):
        self.ui.progress_summary(5, 3)
        obj = self._parse_line(capsys)
        assert obj["type"] == "progress_summary"
        assert obj["checked"] == 5
        assert obj["unchecked"] == 3

    def test_blocked_message(self, capsys):
        self.ui.blocked_message(2)
        obj = self._parse_line(capsys)
        assert obj["type"] == "blocked"
        assert obj["blocked"] == 2

    def test_git_status(self, capsys):
        self.ui.git_status("feat: add feature", pushed=True)
        obj = self._parse_line(capsys)
        assert obj["type"] == "git_status"
        assert obj["pushed"] is True

    def test_max_rounds(self, capsys):
        self.ui.max_rounds(10, 8, 2)
        obj = self._parse_line(capsys)
        assert obj["type"] == "max_rounds"
        assert obj["max_rounds"] == 10

    def test_warn(self, capsys):
        self.ui.warn("something happened")
        obj = self._parse_line(capsys)
        assert obj["type"] == "warn"
        assert obj["message"] == "something happened"

    def test_error(self, capsys):
        self.ui.error("bad thing")
        obj = self._parse_line(capsys)
        assert obj["type"] == "error"
        assert obj["message"] == "bad thing"

    def test_status_flow(self, capsys):
        self.ui.status_header("/tmp/proj", True)
        self.ui.status_improvements(5, 2, 1)
        self.ui.status_memory(3)
        self.ui.status_session("20260101_000000", 5, 3, False)
        self.ui.status_flush()
        out = capsys.readouterr().out.strip()
        lines = out.splitlines()
        assert len(lines) == 5
        for line in lines:
            obj = json.loads(line)
            assert "type" in obj
            assert "timestamp" in obj

    def test_improvement_completed_via_agent_tool(self, capsys):
        """JsonTUI emits agent_tool events that can track improvement completion."""
        self.ui.agent_tool("Edit", "runs/improvements.md")
        obj = self._parse_line(capsys)
        assert obj["type"] == "agent_tool"

    def test_no_check(self, capsys):
        self.ui.no_check()
        obj = self._parse_line(capsys)
        assert obj["type"] == "no_check"

    def test_agent_working(self, capsys):
        self.ui.agent_working()
        obj = self._parse_line(capsys)
        assert obj["type"] == "agent_working"

    def test_agent_done(self, capsys):
        self.ui.agent_done(5, "/tmp/log.md")
        obj = self._parse_line(capsys)
        assert obj["type"] == "agent_done"
        assert obj["tools_used"] == 5

    def test_round_failed(self, capsys):
        self.ui.round_failed(3, 1)
        obj = self._parse_line(capsys)
        assert obj["type"] == "round_failed"
        assert obj["round"] == 3

    def test_party_mode(self, capsys):
        self.ui.party_mode()
        obj = self._parse_line(capsys)
        assert obj["type"] == "party_mode"

    def test_party_results(self, capsys):
        self.ui.party_results("/tmp/proposal.md", "/tmp/report.md")
        obj = self._parse_line(capsys)
        assert obj["type"] == "party_results"

    def test_uncommitted(self, capsys):
        self.ui.uncommitted()
        obj = self._parse_line(capsys)
        assert obj["type"] == "uncommitted"

    def test_sdk_rate_limited(self, capsys):
        self.ui.sdk_rate_limited(60, 1, 5)
        obj = self._parse_line(capsys)
        assert obj["type"] == "sdk_rate_limited"
        assert obj["wait"] == 60

    def test_budget_reached(self, capsys):
        self.ui.budget_reached(5, 10.0, 10.24)
        obj = self._parse_line(capsys)
        assert obj["type"] == "budget_reached"
        assert obj["budget_usd"] == 10.0
        assert obj["spent_usd"] == 10.24

    def test_structural_change_required(self, capsys):
        marker = {
            "reason": "extracted git.py",
            "verify": "python -m evolve --help",
            "resume": "evolve start . --resume",
            "round": "3",
            "timestamp": "2026-04-23T21:00:00Z",
        }
        self.ui.structural_change_required(marker)
        obj = self._parse_line(capsys)
        assert obj["type"] == "structural_change_required"
        assert obj["reason"] == "extracted git.py"
        assert obj["verify"] == "python -m evolve --help"
        assert obj["resume"] == "evolve start . --resume"
        assert obj["round"] == "3"
