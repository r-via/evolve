"""Module-extraction tests for evolve.infrastructure.claude_sdk.oneshot_agents (US-079).

Verifies:
(a) all public symbols importable from the infrastructure module,
(b) re-export identity holds through the shim chain,
(c) no disallowed top-level imports in the infrastructure file,
(d) shim emits DeprecationWarning on import.
"""

import warnings
from pathlib import Path


_CANONICAL_NAMES = (
    "SYNC_README_NO_CHANGES_SENTINEL",
    "_build_check_section",
    "build_validate_prompt",
    "build_dry_run_prompt",
    "_run_readonly_claude_agent",
    "_run_dry_run_claude_agent",
    "run_dry_run_agent",
    "_run_validate_claude_agent",
    "run_validate_agent",
    "build_diff_prompt",
    "_run_diff_claude_agent",
    "run_diff_agent",
    "build_sync_readme_prompt",
    "_run_sync_readme_claude_agent",
    "run_sync_readme_agent",
)


def test_symbols_importable_from_infrastructure():
    """All extracted symbols are importable from the infrastructure module."""
    from evolve.infrastructure.claude_sdk.oneshot_agents import (
        SYNC_README_NO_CHANGES_SENTINEL,
        _build_check_section,
        build_validate_prompt,
        build_dry_run_prompt,
        _run_readonly_claude_agent,
        _run_dry_run_claude_agent,
        run_dry_run_agent,
        _run_validate_claude_agent,
        run_validate_agent,
        build_diff_prompt,
        _run_diff_claude_agent,
        run_diff_agent,
        build_sync_readme_prompt,
        _run_sync_readme_claude_agent,
        run_sync_readme_agent,
    )
    assert callable(_build_check_section)
    assert callable(build_validate_prompt)
    assert callable(run_dry_run_agent)


def test_reexport_identity_oneshot_shim():
    """evolve.oneshot_agents.X is evolve.infrastructure.claude_sdk.oneshot_agents.X."""
    from evolve.infrastructure.claude_sdk import oneshot_agents as infra_mod
    from evolve import oneshot_agents as shim_mod

    for name in _CANONICAL_NAMES:
        infra_obj = getattr(infra_mod, name)
        shim_obj = getattr(shim_mod, name)
        assert shim_obj is infra_obj, (
            f"evolve.oneshot_agents.{name} must be the SAME object "
            f"as evolve.infrastructure.claude_sdk.oneshot_agents.{name}"
        )


def test_reexport_identity_agent():
    """evolve.agent.X is evolve.infrastructure.claude_sdk.oneshot_agents.X."""
    from evolve.infrastructure.claude_sdk import oneshot_agents as infra_mod
    from evolve import agent as agent_mod

    for name in _CANONICAL_NAMES:
        infra_obj = getattr(infra_mod, name)
        agent_obj = getattr(agent_mod, name)
        assert agent_obj is infra_obj, (
            f"evolve.agent.{name} must be the SAME object "
            f"as evolve.infrastructure.claude_sdk.oneshot_agents.{name}"
        )


def test_no_disallowed_top_level_imports():
    """Infrastructure file has no top-level from evolve.agent/orchestrator/cli imports."""
    src = (
        Path(__file__).resolve().parent.parent
        / "evolve"
        / "infrastructure"
        / "claude_sdk"
        / "oneshot_agents.py"
    ).read_text()
    for line in src.splitlines():
        stripped = line.lstrip()
        # Only check top-level (no leading whitespace)
        if line == stripped and stripped.startswith("from evolve."):
            # Allowed: from evolve.infrastructure.*
            assert stripped.startswith("from evolve.infrastructure"), (
                f"Disallowed top-level import: {stripped}"
            )


def test_shim_emits_deprecation_warning():
    """evolve.oneshot_agents shim emits DeprecationWarning on import."""
    import importlib
    import sys

    mod = sys.modules.pop("evolve.oneshot_agents", None)
    try:
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            importlib.import_module("evolve.oneshot_agents")
        deprecation_msgs = [
            w for w in caught
            if issubclass(w.category, DeprecationWarning)
            and "evolve.oneshot_agents" in str(w.message)
        ]
        assert len(deprecation_msgs) >= 1, (
            "evolve.oneshot_agents shim should emit a DeprecationWarning"
        )
    finally:
        if mod is not None:
            sys.modules["evolve.oneshot_agents"] = mod
