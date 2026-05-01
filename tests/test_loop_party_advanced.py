"""Advanced party-mode coverage tests — split from test_loop_party_coverage.py.

Covers agent loading, prompt content, missing workflow handling, and
end-to-end party flow. The companion file ``test_loop_party_coverage.py``
keeps the result-handling and retry-path tests.
"""

from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from evolve.infrastructure.claude_sdk.party import _run_party_mode


# ---------------------------------------------------------------------------
# Agent persona loading from agents/*.md
# ---------------------------------------------------------------------------

class TestPartyModeAgentLoading:
    """Test _run_party_mode agent persona loading from agents/*.md."""

    def test_multiple_agents_loaded_sorted(self, tmp_path: Path):
        """Multiple agent personas are loaded in sorted order and included in prompt."""
        agents = tmp_path / "agents"
        agents.mkdir()
        (agents / "charlie.md").write_text("# Charlie — backend expert")
        (agents / "alice.md").write_text("# Alice — frontend lead")
        (agents / "bob.md").write_text("# Bob — devops guru")

        run_dir = tmp_path / "runs" / "session"
        run_dir.mkdir(parents=True)
        (tmp_path / "README.md").write_text("# My Project")
        (tmp_path / "runs" / "improvements.md").write_text("- [x] done\n")
        (tmp_path / "runs" / "memory.md").write_text("# Memory\n")
        (run_dir / "CONVERGED").write_text("Fully converged")

        ui = MagicMock()
        captured_prompt = {}

        import asyncio as _asyncio
        import evolve.infrastructure.claude_sdk.runtime as agent_mod

        def mock_run_agent(prompt, project_dir, round_num=0, run_dir=None, log_filename=None):
            captured_prompt["value"] = prompt
            return MagicMock()

        def mock_asyncio_run(coro):
            coro.close()
            (run_dir / "party_report.md").write_text("# Party Report\n")
            (run_dir / "README_proposal.md").write_text("# New README\n")

        with patch.object(agent_mod, 'run_claude_agent', side_effect=mock_run_agent) as mock_agent, \
             patch.object(_asyncio, 'run', side_effect=mock_asyncio_run):
            _run_party_mode(tmp_path, run_dir, ui)

        mock_agent.assert_called_once()
        prompt = mock_agent.call_args[0][0]
        alice_pos = prompt.index("alice.md")
        bob_pos = prompt.index("bob.md")
        charlie_pos = prompt.index("charlie.md")
        assert alice_pos < bob_pos < charlie_pos

        assert "# Alice — frontend lead" in prompt
        assert "# Bob — devops guru" in prompt
        assert "# Charlie — backend expert" in prompt

        assert "- alice.md" in prompt
        assert "- bob.md" in prompt
        assert "- charlie.md" in prompt

    def test_project_agents_dir_preferred_over_evolve(self, tmp_path: Path):
        """Project-level agents/ dir is used when it exists."""
        agents = tmp_path / "agents"
        agents.mkdir()
        (agents / "custom.md").write_text("# Custom project agent")

        run_dir = tmp_path / "runs" / "session"
        run_dir.mkdir(parents=True)
        (tmp_path / "README.md").write_text("# Test")
        (tmp_path / "runs" / "improvements.md").write_text("- [x] done\n")

        ui = MagicMock()
        import asyncio as _asyncio
        import evolve.infrastructure.claude_sdk.runtime as agent_mod

        with patch.object(agent_mod, 'run_claude_agent', return_value=MagicMock()), \
             patch.object(_asyncio, 'run', side_effect=lambda c: c.close()):
            _run_party_mode(tmp_path, run_dir, ui)

        for call_args in ui.warn.call_args_list:
            assert "No agent personas" not in str(call_args)
        ui.party_mode.assert_called_once()

    def test_fallback_to_evolve_agents_dir(self, tmp_path: Path):
        """Falls back to evolve's own agents/ when project has none."""
        run_dir = tmp_path / "runs" / "session"
        run_dir.mkdir(parents=True)
        (tmp_path / "README.md").write_text("# Test")
        (tmp_path / "runs" / "improvements.md").write_text("- [x] done\n")

        ui = MagicMock()

        import evolve.infrastructure.claude_sdk.party as party_mod
        evolve_agents = Path(party_mod.__file__).parent.parent / "agents"

        if evolve_agents.is_dir() and list(evolve_agents.glob("*.md")):
            import asyncio as _asyncio
            import evolve.infrastructure.claude_sdk.runtime as agent_mod

            with patch.object(agent_mod, 'run_claude_agent', return_value=MagicMock()), \
                 patch.object(_asyncio, 'run', side_effect=lambda c: c.close()):
                _run_party_mode(tmp_path, run_dir, ui)

            ui.party_mode.assert_called_once()
        else:
            _run_party_mode(tmp_path, run_dir, ui)
            ui.warn.assert_called_with("No agent personas found — skipping party mode")


