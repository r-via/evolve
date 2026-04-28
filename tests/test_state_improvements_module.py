"""US-044 + US-070: tests for the ``evolve.state_improvements`` extraction
and subsequent DDD migration to ``evolve.infrastructure.filesystem.improvement_parser``.

Invariants:

1. Every symbol is importable from ``evolve.infrastructure.filesystem.improvement_parser``.
2. ``is``-equality holds across the full re-export chain:
   ``improvement_parser`` → ``state_improvements`` shim → ``state`` shim.
3. The infrastructure module is a leaf — no ``from evolve.*`` top-level imports.
4. The shim has no forbidden top-level imports.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

import evolve.infrastructure.filesystem.improvement_parser as parser_mod
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
def test_symbol_importable_from_infrastructure(name: str) -> None:
    """US-070 AC 1 — every symbol importable from improvement_parser."""
    assert hasattr(parser_mod, name), (
        f"{name} missing from evolve.infrastructure.filesystem.improvement_parser"
    )


@pytest.mark.parametrize("name", _HOISTED_SYMBOLS)
def test_symbol_importable_from_state_improvements(name: str) -> None:
    """AC 1 — every hoisted symbol is exposed by the shim module."""
    assert hasattr(si_mod, name), (
        f"{name} missing from evolve.state_improvements"
    )


@pytest.mark.parametrize("name", _HOISTED_SYMBOLS)
def test_is_equality_shim_to_infrastructure(name: str) -> None:
    """US-070 AC — is-equality: state_improvements.X is improvement_parser.X."""
    assert getattr(si_mod, name) is getattr(parser_mod, name)


@pytest.mark.parametrize("name", _HOISTED_SYMBOLS)
def test_state_reexports_same_object(name: str) -> None:
    """AC — re-export identity: state.X is improvement_parser.X (full chain)."""
    assert getattr(state_mod, name) is getattr(parser_mod, name)


def test_infrastructure_leaf_module_invariant() -> None:
    """US-070 AC 4 — improvement_parser.py has zero from evolve.* top-level imports."""
    src = Path(parser_mod.__file__).read_text()
    matches = re.findall(r"^from evolve\.", src, re.MULTILINE)
    assert not matches, (
        f"improvement_parser.py has forbidden top-level imports: {matches}"
    )


def test_state_improvements_shim_no_forbidden_imports() -> None:
    """Shim must not import from agent/orchestrator/cli/state at top level."""
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
