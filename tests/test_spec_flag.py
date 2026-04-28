"""Tests for the --spec flag across evolve.py, agent.py, and loop.py.

Verifies:
- evolve.py exits with code 2 when the spec file doesn't exist
- _load_project_context reads the correct spec file
- _forever_restart derives the correct proposal filename (e.g. SPEC_proposal.md)
- _run_party_mode produces proposal files named after the spec
- build_prompt / build_validate_prompt / build_dry_run_prompt use the spec param
"""

import sys
import textwrap
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from evolve.agent import (
    _load_project_context,
    build_prompt,
    build_validate_prompt,
    build_dry_run_prompt,
)
from evolve.party import _forever_restart, _run_party_mode

class TestSpecFileNotFound:

    def test_start_with_missing_spec_exits_2(self, tmp_path: Path):
        project_dir = tmp_path / "proj"
        project_dir.mkdir()
        (project_dir / "README.md").write_text("# Hello\n")

        with patch("sys.argv", [
            "evolve", "start", str(project_dir), "--spec", "NONEXISTENT.md",
        ]), patch("evolve._check_deps"), pytest.raises(SystemExit) as exc:
            from evolve import main
            main()

        assert exc.value.code == 2

    def test_start_with_missing_nested_spec_exits_2(self, tmp_path: Path):
        project_dir = tmp_path / "proj"
        project_dir.mkdir()
        (project_dir / "README.md").write_text("# Hello\n")

        with patch("sys.argv", [
            "evolve", "start", str(project_dir), "--spec", "docs/spec.md",
        ]), patch("evolve._check_deps"), pytest.raises(SystemExit) as exc:
            from evolve import main
            main()

        assert exc.value.code == 2

    def test_start_with_existing_spec_does_not_exit_2(self, tmp_path: Path):
        project_dir = tmp_path / "proj"
        project_dir.mkdir()
        (project_dir / "SPEC.md").write_text("# Spec\n")
        (project_dir / "runs").mkdir()

        # It will try to call evolve_loop, which we mock to prevent actual execution
        with patch("sys.argv", [
            "evolve", "start", str(project_dir), "--spec", "SPEC.md",
        ]), patch("evolve._check_deps"), patch("evolve.orchestrator.evolve_loop") as mock_loop:
            from evolve import main
            main()

        # Should have called evolve_loop with spec="SPEC.md"
        mock_loop.assert_called_once()
        call_kwargs = mock_loop.call_args
        assert call_kwargs[1].get("spec") == "SPEC.md" or \
            (len(call_kwargs[0]) > 0 and "SPEC.md" in str(call_kwargs))

class TestLoadProjectContextSpec:

    def test_reads_custom_spec_file(self, tmp_path: Path):
        (tmp_path / "SPEC.md").write_text("# Custom Spec\nSpec content here")
        (tmp_path / "README.md").write_text("# README\nReadme content")
        (tmp_path / "runs").mkdir()

        ctx = _load_project_context(tmp_path, spec="SPEC.md")
        assert ctx["readme"] == "# Custom Spec\nSpec content here"

    def test_reads_nested_spec_file(self, tmp_path: Path):
        docs = tmp_path / "docs"
        docs.mkdir()
        (docs / "specification.md").write_text("# Nested Spec\nDetails")
        (tmp_path / "README.md").write_text("# README\n")
        (tmp_path / "runs").mkdir()

        ctx = _load_project_context(tmp_path, spec="docs/specification.md")
        assert ctx["readme"] == "# Nested Spec\nDetails"

    def test_missing_spec_file_returns_empty(self, tmp_path: Path):
        (tmp_path / "README.md").write_text("# README\n")
        (tmp_path / "runs").mkdir()

        ctx = _load_project_context(tmp_path, spec="MISSING.md")
        assert ctx["readme"] == ""

    def test_no_spec_falls_back_to_readme(self, tmp_path: Path):
        (tmp_path / "README.md").write_text("# Fallback README\n")
        (tmp_path / "runs").mkdir()

        ctx = _load_project_context(tmp_path, spec=None)
        assert ctx["readme"] == "# Fallback README\n"

    def test_improvements_loaded_regardless_of_spec(self, tmp_path: Path):
        (tmp_path / "SPEC.md").write_text("# Spec\n")
        runs = tmp_path / "runs"
        runs.mkdir()
        (runs / "improvements.md").write_text("- [ ] [functional] Add X\n")

        ctx = _load_project_context(tmp_path, spec="SPEC.md")
        assert "Add X" in ctx["improvements"]