# ---------------------------------------------------------------------------
# Prompt content verification
# ---------------------------------------------------------------------------

class TestPartyModePromptContent:
    """Verify prompt content includes all required context."""

    def test_prompt_includes_context_files(self, tmp_path: Path):
        """Prompt includes README, improvements, memory, and convergence reason."""
        agents = tmp_path / "agents"
        agents.mkdir()
        (agents / "dev.md").write_text("# Dev persona")

        run_dir = tmp_path / "runs" / "session"
        run_dir.mkdir(parents=True)
        (tmp_path / "README.md").write_text("# My Unique Project README")
        (tmp_path / "runs" / "improvements.md").write_text("- [x] [functional] unique improvement item\n")
        (tmp_path / "runs" / "memory.md").write_text("# Memory\n## Error: unique memory entry\n")
        (run_dir / "CONVERGED").write_text("Unique convergence reason here")

        ui = MagicMock()
        import asyncio as _asyncio
        import evolve.infrastructure.claude_sdk.runtime as agent_mod

        with patch.object(agent_mod, 'run_claude_agent', return_value=MagicMock()) as mock_agent, \
             patch.object(_asyncio, 'run', side_effect=lambda c: c.close()):
            _run_party_mode(tmp_path, run_dir, ui)

        prompt = mock_agent.call_args[0][0]
        assert "# My Unique Project README" in prompt
        assert "unique improvement item" in prompt
        assert "unique memory entry" in prompt
        assert "Unique convergence reason here" in prompt

    def test_prompt_handles_missing_context_files(self, tmp_path: Path):
        """Prompt uses '(none)' when README/improvements/memory are missing."""
        agents = tmp_path / "agents"
        agents.mkdir()
        (agents / "dev.md").write_text("# Dev persona")

        run_dir = tmp_path / "runs" / "session"
        run_dir.mkdir(parents=True)

        ui = MagicMock()
        import asyncio as _asyncio
        import evolve.infrastructure.claude_sdk.runtime as agent_mod

        with patch.object(agent_mod, 'run_claude_agent', return_value=MagicMock()) as mock_agent, \
             patch.object(_asyncio, 'run', side_effect=lambda c: c.close()):
            _run_party_mode(tmp_path, run_dir, ui)

        prompt = mock_agent.call_args[0][0]
        assert "(none)" in prompt

    def test_prompt_includes_workflow_content(self, tmp_path: Path):
        """When workflow files exist, their content appears in the prompt."""
        agents = tmp_path / "agents"
        agents.mkdir()
        (agents / "dev.md").write_text("# Dev")

        run_dir = tmp_path / "runs" / "session"
        run_dir.mkdir(parents=True)
        (tmp_path / "README.md").write_text("# Test")
        (tmp_path / "runs" / "improvements.md").write_text("- [x] done\n")

        ui = MagicMock()
        import asyncio as _asyncio
        import evolve.infrastructure.claude_sdk.runtime as agent_mod
        import evolve.infrastructure.claude_sdk.party as party_mod

        wf_dir = Path(party_mod.__file__).parent.parent / "workflows" / "party-mode"
        wf_existed = wf_dir.is_dir()

        if not wf_existed:
            wf_dir.mkdir(parents=True, exist_ok=True)
            (wf_dir / "workflow.md").write_text("# Unique Workflow Content XYZ")
            steps = wf_dir / "steps"
            steps.mkdir(exist_ok=True)
            (steps / "step-01.md").write_text("# Step 1 unique content ABC")

        try:
            with patch.object(agent_mod, 'run_claude_agent', return_value=MagicMock()) as mock_agent, \
                 patch.object(_asyncio, 'run', side_effect=lambda c: c.close()):
                _run_party_mode(tmp_path, run_dir, ui)

            prompt = mock_agent.call_args[0][0]
            if not wf_existed:
                assert "Unique Workflow Content XYZ" in prompt
                assert "Step 1 unique content ABC" in prompt
        finally:
            if not wf_existed and wf_dir.is_dir():
                import shutil
                shutil.rmtree(wf_dir)


