"""US-038: tests for the ``evolve.round_runner`` extraction.

Three invariants per the US definition of done:

1. Every extracted symbol is importable from ``evolve.round_runner``.
2. The same identity (``is``) is reachable via ``evolve.orchestrator``
   (re-export chain — preserves ``patch("evolve.orchestrator.X")``
   test targets and the orchestrator's own internal call site to
   ``run_single_round`` from ``_run_rounds``).
3. ``evolve/round_runner.py`` is a leaf module — no top-level import
   from ``evolve.agent`` / ``evolve.orchestrator`` / ``evolve.cli``.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

import evolve.orchestrator as orchestrator_mod
import evolve.round_runner as round_runner_mod


_HOISTED_SYMBOLS = (
    "run_single_round",
    "_run_single_round_body",
)


@pytest.mark.parametrize("name", _HOISTED_SYMBOLS)
def test_symbol_importable_from_round_runner(name: str) -> None:
    """AC 1 — every hoisted symbol is exposed by the new leaf module."""
    assert hasattr(round_runner_mod, name), (
        f"{name} missing from evolve.round_runner"
    )


@pytest.mark.parametrize("name", _HOISTED_SYMBOLS)
def test_orchestrator_reexports_same_object(name: str) -> None:
    """AC 1 — re-export identity check.

    ``patch("evolve.orchestrator.X")`` and the orchestrator's own
    internal call site to ``run_single_round`` from ``_run_rounds``
    rely on the orchestrator module binding the SAME object the
    round_runner module defines.  If the re-export chain breaks
    (e.g. someone redefines run_single_round in orchestrator.py),
    this test fails first.
    """
    assert getattr(orchestrator_mod, name) is getattr(round_runner_mod, name)


def test_round_runner_is_leaf_module() -> None:
    """AC 2 — leaf-module invariant.

    ``evolve/round_runner.py`` MUST NOT import from ``evolve.agent``,
    ``evolve.orchestrator``, or ``evolve.cli`` at module top — those
    are the three-way cycle traps documented in ``memory.md``
    round-6-of-20260427_114957 (lazy-import trap).  Function-local
    imports are allowed; this regex matches only line-start
    (top-level) imports.
    """
    src = Path(round_runner_mod.__file__).read_text()
    pattern = re.compile(
        r"^from evolve\.(agent|orchestrator|cli)( |$|\.)",
        re.MULTILINE,
    )
    matches = pattern.findall(src)
    assert matches == [], (
        f"round_runner.py has forbidden top-level imports: {matches}"
    )


def test_round_runner_has_no_extra_evolve_imports() -> None:
    """Defensive — the leaf invariant also forbids importing the
    orchestrator's other split modules at top level if doing so would
    introduce an import cycle.  Confirmed allowed: stdlib +
    ``evolve.tui``.  Anything else needs review.
    """
    src = Path(round_runner_mod.__file__).read_text()
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
        f"round_runner.py imports unexpected evolve modules: {forbidden}"
    )


def test_round_runner_under_500_line_cap() -> None:
    """SPEC § "Hard rule: source files MUST NOT exceed 500 lines".

    The round_runner extraction's whole purpose is keeping the
    orchestrator under the cap; the new module itself must also
    respect it.
    """
    line_count = len(Path(round_runner_mod.__file__).read_text().splitlines())
    assert line_count <= 500, (
        f"round_runner.py is {line_count} lines, exceeds 500-line cap"
    )
