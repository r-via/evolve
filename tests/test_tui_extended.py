"""Extended tests for tui.py — PlainTUI and RichTUI method coverage."""

from unittest.mock import patch

from tui import PlainTUI, RichTUI, JsonTUI, _has_rich, get_tui
import tui as _tui_mod


class TestPlainTUIExtended:
    """Cover all PlainTUI methods not yet tested."""

    def test_no_check(self, capsys):
        PlainTUI().no_check()
        out = capsys.readouterr().out
        assert "no check" in out.lower() or "manual" in out.lower()

    def test_agent_working(self, capsys):
        PlainTUI().agent_working()
        # just should not crash

    def test_agent_tool(self, capsys):
        PlainTUI().agent_tool("Bash", "ls -la")
        out = capsys.readouterr().out
        assert "Bash" in out

    def test_agent_done(self, capsys):
        PlainTUI().agent_done(5, "/tmp/log.md")
        out = capsys.readouterr().out
        assert "5" in out

    def test_agent_text(self, capsys):
        PlainTUI().agent_text("hello world")
        # should not crash

    def test_git_status_pushed(self, capsys):
        PlainTUI().git_status("feat: test", pushed=True)
        out = capsys.readouterr().out
        assert "feat: test" in out

    def test_git_status_push_failed(self, capsys):
        PlainTUI().git_status("feat: test", pushed=False, error="rejected")
        out = capsys.readouterr().out
        assert "feat: test" in out

    def test_git_status_no_changes(self, capsys):
        PlainTUI().git_status("chore: nothing", pushed=None)
        out = capsys.readouterr().out
        assert "no changes" in out.lower()

    def test_max_rounds(self, capsys):
        PlainTUI().max_rounds(10, 7, 3)
        out = capsys.readouterr().out
        assert "10" in out

    def test_round_failed(self, capsys):
        PlainTUI().round_failed(3, 1)
        out = capsys.readouterr().out
        assert "3" in out

    def test_no_progress(self, capsys):
        PlainTUI().no_progress()
        # should not crash

    def test_run_dir_info(self, capsys):
        PlainTUI().run_dir_info("/tmp/runs/session")
        out = capsys.readouterr().out
        assert "/tmp/runs/session" in out

    def test_party_mode(self, capsys):
        PlainTUI().party_mode()
        # should not crash

    def test_warn(self, capsys):
        PlainTUI().warn("something bad")
        out = capsys.readouterr().out
        assert "something bad" in out

    def test_error(self, capsys):
        PlainTUI().error("fatal error")
        out = capsys.readouterr().out
        assert "fatal error" in out

    def test_info(self, capsys):
        PlainTUI().info("info message")
        out = capsys.readouterr().out
        assert "info message" in out

    def test_party_results_with_files(self, capsys):
        PlainTUI().party_results("/tmp/proposal.md", "/tmp/report.md")
        out = capsys.readouterr().out
        assert "proposal" in out.lower() or "/tmp" in out

    def test_party_results_no_files(self, capsys):
        PlainTUI().party_results(None, None)
        # should not crash

    def test_uncommitted(self, capsys):
        PlainTUI().uncommitted()
        # should not crash

    def test_sdk_rate_limited(self, capsys):
        PlainTUI().sdk_rate_limited(60, 1, 5)
        out = capsys.readouterr().out
        assert "60" in out or "rate" in out.lower()

    def test_status_no_improvements(self, capsys):
        PlainTUI().status_no_improvements()
        # should not crash

    def test_round_header_no_target(self, capsys):
        PlainTUI().round_header(1, 10)
        out = capsys.readouterr().out
        assert "ROUND 1/10" in out

    def test_check_result_running(self, capsys):
        PlainTUI().check_result("check", "pytest", passed=None)
        out = capsys.readouterr().out
        assert "pytest" in out


