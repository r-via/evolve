"""Tests for evolve/infrastructure/claude_sdk/prompt_builder.py (US-073).

Verifies:
  (a) All 4 symbols importable from evolve.infrastructure.claude_sdk.prompt_builder
  (b) is-identity with evolve.agent.X re-exports
  (c) No top-level imports from evolve.agent, evolve.orchestrator, evolve.cli
"""

from pathlib import Path

import evolve.agent as agent_mod
import evolve.infrastructure.claude_sdk.prompt_builder as infra_pb


def test_symbols_importable():
    """All 4 symbols importable from the infrastructure module."""
    assert hasattr(infra_pb, "PromptBlocks")
    assert hasattr(infra_pb, "_load_project_context")
    assert hasattr(infra_pb, "build_prompt_blocks")
    assert hasattr(infra_pb, "build_prompt")


def test_is_identity_with_agent_reexports():
    """Re-export chain preserves is-identity."""
    assert agent_mod.PromptBlocks is infra_pb.PromptBlocks
    assert agent_mod._load_project_context is infra_pb._load_project_context
    assert agent_mod.build_prompt_blocks is infra_pb.build_prompt_blocks
    assert agent_mod.build_prompt is infra_pb.build_prompt


def test_no_forbidden_top_level_imports():
    """Infrastructure file has no top-level evolve.agent/orchestrator/cli imports."""
    src = Path(infra_pb.__file__).read_text()
    for line in src.splitlines():
        stripped = line.lstrip()
        # Skip indented lines (function-local imports are fine via `from evolve import`)
        if line != stripped:
            continue
        # Top-level import lines only
        if stripped.startswith("from evolve.agent") or \
           stripped.startswith("from evolve.orchestrator") or \
           stripped.startswith("from evolve.cli") or \
           stripped.startswith("from evolve.prompt_diagnostics") or \
           stripped.startswith("from evolve.prompt_builder"):
            assert False, f"Forbidden top-level import: {stripped}"


def test_prompt_builder_shim_is_thin():
    """The shim at evolve/prompt_builder.py is under 50 lines."""
    shim = Path(infra_pb.__file__).resolve().parent.parent.parent / "prompt_builder.py"
    assert shim.is_file()
    lines = len(shim.read_text().splitlines())
    assert lines < 50, f"Shim is {lines} lines, expected < 50"