class TestBuildPromptSpec:

    def test_build_prompt_uses_custom_spec(self, tmp_path: Path):
        (tmp_path / "SPEC.md").write_text("# Custom Specification\nFeature A")
        (tmp_path / "README.md").write_text("# README\nThis is the readme")
        (tmp_path / "runs").mkdir()

        prompt = build_prompt(tmp_path, spec="SPEC.md")
        assert "Custom Specification" in prompt
        assert "Feature A" in prompt

    def test_build_validate_prompt_uses_custom_spec(self, tmp_path: Path):
        (tmp_path / "SPEC.md").write_text("# Spec for Validation\nClaim B")
        (tmp_path / "README.md").write_text("# README\n")
        (tmp_path / "runs").mkdir()

        prompt = build_validate_prompt(tmp_path, spec="SPEC.md")
        assert "Spec for Validation" in prompt
        assert "Claim B" in prompt

    def test_build_dry_run_prompt_uses_custom_spec(self, tmp_path: Path):
        (tmp_path / "SPEC.md").write_text("# Spec for DryRun\nGap analysis")
        (tmp_path / "README.md").write_text("# README\n")
        (tmp_path / "runs").mkdir()

        prompt = build_dry_run_prompt(tmp_path, spec="SPEC.md")
        assert "Spec for DryRun" in prompt
        assert "Gap analysis" in prompt

class TestForeverRestartSpec:

    def setup_method(self):
        self.ui = MagicMock()

    def test_spec_md_proposal_filename(self, tmp_path: Path):
        run_dir = tmp_path / "runs" / "session1"
        run_dir.mkdir(parents=True)
        improvements = tmp_path / "runs" / "improvements.md"
        improvements.write_text("- [x] [functional] Done\n")

        # Create SPEC.md and SPEC_proposal.md
        (tmp_path / "SPEC.md").write_text("# Old Spec\n")
        (run_dir / "SPEC_proposal.md").write_text("# New Spec\nUpdated content\n")

        _forever_restart(tmp_path, run_dir, improvements, self.ui, spec="SPEC.md")

        # SPEC.md should be updated with proposal content
        assert (tmp_path / "SPEC.md").read_text() == "# New Spec\nUpdated content\n"
        # improvements.md should be reset
        assert improvements.read_text() == "# Improvements\n"

    def test_nested_spec_proposal_filename(self, tmp_path: Path):
        run_dir = tmp_path / "runs" / "session1"
        run_dir.mkdir(parents=True)
        improvements = tmp_path / "runs" / "improvements.md"
        improvements.write_text("- [x] [functional] Done\n")

        docs = tmp_path / "docs"
        docs.mkdir()
        (docs / "spec.md").write_text("# Old Spec\n")
        (run_dir / "spec_proposal.md").write_text("# Updated Spec\n")

        _forever_restart(tmp_path, run_dir, improvements, self.ui, spec="docs/spec.md")

        assert (docs / "spec.md").read_text() == "# Updated Spec\n"

    def test_no_proposal_file_warns(self, tmp_path: Path):
        run_dir = tmp_path / "runs" / "session1"
        run_dir.mkdir(parents=True)
        improvements = tmp_path / "runs" / "improvements.md"
        improvements.write_text("- [x] [functional] Done\n")

        (tmp_path / "SPEC.md").write_text("# Original Spec\n")

        _forever_restart(tmp_path, run_dir, improvements, self.ui, spec="SPEC.md")

        # SPEC.md unchanged
        assert (tmp_path / "SPEC.md").read_text() == "# Original Spec\n"
        # Should warn about missing proposal
        self.ui.warn.assert_called()
        warn_msg = self.ui.warn.call_args[0][0]
        assert "SPEC_proposal.md" in warn_msg

    def test_default_spec_uses_readme_proposal(self, tmp_path: Path):
        run_dir = tmp_path / "runs" / "session1"
        run_dir.mkdir(parents=True)
        improvements = tmp_path / "runs" / "improvements.md"
        improvements.write_text("- [x] [functional] Done\n")

        (tmp_path / "README.md").write_text("# Old README\n")
        (run_dir / "README_proposal.md").write_text("# New README\n")

        _forever_restart(tmp_path, run_dir, improvements, self.ui, spec=None)

        assert (tmp_path / "README.md").read_text() == "# New README\n"

    def test_spec_with_different_extension(self, tmp_path: Path):
        run_dir = tmp_path / "runs" / "session1"
        run_dir.mkdir(parents=True)
        improvements = tmp_path / "runs" / "improvements.md"
        improvements.write_text("- [x] [functional] Done\n")

        (tmp_path / "SPEC.rst").write_text("Old RST spec\n")
        (run_dir / "SPEC_proposal.rst").write_text("New RST spec\n")

        _forever_restart(tmp_path, run_dir, improvements, self.ui, spec="SPEC.rst")

        assert (tmp_path / "SPEC.rst").read_text() == "New RST spec\n"

