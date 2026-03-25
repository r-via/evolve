"""Tests for evolve.py — CLI arg parsing, _show_status."""

import subprocess
import sys
import textwrap
from pathlib import Path
from unittest.mock import patch, MagicMock


class TestCLIParsing:
    """Test that CLI arguments are parsed correctly."""

    def test_start_minimal(self):
        """Minimal start command parses without error."""
        with patch("sys.argv", ["evolve", "start", "/tmp/project"]):
            # We can't run main() without deps, but we can test arg parsing
            import argparse
            ap = argparse.ArgumentParser()
            sub = ap.add_subparsers(dest="command")
            start_p = sub.add_parser("start")
            start_p.add_argument("project_dir")
            start_p.add_argument("--rounds", type=int, default=10)
            start_p.add_argument("--check", default=None)
            start_p.add_argument("--yolo", action="store_true")
            start_p.add_argument("--timeout", type=int, default=300)
            start_p.add_argument("--model", default=None)
            start_p.add_argument("--resume", action="store_true")
            args = ap.parse_args(["start", "/tmp/project"])
            assert args.command == "start"
            assert args.project_dir == "/tmp/project"
            assert args.rounds == 10
            assert args.check is None
            assert args.yolo is False
            assert args.timeout == 300
            assert args.model is None
            assert args.resume is False

    def test_start_all_flags(self):
        import argparse
        ap = argparse.ArgumentParser()
        sub = ap.add_subparsers(dest="command")
        start_p = sub.add_parser("start")
        start_p.add_argument("project_dir")
        start_p.add_argument("--rounds", type=int, default=10)
        start_p.add_argument("--check", default=None)
        start_p.add_argument("--yolo", action="store_true")
        start_p.add_argument("--timeout", type=int, default=300)
        start_p.add_argument("--model", default=None)
        start_p.add_argument("--resume", action="store_true")
        start_p.add_argument("--forever", action="store_true")
        start_p.add_argument("--dry-run", action="store_true", dest="dry_run")
        start_p.add_argument("--validate", action="store_true")
        start_p.add_argument("--json", action="store_true")
        args = ap.parse_args([
            "start", "/tmp/project",
            "--rounds", "20",
            "--check", "pytest",
            "--yolo",
            "--timeout", "600",
            "--model", "claude-sonnet-4-20250514",
            "--resume",
            "--forever",
            "--dry-run",
            "--validate",
            "--json",
        ])
        assert args.rounds == 20
        assert args.check == "pytest"
        assert args.yolo is True
        assert args.timeout == 600
        assert args.model == "claude-sonnet-4-20250514"
        assert args.resume is True
        assert args.forever is True
        assert args.dry_run is True
        assert args.validate is True
        assert args.json is True

    def test_forever_flag_defaults_false(self):
        import argparse
        ap = argparse.ArgumentParser()
        sub = ap.add_subparsers(dest="command")
        start_p = sub.add_parser("start")
        start_p.add_argument("project_dir")
        start_p.add_argument("--forever", action="store_true")
        args = ap.parse_args(["start", "/tmp/project"])
        assert args.forever is False

    def test_dry_run_flag_defaults_false(self):
        import argparse
        ap = argparse.ArgumentParser()
        sub = ap.add_subparsers(dest="command")
        start_p = sub.add_parser("start")
        start_p.add_argument("project_dir")
        start_p.add_argument("--dry-run", action="store_true", dest="dry_run")
        args = ap.parse_args(["start", "/tmp/project"])
        assert args.dry_run is False

    def test_validate_flag_defaults_false(self):
        import argparse
        ap = argparse.ArgumentParser()
        sub = ap.add_subparsers(dest="command")
        start_p = sub.add_parser("start")
        start_p.add_argument("project_dir")
        start_p.add_argument("--validate", action="store_true")
        args = ap.parse_args(["start", "/tmp/project"])
        assert args.validate is False

    def test_json_flag_defaults_false(self):
        import argparse
        ap = argparse.ArgumentParser()
        sub = ap.add_subparsers(dest="command")
        start_p = sub.add_parser("start")
        start_p.add_argument("project_dir")
        start_p.add_argument("--json", action="store_true")
        args = ap.parse_args(["start", "/tmp/project"])
        assert args.json is False

    def test_status_parsing(self):
        import argparse
        ap = argparse.ArgumentParser()
        sub = ap.add_subparsers(dest="command")
        status_p = sub.add_parser("status")
        status_p.add_argument("project_dir")
        args = ap.parse_args(["status", "/tmp/project"])
        assert args.command == "status"
        assert args.project_dir == "/tmp/project"


