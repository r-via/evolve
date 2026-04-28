"""US-042: tests for the ``evolve.orchestrator_startup`` extraction.

Three invariants per the US definition of done:

1. ``evolve_loop`` is importable from ``evolve.orchestrator_startup``.
2. The same identity (``is``) is reachable via ``evolve.orchestrator``
   (re-export chain — preserves ``patch("evolve.orchestrator.evolve_loop")``
   test targets and the ``from evolve.orchestrator import evolve_loop``
   call site in ``evolve/cli.py``).
3. ``evolve/orchestrator_startup.py`` is a leaf module — no top-level
   import from ``evolve.agent`` / ``evolve.orchestrator`` / ``evolve.cli``.
"""

from __future__ import annotations

import re
from pathlib import Path

import evolve.orchestrator as orchestrator_mod
import evolve.orchestrator_startup as orchestrator_startup_mod


def test_evolve_loop_importable_from_orchestrator_startup() -> None:
    """AC 1 — evolve_loop is exposed by the new leaf module."""
    assert hasattr(orchestrator_startup_mod, "evolve_loop"), (
        "evolve_loop missing from evolve.orchestrator_startup"
    )


def test_orchestrator_reexports_same_evolve_loop() -> None:
    """AC 3 — re-export identity check.

    ``patch("evolve.orchestrator.evolve_loop")`` and the CLI's
    ``from evolve.orchestrator import evolve_loop`` rely on the
    orchestrator module binding the SAME object the
    orchestrator_startup module defines.  If the re-export chain
    breaks (e.g. someone redefines evolve_loop in orchestrator.py),
    this test fails first.
    """
    assert orchestrator_mod.evolve_loop is orchestrator_startup_mod.evolve_loop


def test_orchestrator_startup_is_leaf_module() -> None:
    """AC 2 — leaf-module invariant.

    ``evolve/orchestrator_startup.py`` MUST NOT import from
    ``evolve.agent``, ``evolve.orchestrator``, or ``evolve.cli`` at
    module top — those are the three-way cycle traps documented in
    ``memory.md`` round-6-of-20260427_114957 (lazy-import trap).
    Function-local imports are allowed; this regex matches only
    line-start (top-level) imports.
    """
    src = Path(orchestrator_startup_mod.__file__).read_text()
    pattern = re.compile(
        r"^from evolve\.(agent|orchestrator|cli)( |$|\.)",
        re.MULTILINE,
    )
    matches = pattern.findall(src)
    assert matches == [], (
        f"orchestrator_startup.py has forbidden top-level imports: {matches}"
    )


def test_orchestrator_startup_has_no_extra_evolve_imports() -> None:
    """Defensive — the leaf invariant also forbids importing the
    orchestrator's other split modules at top level if doing so would
    introduce an import cycle.  Confirmed allowed: stdlib only
    (no ``evolve.*`` top-level imports needed because every dep is
    lazy-imported via ``evolve.orchestrator``).  Anything else needs
    review.
    """
    src = Path(orchestrator_startup_mod.__file__).read_text()
    top_level_evolve_imports = re.findall(
        r"^from (evolve\.\w+) import",
        src,
        re.MULTILINE,
    )
    # No evolve.* top-level imports — all deps lazy via evolve.orchestrator.
    assert top_level_evolve_imports == [], (
        f"orchestrator_startup.py imports unexpected evolve modules: "
        f"{top_level_evolve_imports}"
    )


def test_orchestrator_startup_under_500_line_cap() -> None:
    """SPEC § "Hard rule: source files MUST NOT exceed 500 lines".

    The orchestrator_startup extraction's whole purpose is keeping the
    orchestrator under the cap; the new module itself must also
    respect it.
    """
    line_count = len(
        Path(orchestrator_startup_mod.__file__).read_text().splitlines()
    )
    assert line_count <= 500, (
        f"orchestrator_startup.py is {line_count} lines, exceeds 500-line cap"
    )