class TestRunPartyModeSpec:

    def test_party_mode_uses_spec_for_proposal_name(self, tmp_path: Path):
        # Setup project with agents
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        (agents_dir / "architect.md").write_text("I am the architect agent.")

        # Setup spec and improvements
        (tmp_path / "SPEC.md").write_text("# Custom Spec\nFeature list")
        (tmp_path / "runs").mkdir()
        (tmp_path / "runs" / "improvements.md").write_text("- [x] [functional] Done\n")

        run_dir = tmp_path / "runs" / "session1"
        run_dir.mkdir(parents=True)

        ui = MagicMock()

        # Mock the agent to capture the prompt and create the proposal file
        captured_prompts = []

        async def mock_agent(prompt, project_dir, round_num=0, run_dir=None, log_filename=None, images=None):
            captured_prompts.append(prompt)
            # Simulate agent creating the proposal file
            if run_dir:
                (run_dir / "SPEC_proposal.md").write_text("# Proposed Spec\n")
                (run_dir / "party_report.md").write_text("# Party Report\n")

        with patch("evolve.agent.run_claude_agent", mock_agent), \
             patch("evolve.agent._is_benign_runtime_error", return_value=False), \
             patch("evolve.agent._should_retry_rate_limit", return_value=None):
            _run_party_mode(tmp_path, run_dir, ui, spec="SPEC.md")

        # The prompt should reference SPEC_proposal.md, not README_proposal.md
        assert len(captured_prompts) == 1
        prompt = captured_prompts[0]
        assert "SPEC_proposal.md" in prompt
        assert "Custom Spec" in prompt

    def test_party_mode_default_spec_uses_readme_proposal(self, tmp_path: Path):
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        (agents_dir / "architect.md").write_text("I am the architect agent.")

        (tmp_path / "README.md").write_text("# My Project\n")
        (tmp_path / "runs").mkdir()

        run_dir = tmp_path / "runs" / "session1"
        run_dir.mkdir(parents=True)

        ui = MagicMock()
        captured_prompts = []

        async def mock_agent(prompt, project_dir, round_num=0, run_dir=None, log_filename=None, images=None):
            captured_prompts.append(prompt)

        with patch("evolve.agent.run_claude_agent", mock_agent), \
             patch("evolve.agent._is_benign_runtime_error", return_value=False), \
             patch("evolve.agent._should_retry_rate_limit", return_value=None):
            _run_party_mode(tmp_path, run_dir, ui, spec=None)

        assert len(captured_prompts) == 1
        assert "README_proposal.md" in captured_prompts[0]

    def test_party_mode_checks_spec_named_proposal_file(self, tmp_path: Path):
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        (agents_dir / "architect.md").write_text("Agent persona.")

        (tmp_path / "SPEC.md").write_text("# Spec\n")
        (tmp_path / "runs").mkdir()

        run_dir = tmp_path / "runs" / "session1"
        run_dir.mkdir(parents=True)

        ui = MagicMock()

        async def mock_agent(prompt, project_dir, round_num=0, run_dir=None, log_filename=None, images=None):
            # Simulate agent creating the proposal with the spec-derived name
            if run_dir:
                (run_dir / "SPEC_proposal.md").write_text("# Proposal\n")
                (run_dir / "party_report.md").write_text("# Report\n")

        with patch("evolve.agent.run_claude_agent", mock_agent), \
             patch("evolve.agent._is_benign_runtime_error", return_value=False), \
             patch("evolve.agent._should_retry_rate_limit", return_value=None):
            _run_party_mode(tmp_path, run_dir, ui, spec="SPEC.md")

        # party_results should be called with SPEC_proposal.md path
        ui.party_results.assert_called_once()
        proposal_path = ui.party_results.call_args[0][0]
        assert "SPEC_proposal.md" in proposal_path

