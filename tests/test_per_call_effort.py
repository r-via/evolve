"""Per-call effort overrides for draft and review (US-029).

SPEC § "Multi-call round architecture" pins draft and review to
``effort=low`` regardless of the session-wide ``--effort`` value.  These
tests lock that contract:

* The two constants ``DRAFT_EFFORT`` / ``REVIEW_EFFORT`` exist in
  ``evolve.agent`` and equal ``"low"``.
* The draft and review SDK call sites use the new constants by name
  (verified via source-grep — same approach as the existing
  ``effort=EFFORT`` plumbing test in ``test_evolve.py``).
* Mutating the session-wide ``EFFORT`` global at runtime does **not**
  change the effort passed to the draft / review ``ClaudeAgentOptions``
  blocks — they are pinned to the dedicated constants.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import evolve.agent as agent_mod
import evolve.draft_review as draft_review_mod
from evolve.agent import DRAFT_EFFORT, REVIEW_EFFORT


AGENT_SRC = (Path(agent_mod.__file__)).read_text()
# US-032 extracted the draft/review SDK callsites into evolve/draft_review.py.
# Source-grep assertions that previously scanned only agent.py now scan both
# files so the per-call ``effort=DRAFT_EFFORT`` / ``effort=REVIEW_EFFORT``
# kwargs are still locked even though they live in a sibling module.
DRAFT_REVIEW_SRC = (Path(draft_review_mod.__file__)).read_text()
COMBINED_SRC = AGENT_SRC + "\n" + DRAFT_REVIEW_SRC


@pytest.mark.parametrize(
    "constant_name,expected",
    [
        ("DRAFT_EFFORT", "low"),
        ("REVIEW_EFFORT", "low"),
    ],
)
def test_per_call_effort_constants_pin_to_low(constant_name, expected):
    """The two constants exist on ``evolve.agent`` and equal ``"low"``."""
    assert hasattr(agent_mod, constant_name), (
        f"evolve.agent must define {constant_name} per SPEC § "
        "'Multi-call round architecture'"
    )
    assert getattr(agent_mod, constant_name) == expected


@pytest.mark.parametrize(
    "kwarg_marker",
    [
        "effort=DRAFT_EFFORT",
        "effort=REVIEW_EFFORT",
    ],
)
def test_draft_and_review_call_sites_use_dedicated_constants(kwarg_marker):
    """``ClaudeAgentOptions`` for draft and review must use the dedicated
    per-call constants, NOT the session-wide ``EFFORT`` global.

    Source-grep approach (mirrors the existing
    ``test_evolve.py::test_effort_flag_is_threaded_through_agent`` style
    that asserts ``agent_src.count("effort=EFFORT") >= 3``).  Per US-032
    the call sites live in ``evolve/draft_review.py`` — the assertion is
    broadened to scan both files so the contract survives the extraction.
    """
    assert kwarg_marker in COMBINED_SRC, (
        f"evolve/agent.py + evolve/draft_review.py combined must pass "
        f"{kwarg_marker} to the corresponding ClaudeAgentOptions(...) block "
        "— see SPEC § 'Multi-call round architecture' table that pins "
        "draft/review to effort=low."
    )


def test_implement_path_still_uses_EFFORT_global():
    """The implement / sync-readme / dry-run / validate / curation paths
    keep ``effort=EFFORT`` so the operator's ``--effort`` flag still
    tunes them.  After the per-call override, at least 3 kwarg sites
    in agent.py still bind ``effort=EFFORT`` (analyze_and_fix, readonly
    agent for dry-run/validate/diff, sync-readme).
    """
    # Count is the surviving kwarg sites in agent.py.  Pre-US-032 there
    # were also docstring/comment references in the multi-call section
    # header which inflated the count by 1 — that section was removed
    # when the draft/review SDK callsites were extracted to
    # ``evolve/draft_review.py`` (US-032), so the floor drops to 3.
    # See the existing ``>= 3`` assertion in test_evolve.py for the
    # canonical lower bound.
    assert AGENT_SRC.count("effort=EFFORT") >= 3, (
        "agent.py must keep effort=EFFORT for implement / dry-run / "
        "validate / sync-readme paths so --effort still tunes them "
        "per SPEC § 'The --effort flag'."
    )


def test_runtime_EFFORT_mutation_does_not_leak_into_draft_or_review(monkeypatch):
    """Setting ``agent.EFFORT = "max"`` at runtime (the loop entry
    points overwrite ``EFFORT`` at session start) MUST NOT change the
    effort passed to the draft / review ``ClaudeAgentOptions`` because
    those blocks resolve ``DRAFT_EFFORT`` / ``REVIEW_EFFORT`` instead.

    We verify this by capturing the kwargs handed to ``ClaudeAgentOptions``
    on each invocation: even with ``EFFORT="max"``, draft sees
    ``effort="low"`` and review sees ``effort="low"``.
    """
    # Bump the session-wide global to a non-default value to prove
    # draft/review do NOT pick it up.
    monkeypatch.setattr(agent_mod, "EFFORT", "max")

    # Build a fake claude_agent_sdk module that records the
    # ClaudeAgentOptions kwargs and then short-circuits the async
    # generator without making any network call.
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
        # Patch _patch_sdk_parser so it doesn't try to touch the fake.
        with patch.object(agent_mod, "_patch_sdk_parser", lambda: None):
            import asyncio

            # Draft path
            captured.clear()
            asyncio.run(
                agent_mod._run_draft_claude_agent(
                    prompt="ignored",
                    project_dir=Path("/tmp"),
                    run_dir=Path("/tmp"),
                )
            )
            assert captured.get("effort") == "low", (
                "draft must pin effort=low even when EFFORT is mutated"
            )

            # Review path
            captured.clear()
            asyncio.run(
                agent_mod._run_review_claude_agent(
                    prompt="ignored",
                    project_dir=Path("/tmp"),
                    run_dir=Path("/tmp"),
                    round_num=1,
                )
            )
            assert captured.get("effort") == "low", (
                "review must pin effort=low even when EFFORT is mutated"
            )