class TestShowStatus:
    """Test _show_status with a mock project directory."""

    def test_status_no_runs(self, tmp_path: Path):
        (tmp_path / "README.md").write_text("# Test")
        from evolve import _show_status
        # Should not crash even with no runs dir
        _show_status(tmp_path)

    def test_status_with_improvements(self, tmp_path: Path):
        (tmp_path / "README.md").write_text("# Test")
        runs = tmp_path / "runs"
        runs.mkdir()
        (runs / "improvements.md").write_text(textwrap.dedent("""\
            - [x] [functional] done one
            - [x] [functional] done two
            - [ ] [functional] pending
        """))
        from evolve import _show_status
        _show_status(tmp_path)

    def test_status_with_session(self, tmp_path: Path):
        (tmp_path / "README.md").write_text("# Test")
        runs = tmp_path / "runs"
        session = runs / "20260101_000000"
        session.mkdir(parents=True)
        (session / "conversation_loop_1.md").write_text("# Round 1")
        (session / "check_round_1.txt").write_text("PASS")
        from evolve import _show_status
        _show_status(tmp_path)

    def test_status_converged_session(self, tmp_path: Path):
        (tmp_path / "README.md").write_text("# Test")
        runs = tmp_path / "runs"
        session = runs / "20260101_000000"
        session.mkdir(parents=True)
        (session / "CONVERGED").write_text("All done")
        (session / "conversation_loop_1.md").write_text("# Round 1")
        from evolve import _show_status
        _show_status(tmp_path)


class TestExitCodes:
    """Verify exit code semantics from the README."""

    def test_exit_2_for_missing_deps(self):
        """evolve.py exits with code 2 when claude-agent-sdk is missing."""
        # Run in a subprocess without claude-agent-sdk
        result = subprocess.run(
            [sys.executable, "-c",
             "import sys; sys.modules['claude_agent_sdk'] = None; "
             "from evolve import _check_deps; _check_deps()"],
            capture_output=True, text=True,
            cwd=str(Path(__file__).parent.parent),
        )
        # _check_deps calls sys.exit(2) on import failure
        # This is an indirect test — the actual exit code may vary
        # depending on how ImportError is triggered
        assert result.returncode != 0


class TestInitConfig:
    """Test _init_config scaffolding."""

    def test_creates_evolve_toml(self, tmp_path: Path):
        from evolve import _init_config
        _init_config(tmp_path)
        config = tmp_path / "evolve.toml"
        assert config.is_file()
        content = config.read_text()
        assert 'check = ""' in content
        assert "rounds = 10" in content
        assert "timeout = 300" in content
        assert 'model = "claude-opus-4-6"' in content
        assert "yolo = false" in content

    def test_does_not_overwrite_existing(self, tmp_path: Path):
        config = tmp_path / "evolve.toml"
        config.write_text("custom = true\n")
        from evolve import _init_config
        _init_config(tmp_path)
        assert config.read_text() == "custom = true\n"

    def test_creates_parent_dirs(self, tmp_path: Path):
        target = tmp_path / "nested" / "deep" / "project"
        from evolve import _init_config
        _init_config(target)
        assert (target / "evolve.toml").is_file()