class TestRichTUIExtended:
    """Cover RichTUI methods if rich is available."""

    def test_rich_available(self):
        """Just verify we can check for rich."""
        # _has_rich returns a bool
        result = _has_rich()
        assert isinstance(result, bool)

    def test_rich_tui_instantiation(self):
        if _has_rich():
            ui = RichTUI()
            assert ui is not None

    def test_rich_round_header(self, capsys):
        if _has_rich():
            ui = RichTUI()
            ui.round_header(1, 10, target="test", checked=3, total=5)

    def test_rich_all_methods_callable(self):
        """Verify all RichTUI methods can be called without crashing."""
        if not _has_rich():
            return
        ui = RichTUI()
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
        if not _has_rich():
            return
        ui = RichTUI()
        ui.history_empty("/tmp/proj")

    def test_rich_history_table(self, capsys):
        if not _has_rich():
            return
        ui = RichTUI()
        rows = [
            {"name": "20260101_000000", "rounds": "3/10", "status": "CONVERGED",
             "checked": 3, "unchecked": 0},
            {"name": "20260102_000000", "rounds": "5/10", "status": "IN_PROGRESS",
             "checked": 2, "unchecked": 3},
        ]
        ui.history_table("/tmp/proj", rows, 2, 8, 5)


class TestPlainTUIHistoryAndStatus:
    """Cover PlainTUI history and status_session edge cases."""

    def test_status_session_converged_with_reason(self, capsys):
        ui = PlainTUI()
        ui.status_session("20260101_000000", 5, 3, converged=True, reason="All done")
        out = capsys.readouterr().out
        assert "YES" in out
        assert "All done" in out

    def test_status_session_converged_no_reason(self, capsys):
        ui = PlainTUI()
        ui.status_session("20260101_000000", 5, 3, converged=True, reason="")
        out = capsys.readouterr().out
        assert "YES" in out

    def test_history_empty(self, capsys):
        PlainTUI().history_empty("/tmp/proj")
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
        PlainTUI().history_table("/tmp/proj", rows, 2, 8, 5)
        out = capsys.readouterr().out
        assert "Evolution History" in out
        assert "20260101_000000" in out
        assert "20260102_000000" in out
        assert "CONVERGED" in out
        assert "2 sessions" in out
        assert "8 rounds" in out
        assert "5 improvements" in out

    def test_status_memory_zero(self, capsys):
        PlainTUI().status_memory(0)
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

    def _parse_line(self, capsys) -> dict:
        import json
        out = capsys.readouterr().out.strip()
        lines = [l for l in out.splitlines() if l.strip()]
        assert len(lines) >= 1
        return json.loads(lines[-1])

    def test_agent_text(self, capsys):
        JsonTUI().agent_text("hello world")
        obj = self._parse_line(capsys)
        assert obj["type"] == "agent_text"
        assert obj["text"] == "hello world"

    def test_no_progress(self, capsys):
        JsonTUI().no_progress()
        obj = self._parse_line(capsys)
        assert obj["type"] == "no_progress"

    def test_run_dir_info(self, capsys):
        JsonTUI().run_dir_info("/tmp/run")
        obj = self._parse_line(capsys)
        assert obj["type"] == "run_dir_info"
        assert obj["run_dir"] == "/tmp/run"

    def test_info(self, capsys):
        JsonTUI().info("info msg")
        obj = self._parse_line(capsys)
        assert obj["type"] == "info"
        assert obj["message"] == "info msg"

    def test_status_no_improvements(self, capsys):
        JsonTUI().status_no_improvements()
        obj = self._parse_line(capsys)
        assert obj["type"] == "status_no_improvements"

    def test_history_empty(self, capsys):
        JsonTUI().history_empty("/tmp/proj")
        obj = self._parse_line(capsys)
        assert obj["type"] == "history_empty"
        assert obj["project_dir"] == "/tmp/proj"

    def test_history_table(self, capsys):
        rows = [{"name": "s1", "rounds": "3/10", "status": "CONVERGED",
                 "checked": 3, "unchecked": 0}]
        JsonTUI().history_table("/tmp/proj", rows, 1, 3, 3)
        obj = self._parse_line(capsys)
        assert obj["type"] == "history"
        assert obj["num_sessions"] == 1
