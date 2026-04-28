"""DDD migration test for US-071: evolve/sdk_runner.py → infrastructure/claude_sdk/runner.py.

Verifies:
1. All symbols importable from ``evolve.infrastructure.claude_sdk.runner``
2. ``is``-equality with ``evolve.sdk_runner.X`` (shim identity)
3. No top-level ``from evolve.agent``, ``from evolve.orchestrator``,
   ``from evolve.cli``, or ``from evolve.oneshot_agents`` imports
   (DDD leaf-module invariant)
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

import evolve.infrastructure.claude_sdk.runner as infra_runner_mod
import evolve.sdk_runner as sdk_runner_mod


SYMBOLS = (
    "_build_multimodal_prompt",
    "run_claude_agent",
)


@pytest.mark.parametrize("name", SYMBOLS)
def test_symbol_importable_from_infra_runner(name):
    """Each symbol must be importable from the infrastructure module."""
    assert hasattr(infra_runner_mod, name), (
        f"evolve.infrastructure.claude_sdk.runner must define {name}"
    )


@pytest.mark.parametrize("name", SYMBOLS)
def test_identity_with_shim(name):
    """``is``-equality between shim and infrastructure module."""
    assert getattr(sdk_runner_mod, name) is getattr(infra_runner_mod, name), (
        f"evolve.sdk_runner.{name} must be the same object as "
        f"evolve.infrastructure.claude_sdk.runner.{name}"
    )


def test_no_forbidden_top_level_imports():
    """Infrastructure runner must not import from legacy agent/orchestrator/cli."""
    src = Path(infra_runner_mod.__file__).read_text()
    forbidden = re.compile(
        r"^from evolve\.(agent|orchestrator|cli|oneshot_agents)( |$|\.)",
        re.MULTILINE,
    )
    matches = forbidden.findall(src)
    assert not matches, (
        f"evolve/infrastructure/claude_sdk/runner.py must be a DDD leaf. "
        f"Found: {matches}"
    )