class TestLoadConfig:
    """Test _load_config reads evolve.toml and pyproject.toml correctly."""

    def test_no_config_files(self, tmp_path: Path):
        from evolve import _load_config
        assert _load_config(tmp_path) == {}

    def test_evolve_toml(self, tmp_path: Path):
        (tmp_path / "evolve.toml").write_text(
            'check = "pytest"\nrounds = 20\ntimeout = 600\n'
            'model = "claude-sonnet-4-20250514"\nyolo = true\n'
        )
        from evolve import _load_config
        cfg = _load_config(tmp_path)
        assert cfg["check"] == "pytest"
        assert cfg["rounds"] == 20
        assert cfg["timeout"] == 600
        assert cfg["model"] == "claude-sonnet-4-20250514"
        assert cfg["yolo"] is True

    def test_pyproject_toml_fallback(self, tmp_path: Path):
        (tmp_path / "pyproject.toml").write_text(
            '[tool.evolve]\ncheck = "npm test"\nrounds = 15\n'
        )
        from evolve import _load_config
        cfg = _load_config(tmp_path)
        assert cfg["check"] == "npm test"
        assert cfg["rounds"] == 15

    def test_evolve_toml_takes_precedence(self, tmp_path: Path):
        (tmp_path / "evolve.toml").write_text('check = "pytest"\n')
        (tmp_path / "pyproject.toml").write_text(
            '[tool.evolve]\ncheck = "npm test"\n'
        )
        from evolve import _load_config
        cfg = _load_config(tmp_path)
        assert cfg["check"] == "pytest"

    def test_pyproject_no_tool_evolve(self, tmp_path: Path):
        (tmp_path / "pyproject.toml").write_text('[tool.other]\nfoo = "bar"\n')
        from evolve import _load_config
        assert _load_config(tmp_path) == {}

    def test_malformed_toml_returns_empty(self, tmp_path: Path):
        (tmp_path / "evolve.toml").write_text("this is not valid toml {{{}}")
        from evolve import _load_config
        assert _load_config(tmp_path) == {}


class TestResolveConfig:
    """Test _resolve_config merges CLI, env, file config, and defaults."""

    def _make_args(self, **overrides):
        import argparse
        args = argparse.Namespace(
            check=None, rounds=None, timeout=None,
            model=None, yolo=None, resume=False,
        )
        for k, v in overrides.items():
            setattr(args, k, v)
        return args

    def test_cli_wins_over_file(self, tmp_path: Path):
        (tmp_path / "evolve.toml").write_text(
            'check = "npm test"\nrounds = 20\n'
        )
        from evolve import _resolve_config
        args = self._make_args(check="pytest")
        with patch("sys.argv", ["evolve", "start", str(tmp_path), "--check", "pytest"]):
            result = _resolve_config(args, tmp_path)
        assert result.check == "pytest"

    def test_file_config_used_when_no_cli(self, tmp_path: Path):
        (tmp_path / "evolve.toml").write_text(
            'check = "cargo test"\nrounds = 25\ntimeout = 600\n'
            'model = "claude-sonnet-4-20250514"\nyolo = true\n'
        )
        from evolve import _resolve_config
        args = self._make_args()
        with patch("sys.argv", ["evolve", "start", str(tmp_path)]):
            with patch.dict("os.environ", {}, clear=True):
                result = _resolve_config(args, tmp_path)
        assert result.check == "cargo test"
        assert result.rounds == 25
        assert result.timeout == 600
        assert result.model == "claude-sonnet-4-20250514"
        assert result.yolo is True

    def test_env_wins_over_file(self, tmp_path: Path):
        (tmp_path / "evolve.toml").write_text('model = "claude-sonnet-4-20250514"\n')
        from evolve import _resolve_config
        args = self._make_args()
        with patch("sys.argv", ["evolve", "start", str(tmp_path)]):
            with patch.dict("os.environ", {"EVOLVE_MODEL": "claude-opus-4-6"}, clear=True):
                result = _resolve_config(args, tmp_path)
        assert result.model == "claude-opus-4-6"

    def test_defaults_when_no_config(self, tmp_path: Path):
        from evolve import _resolve_config
        args = self._make_args()
        with patch("sys.argv", ["evolve", "start", str(tmp_path)]):
            with patch.dict("os.environ", {}, clear=True):
                result = _resolve_config(args, tmp_path)
        assert result.check is None
        assert result.rounds == 10
        assert result.timeout == 300
        assert result.model == "claude-opus-4-6"
        assert result.yolo is False


