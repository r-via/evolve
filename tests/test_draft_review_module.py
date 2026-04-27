"""Lock-in test for the canonical ``evolve.draft_review`` module path
(US-032 split step 3).

US-032 extracted Winston/John (draft) and Zara (review) from
``evolve/agent.py`` into the dedicated ``evolve/draft_review.py`` module
so ``agent.py`` moves toward the SPEC § "Hard rule: source files MUST
NOT exceed 500 lines" cap.  The pre-existing draft/review tests
(``tests/test_multi_call_architecture.py`` etc.) only import the
re-exports from ``evolve.agent``, so deleting the new module would NOT
make those tests fail — defeating the purpose of the split.

This file imports each public name **directly** from
``evolve.draft_review`` and asserts that the bound objects are
``is``-identical to the re-exports surfaced via ``evolve.agent``.  If
either the canonical module or the re-export drifts, the test fails.
Mirrors the US-027 / US-030 / US-031 / spec_archival lock-in pattern.
"""

from __future__ import annotations

import re
from pathlib import Path


_DRAFT_REVIEW_NAMES = (
    "_build_draft_prompt",
    "_run_draft_claude_agent",
    "run_draft_agent",
    "_build_review_prompt",
    "_run_review_claude_agent",
    "run_review_agent",
)


def test_canonical_imports_resolve_from_evolve_draft_review():
    """Every public name documented for draft / review must be importable
    from the new canonical path."""
    from evolve.draft_review import (  # noqa: F401
        _build_draft_prompt,
        _run_draft_claude_agent,
        run_draft_agent,
        _build_review_prompt,
        _run_review_claude_agent,
        run_review_agent,
    )


def test_re_exports_are_is_identical_to_canonical_module():
    """``evolve.agent`` re-exports must point at the SAME objects bound
    in ``evolve.draft_review`` — not duplicates, not shims."""
    import evolve.agent as agent_mod
    import evolve.draft_review as draft_review_mod

    for name in _DRAFT_REVIEW_NAMES:
        canonical = getattr(draft_review_mod, name)
        re_exported = getattr(agent_mod, name)
        assert canonical is re_exported, (
            f"evolve.agent.{name} must be the SAME object as "
            f"evolve.draft_review.{name} (re-export, not duplicate)"
        )


def test_draft_review_module_obeys_leaf_invariant():
    """``evolve/draft_review.py`` must NOT have top-level imports from
    ``evolve.agent`` / ``evolve.orchestrator`` / ``evolve.cli``.

    The leaf-module invariant is what prevents the round-6
    lazy-import trap (memory.md "agent_runtime hoist: lazy get_tui must
    resolve via evolve.agent"): runtime constants come from
    ``evolve.agent_runtime``, and the agent.py-resident dependencies
    (``_load_project_context``, ``_patch_sdk_parser``,
    ``_summarise_tool_input``, ``_run_agent_with_retries``) are imported
    lazily inside function bodies — indented imports do NOT match the
    line-anchored regex below (memory.md round-7 entry).
    """
    import evolve.draft_review as draft_review_mod

    src = Path(draft_review_mod.__file__).read_text()
    pattern = re.compile(
        r"^from evolve\.(agent|orchestrator|cli)( |$|\.)", re.MULTILINE
    )
    matches = pattern.findall(src)
    assert matches == [], (
        f"evolve/draft_review.py must not have top-level imports from "
        f"evolve.agent / evolve.orchestrator / evolve.cli — leaf-module "
        f"invariant broken by: {matches}"
    )


def test_runtime_EFFORT_mutation_does_not_leak_into_extracted_draft_or_review(monkeypatch):
    """Mutating ``agent.EFFORT`` at runtime MUST NOT change the effort
    passed to the draft / review ``ClaudeAgentOptions`` blocks because
    the extracted callsites resolve ``DRAFT_EFFORT`` / ``REVIEW_EFFORT``
    from ``evolve.agent_runtime``, NOT the session-wide global.

    Locks the US-029 contract through the US-032 extraction.  Mirrors
    the equivalent test in ``tests/test_per_call_effort.py``, but
    invokes the SDK runners directly via ``evolve.draft_review`` so the
    canonical path is exercised (not just the re-export).
    """
    import asyncio
    from unittest.mock import MagicMock, patch

    import evolve.agent as agent_mod
    import evolve.draft_review as draft_review_mod

    monkeypatch.setattr(agent_mod, "EFFORT", "max")

    captured: dict[str, dict] = {}

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
            captured.clear()
            asyncio.run(
                draft_review_mod._run_draft_claude_agent(
                    prompt="ignored",
                    project_dir=Path("/tmp"),
                    run_dir=Path("/tmp"),
                )
            )
            assert captured.get("effort") == "low", (
                "draft must pin effort=low even when EFFORT is mutated"
            )

            captured.clear()
            asyncio.run(
                draft_review_mod._run_review_claude_agent(
                    prompt="ignored",
                    project_dir=Path("/tmp"),
                    run_dir=Path("/tmp"),
                    round_num=1,
                )
            )
            assert captured.get("effort") == "low", (
                "review must pin effort=low even when EFFORT is mutated"
            )
