"""Tests for US-077: draft_review.py → infrastructure/claude_sdk/draft_review.py migration."""

from pathlib import Path


_SYMBOLS = [
    "_build_draft_prompt",
    "_run_draft_claude_agent",
    "run_draft_agent",
    "_build_review_prompt",
    "_run_review_claude_agent",
    "run_review_agent",
]


def test_all_symbols_importable_from_infrastructure():
    """Each of the 6 symbols is importable from the new infrastructure module."""
    from evolve.infrastructure.claude_sdk import draft_review

    for name in _SYMBOLS:
        assert hasattr(draft_review, name), f"{name} missing from infrastructure module"


def test_reexport_identity_with_agent():
    """is-equality: evolve.agent.X is evolve.infrastructure.claude_sdk.draft_review.X."""
    import evolve.agent as agent_mod
    from evolve.infrastructure.claude_sdk import draft_review as infra_mod

    for name in _SYMBOLS:
        assert getattr(agent_mod, name) is getattr(infra_mod, name), (
            f"identity mismatch for {name}"
        )


def test_reexport_identity_with_shim():
    """is-equality: evolve.draft_review.X is evolve.infrastructure.claude_sdk.draft_review.X."""
    import evolve.draft_review as shim_mod
    from evolve.infrastructure.claude_sdk import draft_review as infra_mod

    for name in _SYMBOLS:
        assert getattr(shim_mod, name) is getattr(infra_mod, name), (
            f"identity mismatch for {name} via shim"
        )


def test_no_banned_top_level_imports():
    """Infrastructure module has no from evolve.agent/orchestrator/cli top-level imports."""
    src = (
        Path(__file__).resolve().parent.parent
        / "evolve"
        / "infrastructure"
        / "claude_sdk"
        / "draft_review.py"
    ).read_text()

    import re
    for line in src.splitlines():
        stripped = line.lstrip()
        # Only check top-level imports (no leading whitespace)
        if line == stripped and stripped.startswith("from evolve."):
            # Allowed: from evolve.infrastructure.*
            assert stripped.startswith("from evolve.infrastructure."), (
                f"Banned top-level import: {stripped}"
            )
