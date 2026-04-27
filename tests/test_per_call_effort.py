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
import evolve.memory_curation as memory_curation_mod
import evolve.oneshot_agents as oneshot_agents_mod
import evolve.spec_archival as spec_archival_mod
import evolve.sync_readme as sync_readme_mod
from evolve.agent import DRAFT_EFFORT, REVIEW_EFFORT


AGENT_SRC = (Path(agent_mod.__file__)).read_text()
# US-032 extracted the draft/review SDK callsites into evolve/draft_review.py.
# US-033 extracted the dry-run / validate / diff / sync-readme SDK
# callsites into evolve/oneshot_agents.py.  US-034 then split the
# sync-readme block out into evolve/sync_readme.py.  Earlier US-031
# extracted the memory curation callsite into evolve/memory_curation.py
# and round 6 extracted SPEC archival into evolve/spec_archival.py.
# Source-grep assertions that previously scanned only agent.py now scan
# the union of the agent.py implement path + every extracted sibling so
# the per-call ``effort=DRAFT_EFFORT`` / ``effort=REVIEW_EFFORT`` and the
# session-wide ``effort=EFFORT`` kwargs are still locked even though
# they live in sibling modules.
DRAFT_REVIEW_SRC = (Path(draft_review_mod.__file__)).read_text()
ONESHOT_AGENTS_SRC = (Path(oneshot_agents_mod.__file__)).read_text()
MEMORY_CURATION_SRC = (Path(memory_curation_mod.__file__)).read_text()
SPEC_ARCHIVAL_SRC = (Path(spec_archival_mod.__file__)).read_text()
SYNC_README_SRC = (Path(sync_readme_mod.__file__)).read_text()
COMBINED_SRC = (
    AGENT_SRC + "\n"
    + DRAFT_REVIEW_SRC + "\n"
    + ONESHOT_AGENTS_SRC + "\n"
    + MEMORY_CURATION_SRC + "\n"
    + SPEC_ARCHIVAL_SRC + "\n"
    + SYNC_README_SRC
)


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
    """The implement / sync-readme / dry-run / validate / curation /
    archival paths keep ``effort=EFFORT`` so the operator's ``--effort``
    flag still tunes them.  After every extraction, the kwarg sites are
    spread across ``evolve/agent.py`` (implement path) + the extracted
    sibling modules (``oneshot_agents`` for readonly + sync-readme,
    ``memory_curation`` for Mira, ``spec_archival`` for Sid).  The
    union must contain at least 3 ``effort=EFFORT`` kwarg bindings.
    """
    # Pre-US-033 there were 3 kwarg sites in agent.py alone
    # (analyze_and_fix, readonly, sync-readme).  US-033 extracted
    # readonly + sync-readme into ``evolve/oneshot_agents.py``, leaving
    # only ``analyze_and_fix`` in agent.py — so the assertion now scans
    # the union of agent.py + every extracted sibling that hosts an
    # ``effort=EFFORT`` callsite.  Floor stays at 3 (analyze_and_fix +
    # readonly + sync-readme); curation + archival add 2 more for a
    # total of 5 in practice.
    assert COMBINED_SRC.count("effort=EFFORT") >= 3, (
        "evolve/agent.py + every extracted sibling combined must keep "
        "effort=EFFORT for implement / dry-run / validate / sync-readme "
        "/ curation / archival paths so --effort still tunes them per "
        "SPEC § 'The --effort flag'."
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