class TestParseRoundArgsSpec:

    def test_spec_flag_parsed(self):
        from evolve import _parse_round_args
        with patch("sys.argv", [
            "evolve", "_round", "/tmp/proj",
            "--round-num", "1",
            "--spec", "SPEC.md",
        ]):
            args = _parse_round_args()
            assert args.spec == "SPEC.md"

    def test_spec_flag_defaults_to_none(self):
        from evolve import _parse_round_args
        with patch("sys.argv", [
            "evolve", "_round", "/tmp/proj",
            "--round-num", "1",
        ]):
            args = _parse_round_args()
            assert args.spec is None

class TestResolveConfigSpec:

    def test_spec_from_evolve_toml(self, tmp_path: Path):
        import argparse
        from evolve import _resolve_config

        (tmp_path / "evolve.toml").write_text('spec = "SPEC.md"\n')

        args = argparse.Namespace(
            check=None, rounds=10, timeout=300,
            model=None, allow_installs=False, spec=None,
        )
        with patch("sys.argv", ["evolve", "start", str(tmp_path)]):
            result = _resolve_config(args, tmp_path)

        assert result.spec == "SPEC.md"

    def test_spec_from_env_var(self, tmp_path: Path):
        import argparse
        import os
        from evolve import _resolve_config

        args = argparse.Namespace(
            check=None, rounds=10, timeout=300,
            model=None, allow_installs=False, spec=None,
        )
        with patch("sys.argv", ["evolve", "start", str(tmp_path)]), \
             patch.dict(os.environ, {"EVOLVE_SPEC": "docs/spec.md"}):
            result = _resolve_config(args, tmp_path)

        assert result.spec == "docs/spec.md"

    def test_cli_spec_overrides_config(self, tmp_path: Path):
        import argparse
        from evolve import _resolve_config

        (tmp_path / "evolve.toml").write_text('spec = "SPEC.md"\n')

        args = argparse.Namespace(
            check=None, rounds=10, timeout=300,
            model=None, allow_installs=False, spec="CLI_SPEC.md",
        )
        with patch("sys.argv", ["evolve", "start", str(tmp_path), "--spec", "CLI_SPEC.md"]):
            result = _resolve_config(args, tmp_path)

        assert result.spec == "CLI_SPEC.md"
