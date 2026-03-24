"""Tests for tui.py — factory function, TUI Protocol parity."""

from tui import get_tui, RichTUI, PlainTUI, TUIProtocol, _has_rich


class TestTUIProtocol:
    """Verify both implementations satisfy the TUIProtocol."""

    def test_plain_tui_is_protocol(self):
        assert isinstance(PlainTUI(), TUIProtocol)

    def test_rich_tui_is_protocol(self):
        if _has_rich():
            assert isinstance(RichTUI(), TUIProtocol)

    def test_method_parity(self):
        """RichTUI and PlainTUI must have the same public methods."""
        rich_methods = {m for m in dir(RichTUI) if not m.startswith("_")}
        plain_methods = {m for m in dir(PlainTUI) if not m.startswith("_")}
        # Both should cover at least all Protocol methods
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
        # And they should match each other
        assert rich_methods == plain_methods, (
            f"Mismatch: only in Rich={rich_methods - plain_methods}, "
            f"only in Plain={plain_methods - rich_methods}"
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

    def test_round_header(self, capsys):
        PlainTUI().round_header(1, 10, target="test", checked=3, total=5)
        out = capsys.readouterr().out
        assert "ROUND 1/10" in out

    def test_check_result_pass(self, capsys):
        PlainTUI().check_result("check", "pytest", passed=True)
        assert "PASS" in capsys.readouterr().out

    def test_check_result_fail(self, capsys):
        PlainTUI().check_result("check", "pytest", passed=False)
        assert "FAIL" in capsys.readouterr().out

    def test_check_result_timeout(self, capsys):
        PlainTUI().check_result("check", "pytest", timeout=True)
        assert "TIMEOUT" in capsys.readouterr().out

    def test_progress_summary(self, capsys):
        PlainTUI().progress_summary(5, 3)
        out = capsys.readouterr().out
        assert "5 done" in out
        assert "3 remaining" in out

    def test_converged(self, capsys):
        PlainTUI().converged(5, "all done")
        assert "CONVERGED" in capsys.readouterr().out

    def test_blocked_message(self, capsys):
        PlainTUI().blocked_message(3)
        assert "3 remaining" in capsys.readouterr().out

    def test_status_flow(self, capsys):
        ui = PlainTUI()
        ui.status_header("/tmp/proj", True)
        ui.status_improvements(5, 2, 1)
        ui.status_memory(3)
        ui.status_session("20260101_000000", 5, 3, False)
        ui.status_flush()
        out = capsys.readouterr().out
        assert "/tmp/proj" in out
        assert "5 done" in out