# ---------------------------------------------------------------------------
# Missing workflow handling
# ---------------------------------------------------------------------------

class TestPartyModeMissingWorkflow:
    """Test _run_party_mode when no workflow directory exists."""

    def test_no_workflow_dir_anywhere(self, tmp_path: Path):
        """Party mode proceeds with empty workflow when no workflow dir exists."""
        agents = tmp_path / "agents"
        agents.mkdir()
        (agents / "dev.md").write_text("# Dev Agent")

        run_dir = tmp_path / "runs" / "session"
        run_dir.mkdir(parents=True)
        (tmp_path / "README.md").write_text("# Test")
        (tmp_path / "runs" / "improvements.md").write_text("- [x] done\n")

        ui = MagicMock()
        import asyncio as _asyncio
        import evolve.infrastructure.claude_sdk.runtime as agent_mod
        import evolve.application.run_loop as loop_mod

        real_parent = Path(loop_mod.__file__).parent
        real_is_dir = Path.is_dir

        def patched_is_dir(self_path):
            if "workflows" in str(self_path) and "party-mode" in str(self_path):
                return False
            return real_is_dir(self_path)

        with patch.object(Path, "is_dir", patched_is_dir), \
             patch.object(agent_mod, 'run_claude_agent', return_value=MagicMock()) as mock_agent, \
             patch.object(_asyncio, 'run', side_effect=lambda c: c.close()):
            _run_party_mode(tmp_path, run_dir, ui)

        mock_agent.assert_called_once()
        prompt = mock_agent.call_args[0][0]
        assert "## Workflow" in prompt
        ui.party_mode.assert_called_once()

    def test_workflow_dir_exists_but_empty(self, tmp_path: Path):
        """Party mode proceeds when workflow dir exists but has no files."""
        agents = tmp_path / "agents"
        agents.mkdir()
        (agents / "dev.md").write_text("# Dev Agent")

        wf_dir = tmp_path / "workflows" / "party-mode"
        wf_dir.mkdir(parents=True)

        run_dir = tmp_path / "runs" / "session"
        run_dir.mkdir(parents=True)
        (tmp_path / "README.md").write_text("# Test")
        (tmp_path / "runs" / "improvements.md").write_text("- [x] done\n")

        ui = MagicMock()
        import asyncio as _asyncio
        import evolve.infrastructure.claude_sdk.runtime as agent_mod
        import evolve.application.run_loop as loop_mod

        real_parent = Path(loop_mod.__file__).parent
        real_is_dir = Path.is_dir

        def patched_is_dir(self_path):
            if str(self_path) == str(real_parent / "workflows" / "party-mode"):
                return False
            return real_is_dir(self_path)

        with patch.object(Path, "is_dir", patched_is_dir), \
             patch.object(agent_mod, 'run_claude_agent', return_value=MagicMock()) as mock_agent, \
             patch.object(_asyncio, 'run', side_effect=lambda c: c.close()):
            _run_party_mode(tmp_path, run_dir, ui)

        mock_agent.assert_called_once()
        prompt = mock_agent.call_args[0][0]
        assert "## Workflow" in prompt


# ---------------------------------------------------------------------------
# End-to-end party-mode tests
# ---------------------------------------------------------------------------

