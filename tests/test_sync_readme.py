"""Tests for the ``evolve sync-readme`` one-shot subcommand.

Covers SPEC.md § "evolve sync-readme":
- Proposal mode (default) writes README_proposal.md without modifying README.md
- Apply mode writes README.md and creates a git commit
- Exit code 1 when README is already in sync (sentinel written by agent)
- Exit code 2 when spec file is missing
- Behavior when --spec is unset / equals README.md (refuse with exit 1)
- CLI argument parsing for the ``sync-readme`` subcommand
- Prompt builder includes spec, current README, output paths, and mode label
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# build_sync_readme_prompt
# ---------------------------------------------------------------------------

class TestBuildSyncReadmePrompt:
    """Tests for agent.build_sync_readme_prompt."""

    def test_includes_spec_text(self, tmp_path: Path):
        from agent import build_sync_readme_prompt
        (tmp_path / "SPEC.md").write_text("# Spec\nFancy feature X\n")
        (tmp_path / "README.md").write_text("# Readme\nOld text\n")
        run_dir = tmp_path / "runs" / "20260424_000000"
        run_dir.mkdir(parents=True)
        prompt = build_sync_readme_prompt(tmp_path, run_dir, spec="SPEC.md")
        assert "Fancy feature X" in prompt
        assert "Old text" in prompt
        assert "SPEC.md" in prompt

    def test_includes_proposal_path_in_default_mode(self, tmp_path: Path):
        from agent import build_sync_readme_prompt
        (tmp_path / "SPEC.md").write_text("# Spec")
        (tmp_path / "README.md").write_text("# R")
        run_dir = tmp_path / "runs" / "s"
        run_dir.mkdir(parents=True)
        prompt = build_sync_readme_prompt(tmp_path, run_dir, spec="SPEC.md", apply=False)
        assert "README_proposal.md" in prompt
        assert "PROPOSAL" in prompt
        # README.md is referenced as the current input but the output path
        # MUST be README_proposal.md, not README.md.
        assert str(tmp_path / "README_proposal.md") in prompt

    def test_includes_readme_path_in_apply_mode(self, tmp_path: Path):
        from agent import build_sync_readme_prompt
        (tmp_path / "SPEC.md").write_text("# Spec")
        (tmp_path / "README.md").write_text("# R")
        run_dir = tmp_path / "runs" / "s"
        run_dir.mkdir(parents=True)
        prompt = build_sync_readme_prompt(tmp_path, run_dir, spec="SPEC.md", apply=True)
        assert "APPLY" in prompt
        # Output path is README.md (not README_proposal.md) in apply mode.
        # Both literal substrings exist; check by absolute path.
        assert str(tmp_path / "README.md") in prompt

    def test_includes_sentinel_path(self, tmp_path: Path):
        from agent import build_sync_readme_prompt, SYNC_README_NO_CHANGES_SENTINEL
        (tmp_path / "SPEC.md").write_text("# Spec")
        (tmp_path / "README.md").write_text("# R")
        run_dir = tmp_path / "runs" / "session1"
        run_dir.mkdir(parents=True)
        prompt = build_sync_readme_prompt(tmp_path, run_dir, spec="SPEC.md")
        assert SYNC_README_NO_CHANGES_SENTINEL in prompt
        assert str(run_dir / SYNC_README_NO_CHANGES_SENTINEL) in prompt

    def test_includes_voice_constraints(self, tmp_path: Path):
        from agent import build_sync_readme_prompt
        (tmp_path / "SPEC.md").write_text("# Spec")
        (tmp_path / "README.md").write_text("# R")
        run_dir = tmp_path / "runs" / "s"
        run_dir.mkdir(parents=True)
        prompt = build_sync_readme_prompt(tmp_path, run_dir, spec="SPEC.md")
        # Tutorial voice / brevity guards from SPEC § "evolve sync-readme".
        lower = prompt.lower()
        assert "tutorial voice" in lower or "brevity" in lower
        assert "do not copy the spec verbatim" in lower
        assert "do not invent features" in lower

    def test_handles_missing_readme(self, tmp_path: Path):
        from agent import build_sync_readme_prompt
        (tmp_path / "SPEC.md").write_text("# Spec\nA, B, C\n")
        run_dir = tmp_path / "runs" / "s"
        run_dir.mkdir(parents=True)
        # No README.md present.
        prompt = build_sync_readme_prompt(tmp_path, run_dir, spec="SPEC.md")
        assert "no README.md" in prompt or "(no README" in prompt


# ---------------------------------------------------------------------------
# run_sync_readme — orchestrator-level exit code logic
# ---------------------------------------------------------------------------

class TestRunSyncReadmeRefusal:
    """Tests for run_sync_readme's no-op refusal when spec IS README."""

    def test_refuses_when_spec_is_none(self, tmp_path: Path):
        from loop import run_sync_readme
        # No --spec → refuse with exit 1, no agent call, no run_dir created.
        with patch("agent.run_sync_readme_agent") as mock_agent:
            rc = run_sync_readme(tmp_path, spec=None, apply=False)
        assert rc == 1
        mock_agent.assert_not_called()
        # No session directory should have been created.
        runs = tmp_path / "runs"
        if runs.exists():
            assert not any(d.is_dir() for d in runs.iterdir())

    def test_refuses_when_spec_equals_readme(self, tmp_path: Path):
        from loop import run_sync_readme
        with patch("agent.run_sync_readme_agent") as mock_agent:
            rc = run_sync_readme(tmp_path, spec="README.md", apply=False)
        assert rc == 1
        mock_agent.assert_not_called()


