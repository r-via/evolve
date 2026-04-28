"""US-041: tests for the ``evolve.round_success`` extraction.

Three invariants per the US definition of done:

1. ``_handle_round_success`` is importable from ``evolve.round_success``.
2. The same identity (``is``) is reachable via
   ``evolve.round_lifecycle._handle_round_success`` and
   ``evolve.orchestrator._handle_round_success`` (re-export chain
   ``evolve.orchestrator`` → ``evolve.round_lifecycle`` →
   ``evolve.round_success`` — preserves
   ``patch("evolve.orchestrator._handle_round_success")`` and
   ``patch("evolve.round_lifecycle._handle_round_success")``
   test surfaces).
3. ``evolve/round_success.py`` is a leaf module — no top-level
   import from ``evolve.agent``, ``evolve.orchestrator``,
   ``evolve.cli``, or ``evolve.round_lifecycle``.
"""

from __future__ import annotations

import re
from pathlib import Path

import evolve.orchestrator as orchestrator_mod
import evolve.round_lifecycle as round_lifecycle_mod
import evolve.round_success as round_success_mod


def test_handle_round_success_importable_from_round_success() -> None:
    """AC 1 — ``_handle_round_success`` is exposed by the new leaf module."""
    assert hasattr(round_success_mod, "_handle_round_success"), (
        "_handle_round_success missing from evolve.round_success"
    )


def test_round_lifecycle_reexports_same_object() -> None:
    """AC 2a — re-export identity check (round_lifecycle leg).

    ``patch("evolve.round_lifecycle._handle_round_success")`` and the
    ``from evolve.round_lifecycle import _handle_round_success`` call
    site in orchestrator.py rely on the round_lifecycle module binding
    the SAME object the round_success module defines.
    """
    assert (
        round_lifecycle_mod._handle_round_success
        is round_success_mod._handle_round_success
    )


def test_orchestrator_reexports_same_object() -> None:
    """AC 2b — re-export identity check (orchestrator leg).

    ``patch("evolve.orchestrator._handle_round_success")`` and the
    orchestrator's own internal call site in ``_run_rounds`` rely on
    the orchestrator module binding the SAME object the round_success
    module defines.  Three-link re-export chain
    (orchestrator → round_lifecycle → round_success) — all three legs
    must point at the same callable.
    """
    assert (
        orchestrator_mod._handle_round_success
        is round_success_mod._handle_round_success
    )


def test_round_success_is_leaf_module() -> None:
    """AC 3 — leaf-module invariant.

    ``evolve/round_success.py`` MUST NOT import from ``evolve.agent``,
    ``evolve.orchestrator``, ``evolve.cli``, or ``evolve.round_lifecycle``
    at module top — those are the cycle traps documented in
    ``memory.md`` round-6-of-20260427_114957 (lazy-import trap).
    Function-local imports are allowed (and are how the helper reaches
    ``_run_curation_pass`` etc. while preserving patch surfaces); this
    regex matches only line-start (top-level) imports.
    """
    src = Path(round_success_mod.__file__).read_text()
    pattern = re.compile(
        r"^from evolve\.(agent|orchestrator|cli|round_lifecycle)( |$|\.)",
        re.MULTILINE,
    )
    matches = pattern.findall(src)
    assert matches == [], (
        f"round_success.py has forbidden top-level imports: {matches}"
    )


def test_round_success_only_allowed_evolve_imports() -> None:
    """Defensive — the leaf invariant also forbids importing the
    orchestrator's other split modules at top level if doing so would
    introduce an import cycle.  Confirmed allowed: stdlib +
    ``evolve.tui``.  Anything else needs review.
    """
    src = Path(round_success_mod.__file__).read_text()
    top_level_evolve_imports = re.findall(
        r"^from (evolve\.\w+) import",
        src,
        re.MULTILINE,
    )
    allowed = {
        "evolve.tui",
    }
    forbidden = set(top_level_evolve_imports) - allowed
    assert not forbidden, (
        f"round_success.py imports unexpected evolve modules: {forbidden}"
    )


def test_round_lifecycle_reduced_by_at_least_220_lines() -> None:
    """AC 4 — round_lifecycle.py shrunk by ≥220 lines vs pre-extraction
    baseline (768) per US-041.  The mechanical extraction must be real
    (≥220 lines moved out), not cosmetic.
    """
    line_count = len(
        Path(round_lifecycle_mod.__file__).read_text().splitlines()
    )
    # Baseline pre-US-041: 768 lines.  Target: drop ≥220 → post ≤548.
    assert line_count <= 548, (
        f"round_lifecycle.py is {line_count} lines — US-041 target "
        f"was ≤548 (≥220-line drop from 768 baseline)"
    )
