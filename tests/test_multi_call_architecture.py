"""Tests for the multi-call round architecture.

SPEC § "Multi-call round architecture": a round is three narrowly-
scoped SDK calls (draft → implement → review) rather than one
Opus session that does everything.  These tests cover the plumbing
— prompt loading, agent function existence, orchestrator routing —
without actually invoking the SDK (the ``claude_agent_sdk`` fake
from ``conftest.py`` stubs the API).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from evolve.agent import (
    _build_draft_prompt,
    _build_review_prompt,
    run_draft_agent,
    run_review_agent,
)


class TestDraftPromptBuilder:
    """_build_draft_prompt loads prompts/draft.md and substitutes
    placeholders + project context."""

    def test_prompt_contains_placeholders_substituted(self, tmp_path: Path):
        (tmp_path / "README.md").write_text("# Test project\n")
        run_dir = tmp_path / ".evolve" / "runs" / "session"
        run_dir.mkdir(parents=True)

        prompt = _build_draft_prompt(tmp_path, run_dir, spec="README.md")

        # Project / run_dir / runs_base all substituted.
        assert str(tmp_path) in prompt
        assert str(run_dir) in prompt
        # Spec content injected.
        assert "# Test project" in prompt

    def test_prompt_includes_existing_improvements(self, tmp_path: Path):
        (tmp_path / "README.md").write_text("# T\n")
        runs_base = tmp_path / ".evolve" / "runs"
        runs_base.mkdir(parents=True)
        (runs_base / "improvements.md").write_text(
            "- [x] [functional] done item\n"
        )
        run_dir = runs_base / "session"
        run_dir.mkdir()

        prompt = _build_draft_prompt(tmp_path, run_dir, spec="README.md")

        assert "done item" in prompt

    def test_prompt_references_draft_md_content(self, tmp_path: Path):
        """The prompt template from prompts/draft.md is included —
        evidenced by the template's unique phrases."""
        (tmp_path / "README.md").write_text("# T\n")
        run_dir = tmp_path / ".evolve" / "runs" / "s"
        run_dir.mkdir(parents=True)

        prompt = _build_draft_prompt(tmp_path, run_dir)

        # Signature phrases from prompts/draft.md.
        assert "Winston" in prompt and "John" in prompt
        assert "drafting call" in prompt.lower()
        assert "exactly one new US item" in prompt


class TestReviewPromptBuilder:
    """_build_review_prompt loads prompts/review.md and injects the
    round's implement conversation + git diff + spec."""

    def test_prompt_includes_spec(self, tmp_path: Path):
        (tmp_path / "README.md").write_text("# Review test\n")
        run_dir = tmp_path / ".evolve" / "runs" / "session"
        run_dir.mkdir(parents=True)

        prompt = _build_review_prompt(tmp_path, run_dir, round_num=5)

        assert "# Review test" in prompt
        assert "Zara" in prompt

    def test_prompt_round_num_substituted(self, tmp_path: Path):
        (tmp_path / "README.md").write_text("# T\n")
        run_dir = tmp_path / ".evolve" / "runs" / "s"
        run_dir.mkdir(parents=True)

        prompt = _build_review_prompt(tmp_path, run_dir, round_num=42)

        # review.md template contains {round_num} in the output schema.
        assert "Round 42" in prompt or "round 42" in prompt or "42" in prompt


