"""Lock-in tests for the DDD migration of diff_agent into
``evolve/infrastructure/claude_sdk/diff_agent.py`` (US-081).

Verifies:
(a) all 3 symbols importable from the infrastructure module,
(b) ``is``-identity with the flat shim and agent.py re-exports,
(c) leaf-module invariant (no forbidden top-level imports),
(d) the ``__init__.py`` lazy re-exports point at the infrastructure
    module (not the flat shim).
"""

from __future__ import annotations

import re
from pathlib import Path


_CANONICAL_NAMES = (
    "build_diff_prompt",
    "_run_diff_claude_agent",
    "run_diff_agent",
)


def test_importable_from_infrastructure_module():
    """Every public diff-agent symbol must be importable from the
    new infrastructure path."""
    from evolve.infrastructure.claude_sdk.diff_agent import (  # noqa: F401
        build_diff_prompt,
        _run_diff_claude_agent,
        run_diff_agent,
    )


def test_is_identity_with_flat_shim():
    """``evolve.diff_agent`` shim re-exports must be ``is``-identical
    to the infrastructure module objects."""
    import evolve.diff_agent as flat_mod
    import evolve.infrastructure.claude_sdk.diff_agent as infra_mod

    for name in _CANONICAL_NAMES:
        assert getattr(flat_mod, name) is getattr(infra_mod, name), (
            f"evolve.diff_agent.{name} must be the SAME object as "
            f"evolve.infrastructure.claude_sdk.diff_agent.{name}"
        )


def test_is_identity_with_agent_reexport():
    """``evolve.agent`` re-exports must be ``is``-identical to the
    infrastructure module objects."""
    import evolve.agent as agent_mod
    import evolve.infrastructure.claude_sdk.diff_agent as infra_mod

    for name in _CANONICAL_NAMES:
        assert getattr(agent_mod, name) is getattr(infra_mod, name), (
            f"evolve.agent.{name} must be the SAME object as "
            f"evolve.infrastructure.claude_sdk.diff_agent.{name}"
        )


def test_infrastructure_module_is_leaf():
    """No top-level imports from evolve.agent, evolve.orchestrator,
    evolve.cli, or evolve.oneshot_agents in the infrastructure module."""
    import evolve.infrastructure.claude_sdk.diff_agent as mod

    src = Path(mod.__file__).read_text()
    violations = re.findall(
        r"^from evolve\.(agent|orchestrator|cli|oneshot_agents)( |$|\.)",
        src,
        re.MULTILINE,
    )
    assert violations == [], (
        "evolve/infrastructure/claude_sdk/diff_agent.py must be a leaf "
        f"— no forbidden top-level imports. Found: {violations}"
    )


def test_infrastructure_module_under_500_lines():
    """SPEC § 'Hard rule: source files MUST NOT exceed 500 lines'."""
    import evolve.infrastructure.claude_sdk.diff_agent as mod

    src = Path(mod.__file__).read_text()
    n = src.count("\n") + (0 if src.endswith("\n") else 1)
    assert n <= 500, (
        f"evolve/infrastructure/claude_sdk/diff_agent.py is {n} lines"
    )
