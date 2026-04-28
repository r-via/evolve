"""Regression tests for the ``evolve/orchestrator_constants.py`` extract.

Round 8 of session 20260428_090113 split the spec-anchored constants
(``MAX_DEBUG_RETRIES``, ``_MEMORY_COMPACTION_MARKER``,
``_MEMORY_WIPE_THRESHOLD``, ``_BACKLOG_VIOLATION_PREFIX``,
``_BACKLOG_VIOLATION_HEADER``) out of ``evolve/orchestrator.py`` to keep
the module under the SPEC § "Hard rule: source files MUST NOT exceed 500
lines" cap that Zara HIGH-1 (round 7 review) flagged at 527 lines.

Validates the four invariants that make this a clean leaf-module split:

1. Every constant is importable from ``evolve.orchestrator_constants``.
2. ``is``-equality holds between ``evolve.orchestrator.X`` and
   ``evolve.orchestrator_constants.X`` for every re-exported name —
   ``from evolve.orchestrator import _MEMORY_COMPACTION_MARKER`` (used
   in ``tests/test_loop_coverage.py``,
   ``tests/test_loop_memory_wipe_coverage.py``,
   ``tests/test_memory_discipline.py``,
   ``tests/test_loop_forever_coverage.py``,
   ``tests/test_loop_misc_coverage.py``,
   ``tests/test_loop_zero_progress_coverage.py``,
   ``tests/test_backlog_discipline.py``) keeps working.
3. ``evolve/orchestrator_constants.py`` imports nothing — leaf-module
   invariant.
4. The new file stays under the 500-line cap and the orchestrator file
   itself is now ≤ 500 lines.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest


_LEAF_INVARIANT = re.compile(
    r"^from evolve\.(agent|orchestrator|cli)( |$|\.)",
    re.MULTILINE,
)

_RE_EXPORT_NAMES = (
    "MAX_DEBUG_RETRIES",
    "_MEMORY_COMPACTION_MARKER",
    "_MEMORY_WIPE_THRESHOLD",
    "_BACKLOG_VIOLATION_PREFIX",
    "_BACKLOG_VIOLATION_HEADER",
)


@pytest.mark.parametrize("name", _RE_EXPORT_NAMES)
def test_constants_importable_from_orchestrator_constants(name):
    import evolve.orchestrator_constants as _oc

    assert hasattr(_oc, name), (
        f"{name} not exported by evolve.orchestrator_constants"
    )


@pytest.mark.parametrize("name", _RE_EXPORT_NAMES)
def test_orchestrator_reexports_same_object(name):
    """Re-export at orchestrator.py top preserves ``is``-identity.

    Tests that import via ``from evolve.orchestrator import X`` MUST
    get the exact same object as ``evolve.orchestrator_constants.X`` —
    otherwise a future refactor that mutates one but not the other
    would silently diverge.
    """
    import evolve.orchestrator as _orch
    import evolve.orchestrator_constants as _oc

    assert getattr(_orch, name) is getattr(_oc, name), (
        f"{name} re-export broken: evolve.orchestrator and "
        "evolve.orchestrator_constants must bind the same object"
    )


def test_constant_values_match_spec():
    """The literal values are SPEC-anchored — verify they didn't drift
    during the extraction."""
    from evolve.orchestrator_constants import (
        MAX_DEBUG_RETRIES,
        _BACKLOG_VIOLATION_HEADER,
        _BACKLOG_VIOLATION_PREFIX,
        _MEMORY_COMPACTION_MARKER,
        _MEMORY_WIPE_THRESHOLD,
    )

    assert MAX_DEBUG_RETRIES == 2
    assert _MEMORY_COMPACTION_MARKER == "memory: compaction"
    assert _MEMORY_WIPE_THRESHOLD == 0.5
    assert _BACKLOG_VIOLATION_PREFIX == "BACKLOG VIOLATION"
    assert _BACKLOG_VIOLATION_HEADER.startswith("CRITICAL")
    assert "Backlog discipline violation" in _BACKLOG_VIOLATION_HEADER


def test_orchestrator_constants_is_leaf_module():
    """No ``from evolve.{agent,orchestrator,cli}`` top-level imports."""
    src_path = (
        Path(__file__).resolve().parent.parent
        / "evolve"
        / "orchestrator_constants.py"
    )
    src = src_path.read_text(encoding="utf-8")

    matches = _LEAF_INVARIANT.findall(src)
    assert matches == [], (
        f"evolve/orchestrator_constants.py violates leaf-module invariant — "
        f"top-level imports from forbidden siblings: {matches}"
    )


def test_orchestrator_constants_under_500_lines():
    """SPEC § 'Hard rule: source files MUST NOT exceed 500 lines'."""
    src_path = (
        Path(__file__).resolve().parent.parent
        / "evolve"
        / "orchestrator_constants.py"
    )
    line_count = len(src_path.read_text(encoding="utf-8").splitlines())
    assert line_count < 500, (
        f"evolve/orchestrator_constants.py at {line_count} lines exceeds 500-line cap"
    )


def test_orchestrator_under_500_lines():
    """The whole point of this extraction — the parent file was 527 lines
    and Zara HIGH-1 (round 7 review) demanded a fix.  Lock in the result.
    """
    src_path = (
        Path(__file__).resolve().parent.parent
        / "evolve"
        / "orchestrator.py"
    )
    line_count = len(src_path.read_text(encoding="utf-8").splitlines())
    assert line_count <= 500, (
        f"evolve/orchestrator.py at {line_count} lines exceeds 500-line cap "
        "(SPEC § 'Hard rule: source files MUST NOT exceed 500 lines')"
    )
