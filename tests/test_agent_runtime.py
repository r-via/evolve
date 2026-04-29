"""US-030: agent.py split step 1 — runtime constants + SDK helpers
hoisted into ``evolve.agent_runtime``.

These tests lock in:

1. The hoisted symbols are importable from ``evolve.agent_runtime``.
2. ``evolve.agent`` re-exports the same object identity (``is``-equal),
   so existing patch targets like ``patch("evolve.infrastructure.claude_sdk.runtime.MODEL")`` and
   ``monkeypatch.setattr(agent_mod, "_patch_sdk_parser", ...)`` keep
   working.
3. The leaf invariant: ``evolve/agent_runtime.py`` source has zero
   module-top ``from evolve.X`` imports (criterion 2 of US-030).
4. ``DRAFT_EFFORT`` / ``REVIEW_EFFORT`` continue to equal ``"low"`` —
   this overlaps with ``tests/test_per_call_effort.py`` on purpose;
   it ensures the contract survives a future move that might
   accidentally reset the constants.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

import evolve.agent as agent_mod
import evolve.agent_runtime as runtime_mod


HOISTED_NAMES = (
    "MODEL",
    "MAX_TURNS",
    "DRAFT_EFFORT",
    "REVIEW_EFFORT",
    "_patch_sdk_parser",
    "_summarise_tool_input",
    "_run_agent_with_retries",
)


@pytest.mark.parametrize("name", HOISTED_NAMES)
def test_hoisted_symbol_importable_from_runtime(name: str) -> None:
    """Each hoisted name resolves on ``evolve.agent_runtime``."""
    assert hasattr(runtime_mod, name), (
        f"evolve.agent_runtime is missing hoisted symbol {name!r}"
    )


@pytest.mark.parametrize("name", HOISTED_NAMES)
def test_agent_module_reexports_same_object(name: str) -> None:
    """``evolve.agent.X`` is the SAME object as ``evolve.agent_runtime.X``.

    ``is``-equality (not just ``==``) is what makes
    ``monkeypatch.setattr(agent_mod, name, fake)`` and
    ``patch("evolve.agent.<name>")`` actually intercept call sites that
    bind the re-exported name (memory.md "Re-export ≠ patch surface
    when call site uses ``_diag.`` indirection" — same lesson, applied
    here proactively).
    """
    assert getattr(agent_mod, name) is getattr(runtime_mod, name)


def test_agent_runtime_is_a_shim() -> None:
    """``evolve/agent_runtime.py`` is a backward-compat shim.

    After DDD migration (US-069), agent_runtime.py re-exports from
    ``evolve.infrastructure.claude_sdk.runtime``.  The only allowed
    ``from evolve.*`` import is the infrastructure re-export chain.
    """
    src = (Path(__file__).resolve().parent.parent
           / "evolve" / "agent_runtime.py").read_text()
    # All evolve imports must point to the infrastructure package
    matches = re.findall(r"^from evolve\.\S+", src, flags=re.MULTILINE)
    for m in matches:
        assert m.startswith("from evolve.infrastructure.claude_sdk"), (
            f"agent_runtime.py should only import from "
            f"evolve.infrastructure.claude_sdk, found: {m}"
        )


def test_draft_and_review_effort_remain_low() -> None:
    """US-029 contract preserved across the hoist."""
    assert runtime_mod.DRAFT_EFFORT == "low"
    assert runtime_mod.REVIEW_EFFORT == "low"
    # Re-export identity already covered by parametrized test above;
    # this one is the value-level smoke check.
    assert agent_mod.DRAFT_EFFORT == "low"
    assert agent_mod.REVIEW_EFFORT == "low"


def test_model_and_max_turns_constants() -> None:
    """``MODEL`` is a non-empty string; ``MAX_TURNS`` is a positive int."""
    assert isinstance(runtime_mod.MODEL, str) and runtime_mod.MODEL
    assert isinstance(runtime_mod.MAX_TURNS, int) and runtime_mod.MAX_TURNS > 0


def test_effort_stays_in_agent_module_only() -> None:
    """``EFFORT`` deliberately NOT hoisted (US-030 criterion 5).

    The runtime-mutation memory entry "--effort plumbing: 3-attempt
    pattern" is the reason: the orchestrator's ``_resolve_config``
    overwrites ``agent.EFFORT`` at session start, and a hoist would
    reintroduce the round-6 lazy-import trap pattern.
    """
    assert hasattr(agent_mod, "EFFORT")
    assert not hasattr(runtime_mod, "EFFORT")
