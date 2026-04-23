"""SPEC-vs-prompt drift tests.

Asserts that `prompts/system.md` stays aligned with SPEC.md § "memory.md"
on three canonical invariants that together describe the memory-discipline
contract. Failure mode caught: SPEC edit raises the threshold (e.g. 500 →
1000) or changes the commit-message marker (e.g. `memory: compaction` →
`memory: compact`), but the runtime prompt still teaches the old contract —
agents follow stale guidance and the orchestrator's sanity gate contradicts
the prompt.

Grep-level heuristic, not NLP. See SPEC.md § "memory.md" and
§ "Orchestrator byte-size sanity gate".
"""

from __future__ import annotations

from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_SPEC = _ROOT / "SPEC.md"
_PROMPT = _ROOT / "prompts" / "system.md"


@pytest.fixture(scope="module")
def spec_text() -> str:
    return _SPEC.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def prompt_text() -> str:
    return _PROMPT.read_text(encoding="utf-8")


def test_compaction_threshold_500_in_both(spec_text: str, prompt_text: str) -> None:
    """Invariant (a): the compaction threshold number `500` appears in both
    files. If SPEC raises the threshold, prompts/system.md MUST follow."""
    assert "500" in spec_text, (
        "SPEC.md must mention the `500` line threshold for memory compaction"
    )
    assert "500" in prompt_text, (
        "prompts/system.md must mention the `500` line threshold to stay "
        "aligned with SPEC.md § 'memory.md'. If SPEC changed the threshold, "
        "update the prompt too."
    )


def test_memory_compaction_marker_verbatim_in_both(
    spec_text: str, prompt_text: str
) -> None:
    """Invariant (b): the `memory: compaction` commit-message marker appears
    verbatim in both files. The orchestrator's sanity gate greps for this
    exact string; if SPEC renames it the prompt MUST be updated."""
    marker = "memory: compaction"
    assert marker in spec_text, (
        f"SPEC.md must contain the `{marker}` commit-message marker verbatim"
    )
    assert marker in prompt_text, (
        f"prompts/system.md must contain the `{marker}` commit-message marker "
        "verbatim to match SPEC.md § 'Orchestrator byte-size sanity gate'. "
        "If the marker was renamed in SPEC, update the prompt too."
    )


def test_archival_directive_and_20_round_cutoff_in_both(
    spec_text: str, prompt_text: str
) -> None:
    """Invariant (c): the archival-not-deletion directive AND the 20-round
    cutoff both appear in each file. Catches SPEC edits that change archival
    semantics or the age cutoff without updating the prompt."""
    # Archive directive: both files must reference a `## Archive` section
    # (the collapsed, still-on-disk section) AND the word `archive` in
    # the sense of "move, don't delete".
    assert "## Archive" in spec_text, (
        "SPEC.md must document the `## Archive` section for aged entries"
    )
    assert "## Archive" in prompt_text, (
        "prompts/system.md must reference the `## Archive` section to match "
        "SPEC.md § 'memory.md'"
    )

    # The 20-round cutoff is the age at which entries move into Archive.
    assert "20 rounds" in spec_text, (
        "SPEC.md must document the `20 rounds` archival cutoff"
    )
    assert "20 rounds" in prompt_text, (
        "prompts/system.md must document the `20 rounds` archival cutoff to "
        "stay aligned with SPEC.md § 'memory.md'. If SPEC changed the cutoff, "
        "update the prompt too."
    )

    # Archival-not-deletion: both files must explicitly say archive rather
    # than delete old entries. The SPEC phrases this as "archives entries
    # older than 20 rounds" and the prompt as "archive (do not delete)".
    # We assert each file mentions both "archive" and a "not delete"
    # directive in proximity (whole-file, case-insensitive substring).
    spec_lower = spec_text.lower()
    prompt_lower = prompt_text.lower()
    assert "archive" in spec_lower and (
        "not delete" in spec_lower or "still on disk" in spec_lower
    ), (
        "SPEC.md must explicitly direct archival rather than deletion of aged "
        "memory entries"
    )
    assert "archive" in prompt_lower and (
        "do not delete" in prompt_lower or "not delete" in prompt_lower
    ), (
        "prompts/system.md must explicitly direct archival rather than "
        "deletion of aged memory entries to match SPEC.md § 'memory.md'"
    )
