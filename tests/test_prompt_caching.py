"""Tests for prompt caching contract — SPEC.md § "Prompt caching".

Validates:
1. ``build_prompt_blocks()`` separates static (cached) from dynamic (uncached)
2. Per-round variable content is in the uncached block, not cached
3. No call site passes ``system_prompt`` as a list-of-dicts to the SDK
4. The concatenated prompt has static content before dynamic content
"""

from __future__ import annotations

from pathlib import Path

import pytest

from evolve.infrastructure.claude_sdk.prompt_builder import (
    build_prompt,
    build_prompt_blocks,
    PromptBlocks,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_project(tmp_path: Path, *, spec: str = "README.md") -> Path:
    """Set up a minimal project directory for prompt building."""
    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    (project_dir / spec).write_text("# My Spec\nSome claims.\n")
    runs = project_dir / ".evolve" / "runs"
    runs.mkdir(parents=True)
    (runs / "improvements.md").write_text(
        "# Improvements\n- [ ] [functional] US-001: initial\n"
    )
    (runs / "memory.md").write_text(
        "# Agent Memory\n## Errors\n## Decisions\n## Patterns\n## Insights\n"
    )
    return project_dir


# ---------------------------------------------------------------------------
# AC 4: build_prompt_blocks returns structured cached + uncached
# ---------------------------------------------------------------------------

class TestBuildPromptBlocks:
    """build_prompt_blocks returns a PromptBlocks with proper split."""

    def test_returns_prompt_blocks_namedtuple(self, tmp_path: Path):
        project_dir = _make_project(tmp_path)
        blocks = build_prompt_blocks(project_dir, check_output="ok", check_cmd="pytest")
        assert isinstance(blocks, PromptBlocks)
        assert hasattr(blocks, "cached")
        assert hasattr(blocks, "uncached")

    def test_cached_contains_spec_content(self, tmp_path: Path):
        project_dir = _make_project(tmp_path)
        blocks = build_prompt_blocks(project_dir, check_output="ok", check_cmd="pytest")
        # Spec content (README) is in the cached block
        assert "My Spec" in blocks.cached
        assert "Some claims" in blocks.cached

    def test_uncached_contains_per_round_variables(self, tmp_path: Path):
        """Per-round variables must be in uncached block — SPEC AC3."""
        project_dir = _make_project(tmp_path)
        blocks = build_prompt_blocks(
            project_dir,
            check_output="5 passed in 1.2s",
            check_cmd="pytest",
            round_num=3,
        )
        # Check results are per-round -> uncached
        assert "5 passed in 1.2s" in blocks.uncached
        # Memory section is per-round -> uncached
        assert "Agent Memory" in blocks.uncached
        # Improvements section is per-round -> uncached
        assert "US-001" in blocks.uncached
        # Current target is per-round -> uncached
        assert "Current target" in blocks.uncached

    def test_attempt_marker_in_uncached(self, tmp_path: Path):
        """Attempt marker (Phase 1 escape hatch) must be in uncached."""
        project_dir = _make_project(tmp_path)
        blocks = build_prompt_blocks(project_dir, round_num=1)
        assert "CURRENT ATTEMPT:" in blocks.uncached

    def test_cached_does_not_contain_per_round_variables(self, tmp_path: Path):
        """Cached block must NOT contain per-round variables."""
        project_dir = _make_project(tmp_path)
        run_dir = project_dir / ".evolve" / "runs" / "session"
        run_dir.mkdir(parents=True)
        (run_dir / "check_round_1.txt").write_text("check output round 1")
        blocks = build_prompt_blocks(
            project_dir,
            check_output="UNIQUE_CHECK_MARKER",
            check_cmd="pytest",
            run_dir=run_dir,
            round_num=2,
        )
        # None of these per-round values should be in cached
        assert "UNIQUE_CHECK_MARKER" not in blocks.cached
        assert "CURRENT ATTEMPT:" not in blocks.cached
        assert "check output round 1" not in blocks.cached

    def test_build_prompt_concatenates_cached_then_uncached(self, tmp_path: Path):
        """build_prompt() must output cached content before uncached."""
        project_dir = _make_project(tmp_path)
        full = build_prompt(
            project_dir,
            check_output="MARKER_CHECK_OUTPUT",
            check_cmd="pytest",
        )
        # Cached content (spec) appears before uncached content (check output)
        spec_pos = full.find("My Spec")
        check_pos = full.find("MARKER_CHECK_OUTPUT")
        assert spec_pos >= 0
        assert check_pos >= 0
        assert spec_pos < check_pos, (
            "Static (cached) content must precede dynamic (uncached) content"
        )


# ---------------------------------------------------------------------------
# AC 2: No call site passes system_prompt as a list — source-level guard
# ---------------------------------------------------------------------------

# Split the search needle to avoid self-matching in source-reading tests.
_SPB_NEEDLE = "system_prompt" + "_blocks"


class TestNoListSystemPrompt:
    """SPEC AC2: no call site passes system_prompt=[...] as a list."""

    def test_no_spb_parameter_in_agent(self):
        """The removed parameter must not appear in agent.py."""
        source = Path(__file__).resolve().parent.parent / "evolve" / "agent.py"
        text = source.read_text()
        assert _SPB_NEEDLE not in text, (
            "removed parameter still present in agent.py"
        )

    def test_no_list_system_prompt_in_options(self):
        """No ClaudeAgentOptions call passes system_prompt as a list."""
        source = Path(__file__).resolve().parent.parent / "evolve" / "agent.py"
        text = source.read_text()
        # Should not have the dict-unpacking pattern
        assert '{"system_prompt": ' + _SPB_NEEDLE + '}' not in text
        # Should not pass a list literal to system_prompt in non-comment code
        for line in text.splitlines():
            stripped = line.lstrip()
            if stripped.startswith("#"):
                continue
            assert "system_prompt=[" not in line, (
                f"Found system_prompt=[ in non-comment code: {line.strip()}"
            )

    def test_no_spb_in_party(self):
        """Party mode must not pass list system_prompt."""
        source = Path(__file__).resolve().parent.parent / "evolve" / "party.py"
        text = source.read_text()
        assert _SPB_NEEDLE not in text

    def test_no_spb_in_other_tests(self):
        """Test mocks should not reference the removed parameter."""
        tests_dir = Path(__file__).resolve().parent
        this_file = Path(__file__).resolve().name
        for f in tests_dir.glob("*.py"):
            if f.name == this_file:
                continue  # skip self
            text = f.read_text()
            assert _SPB_NEEDLE not in text, (
                f"{f.name} still references the removed parameter"
            )


# ---------------------------------------------------------------------------
# AC 3: Cached block is deterministic for the session
# ---------------------------------------------------------------------------

class TestCachedBlockDeterminism:
    """The cached block should be stable across rounds within a session."""

    def test_cached_stable_across_different_check_outputs(self, tmp_path: Path):
        """Different check outputs must not change the cached block."""
        project_dir = _make_project(tmp_path)
        blocks1 = build_prompt_blocks(
            project_dir, check_output="3 passed", check_cmd="pytest", round_num=1,
        )
        blocks2 = build_prompt_blocks(
            project_dir, check_output="5 passed", check_cmd="pytest", round_num=1,
        )
        assert blocks1.cached == blocks2.cached

    def test_cached_stable_across_different_memory(self, tmp_path: Path):
        """Different memory.md content must not change the cached block."""
        project_dir = _make_project(tmp_path)
        blocks1 = build_prompt_blocks(
            project_dir, check_cmd="pytest", round_num=1,
        )
        # Change memory
        mem = project_dir / ".evolve" / "runs" / "memory.md"
        mem.write_text("# Agent Memory\n## Errors\n### Bug\nSomething broke\n")
        blocks2 = build_prompt_blocks(
            project_dir, check_cmd="pytest", round_num=1,
        )
        assert blocks1.cached == blocks2.cached