# --forever mode commit message
class TestForeverAtomicAdoptionCommit:

    def _setup_project(self, tmp_path: Path):
        project_dir = tmp_path / "proj"
        project_dir.mkdir()
        (project_dir / "SPEC.md").write_text("# Old Spec\n")
        (project_dir / "README.md").write_text("# Old README\n")
        (project_dir / "runs").mkdir()
        imp_path = project_dir / "runs" / "improvements.md"
        imp_path.write_text("- [ ] initial\n")
        return project_dir, imp_path

    def _extract_run_dir(self, cmd):
        for i, arg in enumerate(cmd):
            if arg == "--run-dir" and i + 1 < len(cmd):
                return Path(cmd[i + 1])
        return None

    def test_spec_only_commit_message_when_spec_differs(self, tmp_path: Path):
        """With --spec SPEC.md, commit message is a focused feat(spec) adoption
        that references only the spec proposal — README is user-authored and
        never appears in the commit (see SPEC.md § "README as a user-level summary").
        """
        from evolve.orchestrator import evolve_loop

        project_dir, imp_path = self._setup_project(tmp_path)
        call_count = 0

        def mock_monitored(cmd, cwd, ui_, round_num, watchdog_timeout=120):
            nonlocal call_count
            call_count += 1
            run_dir = self._extract_run_dir(cmd)
            if run_dir:
                run_dir.mkdir(parents=True, exist_ok=True)
            if call_count == 1:
                (run_dir / f"conversation_loop_{round_num}.md").write_text("# Converged")
                (run_dir / "CONVERGED").write_text("Done")
                (run_dir / "SPEC_proposal.md").write_text("# New Spec\n")
                imp_path.write_text("- [x] initial\n")
                return 0, "converged", False
            raise SystemExit(42)

        captured_commits: list[str] = []

        def capture_commit(project_dir_, message, ui_=None):
            captured_commits.append(message)

        with patch("evolve.orchestrator._setup_forever_branch"), \
             patch("evolve.orchestrator._ensure_git"), \
             patch("evolve.orchestrator._ensure_runs_layout"), \
             patch("evolve.orchestrator._run_monitored_subprocess", side_effect=mock_monitored), \
             patch("evolve.orchestrator._generate_evolution_report"), \
             patch("evolve.orchestrator._run_party_mode"), \
             patch("evolve.orchestrator._git_commit", side_effect=capture_commit), \
             pytest.raises(SystemExit):
            evolve_loop(project_dir, max_rounds=1, forever=True, spec="SPEC.md")

        adoption_msgs = [m for m in captured_commits if "adopt" in m.lower()]
        assert adoption_msgs, f"no adoption commit found in: {captured_commits}"
        msg = adoption_msgs[0]
        assert msg.startswith("feat(spec): adopt SPEC_proposal")
        assert "SPEC.md updated from SPEC_proposal.md" in msg
        assert "improvements.md reset" in msg
        # README must NOT appear in the adoption commit — README is user-authored
        assert "README" not in msg
        # Spec was adopted, README untouched
        assert (project_dir / "SPEC.md").read_text() == "# New Spec\n"
        assert (project_dir / "README.md").read_text() == "# Old README\n"

    def test_legacy_commit_message_when_spec_is_readme(self, tmp_path: Path):
        from evolve.orchestrator import evolve_loop

        project_dir = tmp_path / "proj"
        project_dir.mkdir()
        (project_dir / "README.md").write_text("# Old\n")
        (project_dir / "runs").mkdir()
        imp_path = project_dir / "runs" / "improvements.md"
        imp_path.write_text("- [ ] initial\n")

        call_count = 0

        def mock_monitored(cmd, cwd, ui_, round_num, watchdog_timeout=120):
            nonlocal call_count
            call_count += 1
            run_dir = self._extract_run_dir(cmd)
            if run_dir:
                run_dir.mkdir(parents=True, exist_ok=True)
            if call_count == 1:
                (run_dir / f"conversation_loop_{round_num}.md").write_text("# Converged")
                (run_dir / "CONVERGED").write_text("Done")
                (run_dir / "README_proposal.md").write_text("# New\n")
                imp_path.write_text("- [x] initial\n")
                return 0, "converged", False
            raise SystemExit(42)

        captured_commits: list[str] = []

        def capture_commit(project_dir_, message, ui_=None):
            captured_commits.append(message)

        with patch("evolve.orchestrator._setup_forever_branch"), \
             patch("evolve.orchestrator._ensure_git"), \
             patch("evolve.orchestrator._ensure_runs_layout"), \
             patch("evolve.orchestrator._run_monitored_subprocess", side_effect=mock_monitored), \
             patch("evolve.orchestrator._generate_evolution_report"), \
             patch("evolve.orchestrator._run_party_mode"), \
             patch("evolve.orchestrator._git_commit", side_effect=capture_commit), \
             pytest.raises(SystemExit):
            evolve_loop(project_dir, max_rounds=1, forever=True)

        adoption_msgs = [m for m in captured_commits if "adopt" in m.lower()]
        assert adoption_msgs
        msg = adoption_msgs[0]
        assert msg.startswith("chore(evolve): forever mode")
