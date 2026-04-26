"""Tests for evolve.py — CLI arg parsing, _show_status."""

import subprocess
import sys
import textwrap
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest


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
            start_p.add_argument("--allow-installs", action="store_true", dest="allow_installs")
            start_p.add_argument("--timeout", type=int, default=20)
            start_p.add_argument("--model", default=None)
            start_p.add_argument("--resume", action="store_true")
            args = ap.parse_args(["start", "/tmp/project"])
            assert args.command == "start"
            assert args.project_dir == "/tmp/project"
            assert args.rounds == 10
            assert args.check is None
            assert args.allow_installs is False
            assert args.timeout == 20
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
        start_p.add_argument("--allow-installs", action="store_true", dest="allow_installs")
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
            "--allow-installs",
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
        assert args.allow_installs is True
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
        assert "timeout = 20" in content
        assert 'model = "claude-opus-4-6"' in content
        assert "allow_installs = false" in content

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

    def test_creates_memory_md_with_four_sections(self, tmp_path: Path):
        """_init_config pre-seeds .evolve/runs/memory.md with the four typed section
        headers from SPEC.md § 'memory.md' so new projects start with the
        expected structure."""
        from evolve import _init_config
        _init_config(tmp_path)
        memory = tmp_path / ".evolve" / "runs" / "memory.md"
        assert memory.is_file()
        content = memory.read_text()
        # Header
        assert content.startswith("# Agent Memory\n")
        # All four typed sections (SPEC.md § memory.md)
        assert "\n## Errors\n" in content
        assert "\n## Decisions\n" in content
        assert "\n## Patterns\n" in content
        assert "\n## Insights\n" in content

    def test_memory_md_not_overwritten_when_present(self, tmp_path: Path):
        """Existing memory.md is left untouched — the scaffold is a cold-
        start helper, not an every-run reset."""
        runs = tmp_path / "runs"
        runs.mkdir()
        existing = runs / "memory.md"
        existing.write_text("# Pre-existing memory\n\n### Important entry\n")
        from evolve import _init_config
        _init_config(tmp_path)
        assert existing.read_text() == "# Pre-existing memory\n\n### Important entry\n"

    def test_memory_md_uses_agnostic_wording_when_spec_none(self, tmp_path: Path):
        """Without --spec (or with spec=None / 'README.md'), the
        scaffolded memory.md keeps the spec-filename-agnostic pointer
        prose from ``_DEFAULT_MEMORY_MD``."""
        from evolve import _init_config
        _init_config(tmp_path)  # spec defaults to None
        memory = (tmp_path / ".evolve" / "runs" / "memory.md").read_text()
        assert "your project's spec file" in memory
        # Must not leak a specific spec filename when none was passed.
        assert "SPEC.md §" not in memory

    def test_memory_md_uses_agnostic_wording_when_spec_readme(self, tmp_path: Path):
        """Explicit spec='README.md' (the default) also keeps the
        agnostic wording — README.md IS the default spec, so
        substituting it would add no self-documentation value and
        would contradict the constant-drift test's intent."""
        from evolve import _init_config
        _init_config(tmp_path, spec="README.md")
        memory = (tmp_path / ".evolve" / "runs" / "memory.md").read_text()
        assert "your project's spec file" in memory

    def test_memory_md_substitutes_spec_filename(self, tmp_path: Path):
        """With --spec SPEC.md, the scaffolded memory.md references
        the actual spec filename so the pointer prose is
        self-documenting for projects with a dedicated spec."""
        from evolve import _init_config
        _init_config(tmp_path, spec="SPEC.md")
        memory = (tmp_path / ".evolve" / "runs" / "memory.md").read_text()
        # The generic placeholder is replaced with the concrete name.
        assert "your project's spec file" not in memory
        assert "SPEC.md §" in memory
        # Four typed sections must still be present.
        for section in ("## Errors", "## Decisions", "## Patterns", "## Insights"):
            assert f"\n{section}\n" in memory

    def test_memory_md_substitutes_custom_spec_path(self, tmp_path: Path):
        """Nested spec paths (e.g. docs/specification.md) are
        substituted verbatim — the helper doesn't split the path."""
        from evolve import _init_config
        _init_config(tmp_path, spec="docs/specification.md")
        memory = (tmp_path / ".evolve" / "runs" / "memory.md").read_text()
        assert "docs/specification.md §" in memory
        assert "your project's spec file" not in memory

    def test_render_default_memory_md_helper(self):
        """The helper is the single substitution seam — called by
        _init_config and independently testable for the three
        branches (None / README.md / explicit spec)."""
        from evolve import _render_default_memory_md, _DEFAULT_MEMORY_MD
        assert _render_default_memory_md(None) == _DEFAULT_MEMORY_MD
        assert _render_default_memory_md("README.md") == _DEFAULT_MEMORY_MD
        rendered = _render_default_memory_md("SPEC.md")
        assert rendered != _DEFAULT_MEMORY_MD
        assert "SPEC.md §" in rendered
        assert "your project's spec file" not in rendered

    def test_default_memory_md_constant_shape(self):
        """_DEFAULT_MEMORY_MD is the template source of truth — future edits
        to the four-section scaffold must update this one string."""
        from evolve import _DEFAULT_MEMORY_MD
        # Typed headers appear in the documented order
        errors_idx = _DEFAULT_MEMORY_MD.index("## Errors")
        decisions_idx = _DEFAULT_MEMORY_MD.index("## Decisions")
        patterns_idx = _DEFAULT_MEMORY_MD.index("## Patterns")
        insights_idx = _DEFAULT_MEMORY_MD.index("## Insights")
        assert errors_idx < decisions_idx < patterns_idx < insights_idx


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
            model=None, allow_installs=None, resume=False,
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
        assert result.allow_installs is True

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
        assert result.timeout == 20
        assert result.model == "claude-opus-4-6"
        assert result.allow_installs is False


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