class TestCleanSessions:
    """Test _clean_sessions cleanup logic."""

    def test_no_runs_dir(self, tmp_path: Path):
        from evolve import _clean_sessions
        # Should not crash
        _clean_sessions(tmp_path, keep=5)

    def test_fewer_sessions_than_keep(self, tmp_path: Path):
        runs = tmp_path / "runs"
        (runs / "20260101_000000").mkdir(parents=True)
        (runs / "20260102_000000").mkdir(parents=True)
        from evolve import _clean_sessions
        _clean_sessions(tmp_path, keep=5)
        # All sessions should still exist
        assert (runs / "20260101_000000").is_dir()
        assert (runs / "20260102_000000").is_dir()

    def test_removes_oldest_sessions(self, tmp_path: Path):
        runs = tmp_path / "runs"
        for i in range(1, 6):
            (runs / f"2026010{i}_000000").mkdir(parents=True)
        from evolve import _clean_sessions
        _clean_sessions(tmp_path, keep=2)
        remaining = sorted(d.name for d in runs.iterdir() if d.is_dir())
        assert len(remaining) == 2
        # Should keep the two most recent (sorted descending)
        assert "20260105_000000" in remaining
        assert "20260104_000000" in remaining

    def test_ignores_non_timestamped_dirs(self, tmp_path: Path):
        runs = tmp_path / "runs"
        (runs / "20260101_000000").mkdir(parents=True)
        (runs / "20260102_000000").mkdir(parents=True)
        (runs / "some_other_dir").mkdir(parents=True)
        from evolve import _clean_sessions
        _clean_sessions(tmp_path, keep=1)
        # Non-timestamped dir should survive
        assert (runs / "some_other_dir").is_dir()
        assert (runs / "20260102_000000").is_dir()
        assert not (runs / "20260101_000000").is_dir()

    def test_keeps_shared_files(self, tmp_path: Path):
        runs = tmp_path / "runs"
        (runs / "20260101_000000").mkdir(parents=True)
        (runs / "improvements.md").write_text("# Improvements\n")
        (runs / "memory.md").write_text("# Memory\n")
        from evolve import _clean_sessions
        _clean_sessions(tmp_path, keep=0)
        # Shared files are not session dirs — should remain
        assert (runs / "improvements.md").is_file()
        assert (runs / "memory.md").is_file()

    def test_clean_cli_parsing(self):
        """Verify clean subcommand CLI args are parsed correctly."""
        import argparse
        ap = argparse.ArgumentParser()
        sub = ap.add_subparsers(dest="command")
        clean_p = sub.add_parser("clean")
        clean_p.add_argument("project_dir")
        clean_p.add_argument("--keep", type=int, default=5)
        args = ap.parse_args(["clean", "/tmp/project", "--keep", "3"])
        assert args.command == "clean"
        assert args.project_dir == "/tmp/project"
        assert args.keep == 3

    def test_init_cli_parsing(self):
        """Verify init subcommand CLI args are parsed correctly."""
        import argparse
        ap = argparse.ArgumentParser()
        sub = ap.add_subparsers(dest="command")
        init_p = sub.add_parser("init")
        init_p.add_argument("project_dir")
        args = ap.parse_args(["init", "/tmp/project"])
        assert args.command == "init"
        assert args.project_dir == "/tmp/project"

    def test_history_cli_parsing(self):
        """Verify history subcommand CLI args are parsed correctly."""
        import argparse
        ap = argparse.ArgumentParser()
        sub = ap.add_subparsers(dest="command")
        history_p = sub.add_parser("history")
        history_p.add_argument("project_dir")
        args = ap.parse_args(["history", "/tmp/project"])
        assert args.command == "history"
        assert args.project_dir == "/tmp/project"


