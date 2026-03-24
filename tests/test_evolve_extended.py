"""Extended tests for evolve.py — _parse_round_args, _check_deps, _show_status edge cases."""

import sys
import textwrap
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from evolve import _parse_round_args, _show_status


# ---------------------------------------------------------------------------
# _parse_round_args
# ---------------------------------------------------------------------------

class TestParseRoundArgs:
    def test_minimal(self):
        with patch("sys.argv", ["evolve", "_round", "/tmp/proj", "--round-num", "3"]):
            args = _parse_round_args()
            assert args.command == "_round"
            assert args.project_dir == "/tmp/proj"
            assert args.round_num == 3
            assert args.check is None
            assert args.timeout == 300
            assert args.run_dir is None
            assert args.yolo is False
            assert args.model == "claude-opus-4-6"

    def test_all_flags(self):
        with patch("sys.argv", [
            "evolve", "_round", "/tmp/proj",
            "--round-num", "5",
            "--check", "pytest",
            "--timeout", "600",
            "--run-dir", "/tmp/runs/session",
            "--yolo",
            "--model", "claude-sonnet-4-20250514",
        ]):
            args = _parse_round_args()
            assert args.round_num == 5
            assert args.check == "pytest"
            assert args.timeout == 600
            assert args.run_dir == "/tmp/runs/session"
            assert args.yolo is True
            assert args.model == "claude-sonnet-4-20250514"


# ---------------------------------------------------------------------------
# _show_status — extended edge cases
# ---------------------------------------------------------------------------

class TestShowStatusExtended:
    def test_no_readme(self, tmp_path: Path):
        """Status works even without README.md."""
        _show_status(tmp_path)

    def test_with_memory_errors(self, tmp_path: Path):
        """Status reports memory error count."""
        (tmp_path / "README.md").write_text("# Test")
        runs = tmp_path / "runs"
        runs.mkdir()
        (runs / "memory.md").write_text(textwrap.dedent("""\
            # Memory
            ## Error: First error
            - What happened: something
            ## Error: Second error
            - What happened: another thing
        """))
        _show_status(tmp_path)

    def test_with_blocked_improvements(self, tmp_path: Path):
        """Status reports blocked count."""
        (tmp_path / "README.md").write_text("# Test")
        runs = tmp_path / "runs"
        runs.mkdir()
        (runs / "improvements.md").write_text(textwrap.dedent("""\
            - [x] [functional] done
            - [ ] [functional] [needs-package] blocked one
            - [ ] [functional] pending
        """))
        _show_status(tmp_path)

    def test_no_improvements_file(self, tmp_path: Path):
        """Status works with no improvements.md."""
        (tmp_path / "README.md").write_text("# Test")
        runs = tmp_path / "runs"
        runs.mkdir()
        _show_status(tmp_path)

    def test_no_memory_file(self, tmp_path: Path):
        """Status works with no memory.md — reports 0 errors."""
        (tmp_path / "README.md").write_text("# Test")
        runs = tmp_path / "runs"
        runs.mkdir()
        _show_status(tmp_path)

    def test_multiple_sessions(self, tmp_path: Path):
        """Status picks the latest session."""
        (tmp_path / "README.md").write_text("# Test")
        runs = tmp_path / "runs"
        s1 = runs / "20260101_000000"
        s1.mkdir(parents=True)
        (s1 / "conversation_loop_1.md").write_text("r1")

        s2 = runs / "20260102_000000"
        s2.mkdir(parents=True)
        (s2 / "conversation_loop_1.md").write_text("r1")
        (s2 / "conversation_loop_2.md").write_text("r2")
        (s2 / "check_round_1.txt").write_text("PASS")
        (s2 / "check_round_2.txt").write_text("PASS")

        _show_status(tmp_path)

    def test_no_runs_dir(self, tmp_path: Path):
        """Status works with no runs/ directory at all."""
        (tmp_path / "README.md").write_text("# Test")
        _show_status(tmp_path)


# ---------------------------------------------------------------------------
# _check_deps — various import scenarios
# ---------------------------------------------------------------------------

class TestCheckDeps:
    def test_sdk_available(self):
        """When claude_agent_sdk imports fine, _check_deps returns normally."""
        mock_module = MagicMock()
        with patch.dict("sys.modules", {"claude_agent_sdk": mock_module}):
            from evolve import _check_deps
            _check_deps()  # should not raise

    def test_sdk_missing_exits_2(self):
        """When claude_agent_sdk is missing, exits with code 2."""
        from evolve import _check_deps

        original_import = __builtins__.__import__ if hasattr(__builtins__, '__import__') else __import__

        def mock_import(name, *args, **kwargs):
            if name == "claude_agent_sdk":
                raise ImportError("no module")
            return original_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=mock_import):
            with pytest.raises(SystemExit) as exc:
                _check_deps()
            assert exc.value.code == 2
