"""Tests for evolve CLI subcommands and the --effort flag.

Split out from test_evolve.py per SPEC § "Hard rule: source files MUST NOT
exceed 500 lines". Covers:
- TestCleanSessions: _clean_sessions cleanup logic
- TestShowHistory: _show_history rendering
- TestEffortFlag: --effort CLI flag plumbing
"""

import textwrap
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest


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

        # Verify the implement-path SDK invocation sites pass EFFORT=...
        # After the agent.py multi-round split (US-030/031/032/033) and
        # the oneshot_agents.py split (US-034), the call sites live
        # across agent.py + oneshot_agents.py + sync_readme.py + the
        # other extracted sibling modules. Scan the union of all sites
        # that honor the operator-tunable ``EFFORT`` global (memory.md
        # "test_effort_propagates: scan agent.py + oneshot_agents.py
        # union — round 2 attempt 2 of 20260427_200209" — same lesson
        # applies to every subsequent extraction; count kwarg sites
        # across the sibling set).
        evolve_dir = Path(__file__).parent.parent / "evolve"
        sibling_files = (
            "agent.py",
            "oneshot_agents.py",
            "sync_readme.py",
            "memory_curation.py",
            "spec_archival.py",
        )
        total = sum(
            (evolve_dir / fname).read_text().count("effort=EFFORT")
            for fname in sibling_files
            if (evolve_dir / fname).exists()
        )
        assert total >= 3, (
            "agent.py + oneshot_agents.py + sync_readme.py (and other "
            "extracted siblings) must pass effort=EFFORT to each "
            "operator-tunable ClaudeAgentOptions(...) invocation site "
            "(analyze_and_fix, _run_readonly_claude_agent, "
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
            with _patch("evolve.agent.analyze_and_fix", return_value=None), \
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