class TestRunSyncReadmeMissingSpec:
    """Tests for run_sync_readme when the spec file is missing."""

    def test_missing_spec_returns_2(self, tmp_path: Path):
        from loop import run_sync_readme
        # No SPEC.md created.
        with patch("agent.run_sync_readme_agent") as mock_agent:
            rc = run_sync_readme(tmp_path, spec="SPEC.md", apply=False)
        assert rc == 2
        mock_agent.assert_not_called()


class TestRunSyncReadmeProposalMode:
    """Tests for run_sync_readme default (proposal) mode."""

    def test_writes_proposal_returns_0(self, tmp_path: Path):
        from loop import run_sync_readme
        (tmp_path / "SPEC.md").write_text("# Spec")
        (tmp_path / "README.md").write_text("# Readme")

        # Stub agent: write README_proposal.md as the agent would.
        def fake_agent(project_dir, run_dir, spec, apply, max_retries=5):
            (project_dir / "README_proposal.md").write_text(
                "# Readme\n\nUpdated to reflect SPEC.md\n"
            )

        with patch("agent.run_sync_readme_agent", side_effect=fake_agent):
            rc = run_sync_readme(tmp_path, spec="SPEC.md", apply=False)
        assert rc == 0
        # README.md must NOT have been overwritten.
        assert (tmp_path / "README.md").read_text() == "# Readme"
        # Proposal must exist with the agent's output.
        assert (tmp_path / "README_proposal.md").is_file()
        assert "Updated to reflect" in (tmp_path / "README_proposal.md").read_text()

    def test_no_changes_sentinel_returns_1(self, tmp_path: Path):
        from loop import run_sync_readme
        from agent import SYNC_README_NO_CHANGES_SENTINEL

        (tmp_path / "SPEC.md").write_text("# Spec")
        (tmp_path / "README.md").write_text("# Readme")

        # Stub agent: write the no-changes sentinel inside run_dir.
        def fake_agent(project_dir, run_dir, spec, apply, max_retries=5):
            (run_dir / SYNC_README_NO_CHANGES_SENTINEL).write_text(
                "README already in sync"
            )

        with patch("agent.run_sync_readme_agent", side_effect=fake_agent):
            rc = run_sync_readme(tmp_path, spec="SPEC.md", apply=False)
        assert rc == 1
        # No proposal file should have been written.
        assert not (tmp_path / "README_proposal.md").is_file()
        # README must be untouched.
        assert (tmp_path / "README.md").read_text() == "# Readme"

    def test_no_output_returns_2(self, tmp_path: Path):
        from loop import run_sync_readme
        (tmp_path / "SPEC.md").write_text("# Spec")
        (tmp_path / "README.md").write_text("# Readme")

        # Agent writes neither proposal nor sentinel.
        def fake_agent(project_dir, run_dir, spec, apply, max_retries=5):
            pass

        with patch("agent.run_sync_readme_agent", side_effect=fake_agent):
            rc = run_sync_readme(tmp_path, spec="SPEC.md", apply=False)
        assert rc == 2

    def test_agent_exception_returns_2(self, tmp_path: Path):
        from loop import run_sync_readme
        (tmp_path / "SPEC.md").write_text("# Spec")
        (tmp_path / "README.md").write_text("# Readme")

        with patch("agent.run_sync_readme_agent", side_effect=RuntimeError("boom")):
            rc = run_sync_readme(tmp_path, spec="SPEC.md", apply=False)
        assert rc == 2


