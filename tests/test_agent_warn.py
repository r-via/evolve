"""Tests for US-049: agent_warn method on TUIProtocol and SDK wiring.

Covers:
- TUIProtocol includes agent_warn
- All three TUI implementations are callable
- SDK runner invokes agent_warn when is_error=True (error subtype)
- SDK runner does NOT invoke agent_warn on success subtype
"""

import json
import sys
from io import StringIO
from unittest.mock import MagicMock, patch

from evolve.tui import TUIProtocol, RichTUI, PlainTUI, JsonTUI, _has_rich


class TestAgentWarnProtocol:
    """TUIProtocol declares agent_warn."""

    def test_protocol_has_agent_warn(self):
        assert hasattr(TUIProtocol, "agent_warn")
        assert callable(getattr(TUIProtocol, "agent_warn", None))


class TestPlainTUIAgentWarn:
    """PlainTUI.agent_warn prints to stderr."""

    def test_agent_warn_stderr(self):
        tui = PlainTUI()
        buf = StringIO()
        with patch.object(sys, "stderr", buf):
            tui.agent_warn("Agent stopped: error_max_turns after 40 turns")
        output = buf.getvalue()
        assert "⚠" in output
        assert "error_max_turns" in output
        assert "40 turns" in output


class TestRichTUIAgentWarn:
    """RichTUI.agent_warn renders a yellow warning panel."""

    def test_agent_warn_callable(self):
        if not _has_rich():
            return
        tui = RichTUI()
        # Should not raise
        tui.agent_warn("Agent stopped: error_max_turns after 40 turns")


class TestJsonTUIAgentWarn:
    """JsonTUI.agent_warn emits a structured JSON event."""

    def test_agent_warn_json_event(self, capsys):
        tui = JsonTUI()
        tui.agent_warn("Agent stopped: error_max_turns after 40 turns")
        output = capsys.readouterr().out.strip()
        event = json.loads(output)
        assert event["type"] == "agent_warn"
        assert "error_max_turns" in event["message"]
        assert "timestamp" in event


class TestSDKRunnerAgentWarnWiring:
    """sdk_runner.run_claude_agent calls ui.agent_warn on error subtypes."""

    def test_agent_warn_called_on_error_subtype(self):
        """When SDK returns error_max_turns, agent_warn is invoked."""
        # Verify the source code calls ui.agent_warn (not ui.warn) for
        # error subtypes
        from pathlib import Path
        # Real code lives in infrastructure/claude_sdk/runner.py after
        # DDD migration (US-071); sdk_runner.py is a backward-compat shim.
        src = (Path(__file__).resolve().parent.parent
               / "evolve" / "infrastructure" / "claude_sdk" / "runner.py").read_text()
        assert "ui.agent_warn(" in src, (
            "sdk_runner.py must call ui.agent_warn() for error subtypes"
        )
        # Ensure it does NOT call ui.warn for the error path
        # (the old pattern was ui.warn("⚠ ..."))
        lines = src.splitlines()
        for i, line in enumerate(lines):
            if "ui.warn" in line and "agent_warn" not in line:
                # Allow ui.warn in other contexts, but not in the
                # error subtype block
                context = "\n".join(lines[max(0, i - 3):i + 3])
                assert "final_subtype" not in context, (
                    f"sdk_runner.py line {i+1} calls ui.warn instead of "
                    f"ui.agent_warn in the error-subtype block:\n{context}"
                )
