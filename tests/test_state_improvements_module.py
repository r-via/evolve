"""US-044: tests for the ``evolve.state_improvements`` extraction.

Three invariants per the US definition of done:

1. Every extracted symbol is importable from ``evolve.state_improvements``.
2. The same identity (``is``) is reachable via ``evolve.state``
   (re-export chain — preserves ``patch("evolve.state.X")`` test
   targets and ``from evolve.state import _count_unchecked`` callers).
3. ``evolve/state_improvements.py`` is a leaf module — no top-level
   import from ``evolve.agent`` / ``evolve.orchestrator`` / ``evolve.cli``
   / ``evolve.state``.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

import evolve.state as state_mod
import evolve.state_improvements as si_mod


_HOISTED_SYMBOLS = (
    "_count_checked",
    "_count_unchecked",
    "_is_needs_package",
    "_count_blocked",
    "_get_current_improvement",
    "_extract_unchecked_set",
    "_extract_unchecked_lines",
    "_detect_backlog_violation",
    "_parse_check_output",
)


@pytest.mark.parametrize("name", _HOISTED_SYMBOLS)
def test_symbol_importable_from_state_improvements(name: str) -> None:
    """AC 1 — every hoisted symbol is exposed by the new leaf module."""
    assert hasattr(si_mod, name), (
        f"{name} missing from evolve.state_improvements"
    )


@pytest.mark.parametrize("name", _HOISTED_SYMBOLS)
def test_state_reexports_same_object(name: str) -> None:
    """AC 1 — re-export identity check.

    ``patch("evolve.state.X")`` and direct callers via
    ``from evolve.state import _count_unchecked`` rely on the
    state module binding the SAME object the leaf module defines.
    If the re-export chain breaks, this test fails first.
    """
    assert getattr(state_mod, name) is getattr(si_mod, name)


def test_state_improvements_is_leaf_module() -> None:
    """AC 2 — leaf-module invariant.

    ``evolve/state_improvements.py`` MUST NOT import from
    ``evolve.agent``, ``evolve.orchestrator``, ``evolve.cli``, or
    ``evolve.state`` at module top — those would create cycles
    against this module's role as a leaf used by ``evolve.state``.
    """
    src = Path(si_mod.__file__).read_text()
    pattern = re.compile(
        r"^from evolve\.(agent|orchestrator|cli|state)( |$|\.)",
        re.MULTILINE,
    )
    matches = pattern.findall(src)
    assert matches == [], (
        f"state_improvements.py has forbidden top-level imports: {matches}"
    )


def test_state_under_500_line_cap() -> None:
    """SPEC § 'Hard rule' — state.py must drop under the 500-line cap
    after the US-044 extraction."""
    line_count = len(Path(state_mod.__file__).read_text().splitlines())
    assert line_count <= 500, (
        f"state.py at {line_count} lines exceeds 500-line SPEC cap"
    )


def test_state_improvements_under_500_line_cap() -> None:
    """SPEC § 'Hard rule' — the new leaf module must also be under cap."""
    line_count = len(Path(si_mod.__file__).read_text().splitlines())
    assert line_count <= 500, (
        f"state_improvements.py at {line_count} lines exceeds 500-line SPEC cap"
    )