class TestOrchestratorRouting:
    """Orchestrator picks the right agent based on backlog state."""

    def test_backlog_with_unchecked_calls_implement(self, tmp_path: Path, monkeypatch):
        """When improvements.md has a ``[ ]`` item, analyze_and_fix
        (implement path) is invoked — NOT run_draft_agent.
        """
        project_dir = tmp_path / "proj"
        project_dir.mkdir()
        (project_dir / "README.md").write_text("# T\n")
        runs_base = project_dir / ".evolve" / "runs"
        runs_base.mkdir(parents=True)
        (runs_base / "improvements.md").write_text(
            "- [ ] [functional] [P1] US-001: open item\n"
        )
        run_dir = runs_base / "session"
        run_dir.mkdir()

        import evolve.orchestrator as orch
        import evolve.agent as agent_mod

        # Mock the agent functions so we can see which was invoked.
        called = {"implement": 0, "draft": 0, "review": 0}

        def mock_analyze(**kwargs):
            called["implement"] += 1

        def mock_draft(**kwargs):
            called["draft"] += 1

        def mock_review(**kwargs):
            called["review"] += 1

        monkeypatch.setattr(agent_mod, "analyze_and_fix", mock_analyze)
        monkeypatch.setattr(agent_mod, "run_draft_agent", mock_draft)
        monkeypatch.setattr(agent_mod, "run_review_agent", mock_review)
        # Skip git commit + post-check plumbing
        monkeypatch.setattr(orch, "_git_commit", lambda *a, **kw: None)

        orch._run_single_round_body(
            project_dir=project_dir,
            round_num=1,
            check_cmd=None,
            allow_installs=False,
            timeout=20,
            rdir=run_dir,
            improvements_path=runs_base / "improvements.md",
            ui=MagicMock(),
            spec="README.md",
        )

        assert called["implement"] == 1
        assert called["draft"] == 0
        assert called["review"] == 1  # review runs regardless

    def test_empty_backlog_calls_draft(self, tmp_path: Path, monkeypatch):
        """When improvements.md has zero ``[ ]`` items,
        run_draft_agent is invoked — NOT analyze_and_fix.
        """
        project_dir = tmp_path / "proj"
        project_dir.mkdir()
        (project_dir / "README.md").write_text("# T\n")
        runs_base = project_dir / ".evolve" / "runs"
        runs_base.mkdir(parents=True)
        (runs_base / "improvements.md").write_text(
            "- [x] [functional] all done\n"
        )
        run_dir = runs_base / "session"
        run_dir.mkdir()

        import evolve.orchestrator as orch
        import evolve.agent as agent_mod

        called = {"implement": 0, "draft": 0, "review": 0}
        monkeypatch.setattr(agent_mod, "analyze_and_fix",
                            lambda **kw: called.update(implement=called["implement"] + 1))
        monkeypatch.setattr(agent_mod, "run_draft_agent",
                            lambda **kw: called.update(draft=called["draft"] + 1))
        monkeypatch.setattr(agent_mod, "run_review_agent",
                            lambda **kw: called.update(review=called["review"] + 1))
        monkeypatch.setattr(orch, "_git_commit", lambda *a, **kw: None)

        orch._run_single_round_body(
            project_dir=project_dir,
            round_num=1,
            check_cmd=None,
            allow_installs=False,
            timeout=20,
            rdir=run_dir,
            improvements_path=runs_base / "improvements.md",
            ui=MagicMock(),
            spec="README.md",
        )

        assert called["draft"] == 1
        assert called["implement"] == 0
        assert called["review"] == 1


class TestReviewAgentErrorIsolation:
    """Review agent failures do not sink the round — they log and
    the round continues.  This keeps a broken review path from
    blocking the rest of the pipeline."""

    def test_review_exception_does_not_propagate(self, tmp_path: Path, monkeypatch):
        project_dir = tmp_path / "proj"
        project_dir.mkdir()
        (project_dir / "README.md").write_text("# T\n")
        runs_base = project_dir / ".evolve" / "runs"
        runs_base.mkdir(parents=True)
        (runs_base / "improvements.md").write_text(
            "- [ ] [functional] [P1] US-001: item\n"
        )
        run_dir = runs_base / "session"
        run_dir.mkdir()

        import evolve.orchestrator as orch
        import evolve.agent as agent_mod

        def boom(**kwargs):
            raise RuntimeError("review boom")

        monkeypatch.setattr(agent_mod, "analyze_and_fix", lambda **kw: None)
        monkeypatch.setattr(agent_mod, "run_draft_agent", lambda **kw: None)
        monkeypatch.setattr(agent_mod, "run_review_agent", boom)
        monkeypatch.setattr(orch, "_git_commit", lambda *a, **kw: None)

        # Should not raise — the orchestrator swallows review errors.
        orch._run_single_round_body(
            project_dir=project_dir,
            round_num=1,
            check_cmd=None,
            allow_installs=False,
            timeout=20,
            rdir=run_dir,
            improvements_path=runs_base / "improvements.md",
            ui=MagicMock(),
            spec="README.md",
        )