class TestRunSyncReadmeApplyMode:
    """Tests for run_sync_readme --apply mode (writes README.md, commits)."""

    def _git_init(self, tmp_path: Path) -> None:
        subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
        subprocess.run(["git", "config", "user.email", "t@t"], cwd=tmp_path, check=True)
        subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path, check=True)
        subprocess.run(["git", "config", "commit.gpgsign", "false"], cwd=tmp_path, check=True)

    def test_apply_mode_writes_readme_and_commits(self, tmp_path: Path):
        from loop import run_sync_readme

        self._git_init(tmp_path)
        (tmp_path / "SPEC.md").write_text("# Spec\nfeature X\n")
        (tmp_path / "README.md").write_text("# Old README\n")
        # Initial commit so git status is clean before sync.
        subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=tmp_path, check=True)

        def fake_agent(project_dir, run_dir, spec, apply, max_retries=5):
            assert apply is True
            # Sleep briefly to ensure mtime advances on coarse-grained FSes.
            (project_dir / "README.md").write_text("# New README\n\nfeature X documented\n")

        # Block the network push attempted by _git_commit.
        with patch("agent.run_sync_readme_agent", side_effect=fake_agent), \
             patch("loop._git_commit") as mock_commit:
            rc = run_sync_readme(tmp_path, spec="SPEC.md", apply=True)

        assert rc == 0
        assert "feature X documented" in (tmp_path / "README.md").read_text()
        # Verify _git_commit was called with the documented message.
        assert mock_commit.called
        commit_msg = mock_commit.call_args[0][1]
        assert "docs" in commit_msg.lower()
        assert "readme" in commit_msg.lower()

    def test_apply_mode_unmodified_readme_returns_2(self, tmp_path: Path):
        from loop import run_sync_readme

        self._git_init(tmp_path)
        (tmp_path / "SPEC.md").write_text("# Spec")
        (tmp_path / "README.md").write_text("# Old")
        subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=tmp_path, check=True)

        # Agent does nothing — README.md mtime stays the same.
        def fake_agent(project_dir, run_dir, spec, apply, max_retries=5):
            pass

        with patch("agent.run_sync_readme_agent", side_effect=fake_agent), \
             patch("loop._git_commit") as mock_commit:
            rc = run_sync_readme(tmp_path, spec="SPEC.md", apply=True)
        assert rc == 2
        # No commit should have been attempted.
        mock_commit.assert_not_called()


# ---------------------------------------------------------------------------
# CLI plumbing — `evolve sync-readme` subcommand
# ---------------------------------------------------------------------------

class TestSyncReadmeCLI:
    """Tests for the sync-readme subcommand's CLI integration in evolve.py."""

    def test_subparser_exposes_subcommand(self):
        # Sanity: importing evolve.py and constructing the parser should
        # include 'sync-readme' as a valid subcommand.
        import argparse
        import evolve
        # Re-create the argument parser by parsing a known sync-readme
        # invocation; if the subparser isn't registered, argparse exits.
        with patch.object(sys, "argv", ["evolve", "sync-readme", "--help"]):
            with patch("evolve._check_deps"):
                with pytest.raises(SystemExit) as exc_info:
                    evolve.main()
            # --help exits with code 0
            assert exc_info.value.code == 0

    def test_dispatch_calls_run_sync_readme(self, tmp_path: Path):
        import evolve
        with patch.object(sys, "argv", [
            "evolve", "sync-readme", str(tmp_path), "--spec", "SPEC.md",
        ]):
            with patch("evolve._check_deps"), \
                 patch("loop.run_sync_readme", return_value=0) as mock_run:
                with pytest.raises(SystemExit) as exc_info:
                    evolve.main()
        assert exc_info.value.code == 0
        mock_run.assert_called_once()
        kwargs = mock_run.call_args.kwargs
        assert kwargs["spec"] == "SPEC.md"
        assert kwargs["apply"] is False
        assert kwargs["project_dir"] == tmp_path.resolve()

    def test_dispatch_apply_flag(self, tmp_path: Path):
        import evolve
        with patch.object(sys, "argv", [
            "evolve", "sync-readme", str(tmp_path),
            "--spec", "SPEC.md", "--apply",
        ]):
            with patch("evolve._check_deps"), \
                 patch("loop.run_sync_readme", return_value=0) as mock_run:
                with pytest.raises(SystemExit):
                    evolve.main()
        assert mock_run.call_args.kwargs["apply"] is True

    def test_dispatch_propagates_exit_1(self, tmp_path: Path):
        import evolve
        with patch.object(sys, "argv", [
            "evolve", "sync-readme", str(tmp_path), "--spec", "SPEC.md",
        ]):
            with patch("evolve._check_deps"), \
                 patch("loop.run_sync_readme", return_value=1):
                with pytest.raises(SystemExit) as exc_info:
                    evolve.main()
        assert exc_info.value.code == 1

    def test_dispatch_propagates_exit_2(self, tmp_path: Path):
        import evolve
        with patch.object(sys, "argv", [
            "evolve", "sync-readme", str(tmp_path), "--spec", "SPEC.md",
        ]):
            with patch("evolve._check_deps"), \
                 patch("loop.run_sync_readme", return_value=2):
                with pytest.raises(SystemExit) as exc_info:
                    evolve.main()
        assert exc_info.value.code == 2

    def test_default_project_dir_is_cwd(self, tmp_path: Path, monkeypatch):
        import evolve
        monkeypatch.chdir(tmp_path)
        with patch.object(sys, "argv", ["evolve", "sync-readme", "--spec", "SPEC.md"]):
            with patch("evolve._check_deps"), \
                 patch("loop.run_sync_readme", return_value=0) as mock_run:
                with pytest.raises(SystemExit):
                    evolve.main()
        # Should resolve "." against cwd.
        assert mock_run.call_args.kwargs["project_dir"] == tmp_path.resolve()