class TestPartyModeEndToEnd:
    """End-to-end party mode tests verifying file creation and UI calls."""

    def test_successful_run_creates_files_and_calls_ui(self, tmp_path: Path):
        """Full successful party mode run creates both output files and calls all UI methods."""
        agents = tmp_path / "agents"
        agents.mkdir()
        (agents / "dev.md").write_text("# Dev")
        (agents / "pm.md").write_text("# PM")

        run_dir = tmp_path / "runs" / "session"
        run_dir.mkdir(parents=True)
        (tmp_path / "README.md").write_text("# Project")
        (tmp_path / "runs" / "improvements.md").write_text("- [x] done\n")
        (tmp_path / "runs" / "memory.md").write_text("# Memory\n")
        (run_dir / "CONVERGED").write_text("Done")

        ui = MagicMock()
        import asyncio as _asyncio
        import evolve.infrastructure.claude_sdk.runtime as agent_mod

        def mock_asyncio_run(coro):
            coro.close()
            (run_dir / "party_report.md").write_text("# Party Report\nAgents discussed improvements.\n")
            (run_dir / "README_proposal.md").write_text("# Updated README\nNew features proposed.\n")

        with patch.object(agent_mod, 'run_claude_agent', return_value=MagicMock()), \
             patch.object(_asyncio, 'run', side_effect=mock_asyncio_run):
            _run_party_mode(tmp_path, run_dir, ui)

        ui.party_mode.assert_called_once()
        ui.party_results.assert_called_once_with(
            str(run_dir / "README_proposal.md"),
            str(run_dir / "party_report.md"),
        )
        ui.warn.assert_not_called()

        assert (run_dir / "party_report.md").read_text() == "# Party Report\nAgents discussed improvements.\n"
        assert (run_dir / "README_proposal.md").read_text() == "# Updated README\nNew features proposed.\n"

    def test_agent_called_with_correct_args(self, tmp_path: Path):
        """run_claude_agent is called with correct project_dir, round_num, run_dir, log_filename."""
        agents = tmp_path / "agents"
        agents.mkdir()
        (agents / "dev.md").write_text("# Dev")

        run_dir = tmp_path / "runs" / "session"
        run_dir.mkdir(parents=True)
        (tmp_path / "README.md").write_text("# Test")
        (tmp_path / "runs" / "improvements.md").write_text("- [x] done\n")

        ui = MagicMock()
        import asyncio as _asyncio
        import evolve.infrastructure.claude_sdk.runtime as agent_mod

        with patch.object(agent_mod, 'run_claude_agent', return_value=MagicMock()) as mock_agent, \
             patch.object(_asyncio, 'run', side_effect=lambda c: c.close()):
            _run_party_mode(tmp_path, run_dir, ui)

        mock_agent.assert_called_once()
        _, kwargs = mock_agent.call_args
        args = mock_agent.call_args[0]
        assert args[1] == tmp_path
        assert kwargs.get("round_num", mock_agent.call_args[0][2] if len(args) > 2 else None) is not None
        assert "log_filename" in kwargs or len(args) > 4
        if "log_filename" in kwargs:
            assert kwargs["log_filename"] == "party_conversation.md"

    def test_ui_none_creates_default_tui(self, tmp_path: Path):
        """When ui=None, _run_party_mode creates a default TUI via get_tui()."""
        agents = tmp_path / "agents"
        agents.mkdir()
        (agents / "dev.md").write_text("# Dev")

        run_dir = tmp_path / "runs" / "session"
        run_dir.mkdir(parents=True)
        (tmp_path / "README.md").write_text("# Test")
        (tmp_path / "runs" / "improvements.md").write_text("- [x] done\n")

        import asyncio as _asyncio
        import evolve.infrastructure.claude_sdk.runtime as agent_mod

        mock_ui = MagicMock()

        with patch("evolve.infrastructure.claude_sdk.party.get_tui", return_value=mock_ui), \
             patch.object(agent_mod, 'run_claude_agent', return_value=MagicMock()), \
             patch.object(_asyncio, 'run', side_effect=lambda c: c.close()):
            _run_party_mode(tmp_path, run_dir, ui=None)

        mock_ui.party_mode.assert_called_once()
