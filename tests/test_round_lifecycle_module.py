"""US-040: tests for the ``evolve.round_lifecycle`` extraction.

Four invariants per the US definition of done:

1. Every extracted symbol is importable from ``evolve.round_lifecycle``.
2. The same identity (``is``) is reachable via ``evolve.orchestrator``
   (re-export chain — preserves ``patch("evolve.orchestrator.X")``
   test surfaces and the orchestrator's own internal call sites in
   ``_run_rounds``).
3. ``evolve/round_lifecycle.py`` is a leaf module — no top-level
   import from ``evolve.agent`` / ``evolve.orchestrator`` /
   ``evolve.cli`` (function-local imports are allowed and are how
   the helpers reach orchestrator-resident dependencies while
   preserving patch surfaces).
4. ``evolve/orchestrator.py`` line count dropped by ≥700 lines
   compared to its pre-extraction baseline (1514).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

import evolve.orchestrator as orchestrator_mod
import evolve.round_lifecycle as round_lifecycle_mod


_HOISTED_SYMBOLS = (
    "_AttemptOutcome",
    "_diagnose_attempt_outcome",
    "_handle_round_success",
)


@pytest.mark.parametrize("name", _HOISTED_SYMBOLS)
def test_symbol_importable_from_round_lifecycle(name: str) -> None:
    """AC 1 — every hoisted symbol is exposed by the new leaf module."""
    assert hasattr(round_lifecycle_mod, name), (
        f"{name} missing from evolve.round_lifecycle"
    )


@pytest.mark.parametrize("name", _HOISTED_SYMBOLS)
def test_orchestrator_reexports_same_object(name: str) -> None:
    """AC 2 — re-export identity check.

    ``patch("evolve.orchestrator.X")`` and the orchestrator's own
    internal call sites in ``_run_rounds`` rely on the orchestrator
    module binding the SAME object the round_lifecycle module defines.
    If the re-export chain breaks (e.g. someone redefines a helper
    in orchestrator.py), this test fails first.
    """
    assert (
        getattr(orchestrator_mod, name)
        is getattr(round_lifecycle_mod, name)
    )


def test_round_lifecycle_is_leaf_module() -> None:
    """AC 3 — leaf-module invariant.

    ``evolve/round_lifecycle.py`` MUST NOT import from ``evolve.agent``,
    ``evolve.orchestrator``, or ``evolve.cli`` at module top — those
    are the three-way cycle traps documented in ``memory.md``
    round-6-of-20260427_114957 (lazy-import trap).  Function-local
    imports are allowed (and are how the helpers reach
    ``_save_subprocess_diagnostic`` etc. while preserving patch
    surfaces); this regex matches only line-start (top-level) imports.
    """
    src = Path(round_lifecycle_mod.__file__).read_text()
    pattern = re.compile(
        r"^from evolve\.(agent|orchestrator|cli)( |$|\.)",
        re.MULTILINE,
    )
    matches = pattern.findall(src)
    assert matches == [], (
        f"round_lifecycle.py has forbidden top-level imports: {matches}"
    )


def test_round_lifecycle_has_no_extra_evolve_imports() -> None:
    """Defensive — the leaf invariant also forbids importing the
    orchestrator's other split modules at top level if doing so would
    introduce an import cycle.  Confirmed allowed: stdlib +
    ``evolve.tui``.  Anything else needs review.
    """
    src = Path(round_lifecycle_mod.__file__).read_text()
    top_level_evolve_imports = re.findall(
        r"^from (evolve\.\w+) import",
        src,
        re.MULTILINE,
    )
    allowed = {
        "evolve.tui",
        # US-041: ``_handle_round_success`` re-exported from sibling
        # leaf module ``evolve.round_success`` — no cycle (round_success
        # has no top-level imports from agent/orchestrator/cli/round_lifecycle,
        # locked by tests/test_round_success_module.py).
        "evolve.round_success",
    }
    forbidden = set(top_level_evolve_imports) - allowed
    assert not forbidden, (
        f"round_lifecycle.py imports unexpected evolve modules: {forbidden}"
    )


def test_orchestrator_reduced_by_at_least_700_lines() -> None:
    """AC 4 — orchestrator.py shrunk by ≥700 lines vs pre-extraction
    baseline (1514) per US-040.  The mechanical extraction must be
    real (≥700 lines moved out), not cosmetic.
    """
    line_count = len(
        Path(orchestrator_mod.__file__).read_text().splitlines()
    )
    # Baseline: 1514 lines pre-US-040.  US-040 target: drop ≥700 →
    # post-extraction ≤814 lines.
    assert line_count <= 814, (
        f"orchestrator.py is {line_count} lines — US-040 target "
        f"was ≤814 (≥700-line drop from 1514 baseline)"
    )


def test_attempt_outcome_dataclass_fields() -> None:
    """``_AttemptOutcome`` is the structured return of
    ``_diagnose_attempt_outcome``.  Field stability is a public
    contract for the orchestrator caller — guard against accidental
    rename or field-removal regressions.
    """
    from dataclasses import fields

    field_names = {f.name for f in fields(round_lifecycle_mod._AttemptOutcome)}
    expected = {
        "attempt_sig",
        "checked",
        "unchecked",
        "round_succeeded",
        "is_review_retry",
        "review_retry_circuit_tripped",
    }
    assert field_names == expected, (
        f"_AttemptOutcome fields drifted: got {field_names}, "
        f"expected {expected}"
    )
