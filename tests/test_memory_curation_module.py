"""Lock-in tests for the canonical ``evolve.memory_curation`` module path
(US-031, mirrors ``tests/test_spec_archival_module.py``'s lock-in pattern).

The structural extraction in US-031 moves Mira (the memory curation
agent) from ``evolve/agent.py`` into the dedicated
``evolve/memory_curation.py`` leaf module so ``agent.py`` drops below
the SPEC § "Hard rule: source files MUST NOT exceed 500 lines" cap.
The pre-existing ``tests/test_memory_curation.py`` only imports the
re-exports from ``evolve.agent``, so deleting the new module would
NOT make those tests fail — defeating the purpose of the split.

This file:

(a) imports each public name **directly** from ``evolve.memory_curation``,
(b) asserts the bound objects are ``is``-identical to the re-exports
    surfaced via ``evolve.agent``,
(c) re-asserts the leaf-module invariant (no top-level
    ``from evolve.{agent,orchestrator,cli}`` imports), and
(d) proves runtime mutation of ``evolve.infrastructure.claude_sdk.runtime.EFFORT`` propagates into
    the next ``_run_memory_curation_claude_agent`` call's
    ``ClaudeAgentOptions(effort=...)`` kwarg — same pattern as
    ``tests/test_per_call_effort.py``'s
    ``test_runtime_EFFORT_mutation_does_not_leak_into_draft_or_review``.
"""

from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import MagicMock, patch


_CANONICAL_NAMES = (
    "CURATION_LINE_THRESHOLD",
    "CURATION_ROUND_INTERVAL",
    "_CURATION_MAX_SHRINK",
    "_should_run_curation",
    "build_memory_curation_prompt",
    "_run_memory_curation_claude_agent",
    "run_memory_curation",
)


def test_canonical_imports_resolve_from_evolve_memory_curation():
    """Every public name documented for Mira must be importable from the
    new canonical path."""
    from evolve.memory_curation import (  # noqa: F401
        CURATION_LINE_THRESHOLD,
        CURATION_ROUND_INTERVAL,
        _CURATION_MAX_SHRINK,
        _should_run_curation,
        build_memory_curation_prompt,
        _run_memory_curation_claude_agent,
        run_memory_curation,
    )


def test_re_exports_are_is_identical_to_canonical_module():
    """``evolve.agent`` re-exports must point at the SAME objects bound
    in ``evolve.memory_curation`` — not duplicates, not shims."""
    import evolve.agent as agent_mod
    import evolve.memory_curation as curation_mod

    for name in _CANONICAL_NAMES:
        canonical = getattr(curation_mod, name)
        re_exported = getattr(agent_mod, name)
        assert canonical is re_exported, (
            f"evolve.agent.{name} must be the SAME object as "
            f"evolve.memory_curation.{name} (re-export, not duplicate)"
        )


def test_threshold_constants_have_expected_values():
    """Spec-pinned values per SPEC § 'Dedicated memory curation — Mira'."""
    from evolve.memory_curation import (
        CURATION_LINE_THRESHOLD,
        CURATION_ROUND_INTERVAL,
        _CURATION_MAX_SHRINK,
    )

    assert CURATION_LINE_THRESHOLD == 300
    assert CURATION_ROUND_INTERVAL == 10
    assert _CURATION_MAX_SHRINK == 0.80


def test_memory_curation_module_is_a_leaf():
    """The canonical module must NOT import from ``evolve.agent``,
    ``evolve.orchestrator``, or ``evolve.cli`` at module top level.

    Function-local (indented) imports are intentionally allowed —
    ``_run_memory_curation_claude_agent`` and ``run_memory_curation``
    look up ``MODEL`` / ``EFFORT`` / ``MAX_TURNS`` /
    ``_patch_sdk_parser`` / ``_summarise_tool_input`` /
    ``_run_agent_with_retries`` lazily so the
    ``EFFORT`` runtime mutation by ``_resolve_config`` keeps
    propagating, and so module-load order remains acyclic
    (memory.md round-7 entry: indented imports don't trip the
    leaf-invariant regex ``^from evolve\\.``).
    """
    import evolve.memory_curation as curation_mod

    src = Path(curation_mod.__file__).read_text()
    leaf_violations = re.findall(
        r"^from evolve\.(agent|orchestrator|cli)( |$|\.)",
        src,
        re.MULTILINE,
    )
    assert leaf_violations == [], (
        "evolve/memory_curation.py must remain a leaf module — no "
        "top-level imports from evolve.{agent,orchestrator,cli}. "
        f"Found: {leaf_violations}"
    )


def test_runtime_EFFORT_mutation_propagates_into_curation_options(monkeypatch):
    """Setting ``evolve.infrastructure.claude_sdk.runtime.EFFORT = "max"`` at runtime (the loop
    entry points overwrite ``EFFORT`` at session start) MUST propagate
    into the next ``_run_memory_curation_claude_agent`` call's
    ``ClaudeAgentOptions(effort=...)`` kwarg.

    This is the inverse of ``test_runtime_EFFORT_mutation_does_not_leak_into_draft_or_review``
    in ``tests/test_per_call_effort.py``: draft/review pin to
    ``DRAFT_EFFORT`` / ``REVIEW_EFFORT`` (low), but curation continues
    to use the session-wide ``EFFORT`` so ``--effort`` still tunes it
    per SPEC § "The --effort flag".  The lazy import inside
    ``_run_memory_curation_claude_agent`` is what makes this work —
    if we accidentally bound ``EFFORT`` at module top, this test
    would fail.
    """
    import evolve.agent as agent_mod
    import evolve.memory_curation as curation_mod

    monkeypatch.setattr(agent_mod, "EFFORT", "max")

    captured: dict[str, object] = {}

    class FakeOptions:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    async def fake_query(prompt, options):  # pragma: no cover - generator shell
        if False:  # pragma: no cover
            yield None

    fake_sdk = MagicMock()
    fake_sdk.ClaudeAgentOptions = FakeOptions
    fake_sdk.query = fake_query
    fake_sdk.AssistantMessage = type("AssistantMessage", (), {})
    fake_sdk.ResultMessage = type("ResultMessage", (), {})

    with patch.dict("sys.modules", {"claude_agent_sdk": fake_sdk}):
        with patch.object(agent_mod, "_patch_sdk_parser", lambda: None):
            import asyncio

            asyncio.run(
                curation_mod._run_memory_curation_claude_agent(
                    prompt="ignored",
                    project_dir=Path("/tmp"),
                    run_dir=Path("/tmp"),
                )
            )

    assert captured.get("effort") == "max", (
        "memory curation must pick up runtime mutation of "
        "evolve.infrastructure.claude_sdk.runtime.EFFORT — lazy import inside "
        "_run_memory_curation_claude_agent is what guarantees this."
    )