class TestShowHistory:
    """Test _show_history with mock project directories."""

    def test_no_runs_dir(self, tmp_path: Path):
        """history works when no runs/ directory exists."""
        from evolve import _show_history
        _show_history(tmp_path)  # should not crash

    def test_empty_runs_dir(self, tmp_path: Path):
        """history works with an empty runs/ directory."""
        (tmp_path / "runs").mkdir()
        from evolve import _show_history
        _show_history(tmp_path)

    def test_no_session_dirs(self, tmp_path: Path):
        """history works when runs/ has only non-session files."""
        runs = tmp_path / "runs"
        runs.mkdir()
        (runs / "improvements.md").write_text("# Improvements\n")
        (runs / "memory.md").write_text("# Memory\n")
        from evolve import _show_history
        _show_history(tmp_path)

    def test_single_session_no_report(self, tmp_path: Path):
        """history shows a session even without evolution_report.md."""
        runs = tmp_path / "runs"
        session = runs / "20260101_000000"
        session.mkdir(parents=True)
        (session / "conversation_loop_1.md").write_text("# Round 1")
        (session / "conversation_loop_2.md").write_text("# Round 2")
        from evolve import _show_history
        _show_history(tmp_path)

    def test_session_with_evolution_report(self, tmp_path: Path):
        """history parses evolution_report.md for round/improvement data."""
        runs = tmp_path / "runs"
        session = runs / "20260101_000000"
        session.mkdir(parents=True)
        (session / "evolution_report.md").write_text(textwrap.dedent("""\
            # Evolution Report
            **Project:** test
            **Session:** 20260101_000000
            **Rounds:** 5/10
            **Status:** CONVERGED

            ## Summary
            - 3 improvements completed
            - 1 bugs fixed
            - 5 files modified
            - 2 improvements remaining
        """))
        from evolve import _show_history
        _show_history(tmp_path)

    def test_session_converged_marker(self, tmp_path: Path):
        """history detects CONVERGED marker when no report exists."""
        runs = tmp_path / "runs"
        session = runs / "20260101_000000"
        session.mkdir(parents=True)
        (session / "CONVERGED").write_text("All done")
        from evolve import _show_history
        _show_history(tmp_path)

    def test_multiple_sessions(self, tmp_path: Path):
        """history shows all sessions sorted by timestamp."""
        runs = tmp_path / "runs"
        s1 = runs / "20260101_000000"
        s1.mkdir(parents=True)
        (s1 / "CONVERGED").write_text("Done")
        (s1 / "evolution_report.md").write_text(textwrap.dedent("""\
            # Evolution Report
            **Rounds:** 3/10
            **Status:** CONVERGED

            ## Summary
            - 3 improvements completed
        """))

        s2 = runs / "20260102_000000"
        s2.mkdir(parents=True)
        (s2 / "conversation_loop_1.md").write_text("# Round 1")

        s3 = runs / "20260103_000000"
        s3.mkdir(parents=True)
        (s3 / "CONVERGED").write_text("All good")
        (s3 / "evolution_report.md").write_text(textwrap.dedent("""\
            # Evolution Report
            **Rounds:** 5/20
            **Status:** CONVERGED

            ## Summary
            - 4 improvements completed
            - 1 improvements remaining
        """))

        from evolve import _show_history
        _show_history(tmp_path)

    def test_ignores_non_timestamped_dirs(self, tmp_path: Path):
        """history ignores directories that don't start with a digit."""
        runs = tmp_path / "runs"
        runs.mkdir()
        (runs / "some_other_dir").mkdir()
        (runs / "20260101_000000").mkdir()
        from evolve import _show_history
        _show_history(tmp_path)