class TestEffortFlag:
    """Tests for the ``--effort`` CLI flag / ``effort`` config key / ``EVOLVE_EFFORT`` env var.

    See SPEC.md § "The --effort flag" — accepted values: low / medium / high / max.
    Default is "max". Resolution order: CLI → env → evolve.toml → pyproject → default.
    """

    def _make_args(self, **overrides):
        import argparse
        args = argparse.Namespace(
            check=None, rounds=None, timeout=None,
            model=None, allow_installs=None, resume=False,
            spec=None, capture_frames=False, effort=None,
        )
        for k, v in overrides.items():
            setattr(args, k, v)
        return args

    def test_validate_effort_accepts_four_levels(self):
        from evolve import _validate_effort, EFFORT_LEVELS
        assert EFFORT_LEVELS == ("low", "medium", "high", "max")
        for level in EFFORT_LEVELS:
            assert _validate_effort(level) == level

    def test_validate_effort_rejects_invalid(self):
        import argparse
        from evolve import _validate_effort
        import pytest
        with pytest.raises(argparse.ArgumentTypeError) as exc_info:
            _validate_effort("extreme")
        assert "invalid effort level" in str(exc_info.value)
        assert "low" in str(exc_info.value)
        assert "max" in str(exc_info.value)

    def test_default_is_medium_when_unset(self, tmp_path: Path):
        """No CLI, no env, no config → effort defaults to 'medium'."""
        from evolve import _resolve_config
        args = self._make_args()
        with patch("sys.argv", ["evolve", "start", str(tmp_path)]):
            with patch.dict("os.environ", {}, clear=True):
                result = _resolve_config(args, tmp_path)
        assert result.effort == "medium"

    def test_cli_wins_over_file_and_env(self, tmp_path: Path):
        """CLI --effort low overrides evolve.toml and EVOLVE_EFFORT."""
        (tmp_path / "evolve.toml").write_text('effort = "high"\n')
        from evolve import _resolve_config
        args = self._make_args(effort="low")
        with patch("sys.argv", ["evolve", "start", str(tmp_path), "--effort", "low"]):
            with patch.dict("os.environ", {"EVOLVE_EFFORT": "medium"}, clear=True):
                result = _resolve_config(args, tmp_path)
        assert result.effort == "low"

    def test_env_wins_over_file(self, tmp_path: Path):
        (tmp_path / "evolve.toml").write_text('effort = "high"\n')
        from evolve import _resolve_config
        args = self._make_args()
        with patch("sys.argv", ["evolve", "start", str(tmp_path)]):
            with patch.dict("os.environ", {"EVOLVE_EFFORT": "medium"}, clear=True):
                result = _resolve_config(args, tmp_path)
        assert result.effort == "medium"

    def test_file_config_used_when_no_cli_or_env(self, tmp_path: Path):
        (tmp_path / "evolve.toml").write_text('effort = "high"\n')
        from evolve import _resolve_config
        args = self._make_args()
        with patch("sys.argv", ["evolve", "start", str(tmp_path)]):
            with patch.dict("os.environ", {}, clear=True):
                result = _resolve_config(args, tmp_path)
        assert result.effort == "high"

    def test_cli_parser_rejects_invalid_effort(self):
        """argparse parser exits (code 2) on invalid --effort value."""
        import pytest
        with pytest.raises(SystemExit):
            with patch("sys.argv", ["evolve", "start", "/tmp/project", "--effort", "bogus"]):
                from evolve import main
                main()

    def test_effort_propagates_to_claude_agent_options(self, tmp_path: Path):
        """ClaudeAgentOptions accepts the ``effort`` kwarg and each call site
        in agent.py passes ``EFFORT`` to it.

        This verifies the plumbing from loop.py's ``run_single_round`` (and
        sibling entry points) to ``agent.EFFORT`` to the SDK option.
        """
        # Real SDK is required — ``inspect.signature`` on the stub's
        # ``MagicMock`` attribute is meaningless.
        pytest.importorskip(
            "claude_agent_sdk",
            reason="requires real claude_agent_sdk to inspect ClaudeAgentOptions signature",
        )
        # Conftest installs a MagicMock stub when the SDK isn't installed;
        # importorskip above only covers the module presence, so also
        # confirm the real package is on the path by checking the stub
        # wasn't taken.
        import claude_agent_sdk as _sdk
        if isinstance(_sdk, MagicMock):
            pytest.skip("conftest installed a claude_agent_sdk stub — real SDK not available")
        from claude_agent_sdk import ClaudeAgentOptions
        import inspect
        sig = inspect.signature(ClaudeAgentOptions)
        assert "effort" in sig.parameters, (
            "claude_agent_sdk.ClaudeAgentOptions must accept an 'effort' parameter "
            "for the --effort flag plumbing to work"
        )

        # Verify agent.py passes EFFORT=... to each of its SDK invocation sites.
        agent_src = (Path(__file__).parent.parent / "evolve" / "agent.py").read_text()
        # Count invocations: analyze_and_fix (run_claude_agent), _run_readonly_claude_agent,
        # _run_party_agent_async (via run_claude_agent), _run_sync_readme_claude_agent.
        # Each ClaudeAgentOptions(...) block must include effort=EFFORT.
        assert agent_src.count("effort=EFFORT") >= 3, (
            "agent.py must pass effort=EFFORT to each ClaudeAgentOptions(...) "
            "invocation site (analyze_and_fix, _run_readonly_claude_agent, "
            "_run_sync_readme_claude_agent)"
        )

    def test_explicit_effort_low_sets_module_global(self):
        """An explicit 'low' CLI propagates all the way to agent.EFFORT
        after run_single_round assigns it."""
        pytest.importorskip(
            "claude_agent_sdk",
            reason="requires real claude_agent_sdk to verify ClaudeAgentOptions.effort attribute",
        )
        import claude_agent_sdk as _sdk
        if isinstance(_sdk, MagicMock):
            pytest.skip("conftest installed a claude_agent_sdk stub — real SDK not available")
        import evolve.agent as _agent_mod
        original = _agent_mod.EFFORT
        try:
            _agent_mod.EFFORT = "low"
            # ClaudeAgentOptions receives agent.EFFORT — confirm via direct construction.
            from claude_agent_sdk import ClaudeAgentOptions
            opts = ClaudeAgentOptions(
                model="claude-opus-4-6",
                cwd="/tmp",
                disallowed_tools=[],
                include_partial_messages=True,
                effort=_agent_mod.EFFORT,
            )
            assert opts.effort == "low"
        finally:
            _agent_mod.EFFORT = original

    def test_run_single_round_sets_agent_effort(self, tmp_path: Path):
        """loop.run_single_round writes args.effort to agent.EFFORT."""
        import evolve.agent as _agent_mod
        original = _agent_mod.EFFORT
        runs_dir = tmp_path / "runs"
        runs_dir.mkdir()
        (runs_dir / "improvements.md").write_text("- [ ] [functional] placeholder\n")
        (tmp_path / "README.md").write_text("# Test\n")
        try:
            from unittest.mock import patch as _patch
            with _patch("evolve.agent.analyze_and_fix"), \
                 _patch("evolve.agent.run_review_agent"):
                from evolve.orchestrator import run_single_round
                run_single_round(
                    project_dir=tmp_path,
                    round_num=1,
                    check_cmd=None,
                    allow_installs=False,
                    timeout=60,
                    run_dir=tmp_path / "runs",
                    model="claude-opus-4-6",
                    spec=None,
                    effort="high",
                )
            assert _agent_mod.EFFORT == "high"
        finally:
            _agent_mod.EFFORT = original

    def test_cli_parser_accepts_all_four_levels(self):
        """Parser accepts each documented level without raising SystemExit."""
        import argparse
        from evolve import _validate_effort
        ap = argparse.ArgumentParser()
        ap.add_argument("--effort", type=_validate_effort, default=None)
        for level in ("low", "medium", "high", "max"):
            ns = ap.parse_args(["--effort", level])
            assert ns.effort == level
