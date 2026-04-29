"""Lock-in tests for the DDD migration of sync-readme into
``evolve.infrastructure.claude_sdk.sync_readme`` (US-080).

Mirrors the pattern of ``tests/test_sync_readme_module.py`` (US-034)
but asserts the infrastructure path is the canonical home.
"""

from __future__ import annotations

import re
from pathlib import Path


_CANONICAL_NAMES = (
    "SYNC_README_NO_CHANGES_SENTINEL",
    "build_sync_readme_prompt",
    "_run_sync_readme_claude_agent",
    "run_sync_readme_agent",
)


def test_canonical_imports_from_infrastructure():
    """Every symbol must be importable from the infrastructure path."""
    from evolve.infrastructure.claude_sdk.sync_readme import (  # noqa: F401
        SYNC_README_NO_CHANGES_SENTINEL,
        build_sync_readme_prompt,
        _run_sync_readme_claude_agent,
        run_sync_readme_agent,
    )


def test_is_identical_through_shim_chain():
    """The re-export chain must preserve object identity:
    infrastructure.claude_sdk.sync_readme → sync_readme (shim) →
    oneshot_agents → agent."""
    import evolve.infrastructure.claude_sdk.sync_readme as infra_mod
    import evolve.agent as agent_mod

    for name in _CANONICAL_NAMES:
        infra_obj = getattr(infra_mod, name)
        agent_obj = getattr(agent_mod, name)
        assert infra_obj is agent_obj, (
            f"evolve.agent.{name} must be the SAME object as "
            f"evolve.infrastructure.claude_sdk.sync_readme.{name}"
        )


def test_infrastructure_module_is_leaf():
    """No top-level imports from evolve.agent, evolve.orchestrator,
    evolve.cli, or evolve.oneshot_agents."""
    import evolve.infrastructure.claude_sdk.sync_readme as mod

    src = Path(mod.__file__).read_text()
    violations = re.findall(
        r"^from evolve\.(agent|orchestrator|cli|oneshot_agents)( |$|\.)",
        src,
        re.MULTILINE,
    )
    assert violations == [], (
        "evolve/infrastructure/claude_sdk/sync_readme.py must remain a "
        f"leaf module. Found top-level imports: {violations}"
    )
