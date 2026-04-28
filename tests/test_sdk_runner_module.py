"""Re-export identity + leaf-module invariant for ``evolve/sdk_runner.py``.

After US-071, ``evolve/sdk_runner.py`` is a backward-compat shim that
re-exports from ``evolve.infrastructure.claude_sdk.runner``.  These tests
lock the re-export chain:

1. Each symbol is importable from ``evolve.sdk_runner`` (shim).
2. ``is``-equality holds between ``evolve.agent.X`` and
   ``evolve.sdk_runner.X`` (re-export identity — patches against
   ``evolve.agent.run_claude_agent`` continue to intercept the
   bound name in the ``analyze_and_fix`` call site).
3. ``is``-equality holds between ``evolve.sdk_runner.X`` and
   ``evolve.infrastructure.claude_sdk.runner.X`` (infrastructure
   identity — proves the shim chain is intact).
4. ``evolve/infrastructure/claude_sdk/runner.py`` source contains no
   top-level ``from evolve.agent``, ``from evolve.orchestrator``, or
   ``from evolve.cli`` imports (leaf-module invariant).
5. Both files stay under the 500-line cap.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

import evolve.agent as agent_mod
import evolve.sdk_runner as sdk_runner_mod
import evolve.infrastructure.claude_sdk.runner as infra_runner_mod


HOISTED_SYMBOLS = (
    "_build_multimodal_prompt",
    "run_claude_agent",
)


@pytest.mark.parametrize("name", HOISTED_SYMBOLS)
def test_symbol_importable_from_sdk_runner(name):
    """Every symbol must be importable from ``evolve.sdk_runner``."""
    assert hasattr(sdk_runner_mod, name), (
        f"evolve.sdk_runner must define {name}"
    )


@pytest.mark.parametrize("name", HOISTED_SYMBOLS)
def test_reexport_identity_with_agent(name):
    """``is``-equality between ``evolve.agent.X`` and
    ``evolve.sdk_runner.X``."""
    assert getattr(agent_mod, name) is getattr(sdk_runner_mod, name), (
        f"evolve.agent.{name} must be the same object as "
        f"evolve.sdk_runner.{name} (re-export identity check)."
    )


@pytest.mark.parametrize("name", HOISTED_SYMBOLS)
def test_reexport_identity_with_infrastructure(name):
    """``is``-equality between ``evolve.sdk_runner.X`` and
    ``evolve.infrastructure.claude_sdk.runner.X`` — proves the shim
    chain (agent → sdk_runner → infrastructure) is intact."""
    assert getattr(sdk_runner_mod, name) is getattr(infra_runner_mod, name), (
        f"evolve.sdk_runner.{name} must be the same object as "
        f"evolve.infrastructure.claude_sdk.runner.{name} "
        "(infrastructure identity check)."
    )


def test_infra_runner_is_a_leaf_module():
    """``evolve/infrastructure/claude_sdk/runner.py`` must NOT import
    from ``evolve.agent``, ``evolve.orchestrator``, or ``evolve.cli``
    at module top.

    Function-local imports (notably ``from evolve import agent`` to
    access ``EFFORT``) are indented and do NOT trip this regex.
    """
    src = Path(infra_runner_mod.__file__).read_text()
    forbidden = re.compile(
        r"^from evolve\.(agent|orchestrator|cli)( |$|\.)",
        re.MULTILINE,
    )
    matches = forbidden.findall(src)
    assert not matches, (
        f"evolve/infrastructure/claude_sdk/runner.py must be a leaf module. "
        f"Found forbidden top-level imports: {matches}"
    )


def test_both_files_under_500_line_cap():
    """SPEC § 'Hard rule: source files MUST NOT exceed 500 lines'."""
    for mod in (agent_mod, sdk_runner_mod, infra_runner_mod):
        path = Path(mod.__file__)
        n = len(path.read_text().splitlines())
        assert n <= 500, f"{path.name} has {n} lines, exceeds 500-line cap"
