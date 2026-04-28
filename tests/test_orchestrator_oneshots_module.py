"""Lock-in tests for the canonical ``evolve.orchestrator_oneshots`` module
path (US-036, mirrors ``tests/test_oneshot_agents_module.py`` /
``tests/test_sync_readme_module.py`` / ``tests/test_prompt_builder_module.py``).

The structural extraction in US-036 moves the four one-shot orchestrator
entry points (``run_dry_run``, ``run_validate``, ``run_diff``,
``run_sync_readme``) from ``evolve/orchestrator.py`` into the dedicated
``evolve/orchestrator_oneshots.py`` leaf module so ``orchestrator.py``
drops toward the SPEC § "Hard rule: source files MUST NOT exceed 500
lines" cap.  The pre-existing one-shot test files
(``tests/test_dry_run.py``, ``tests/test_validate.py``,
``tests/test_diff.py``, ``tests/test_sync_readme.py``) and ``evolve/cli.py``
only import the re-exports from ``evolve.orchestrator``, so deleting the
new module would NOT make those tests fail — defeating the purpose of
the split.

This file:

(a) imports each public name **directly** from
    ``evolve.orchestrator_oneshots``,
(b) asserts the bound objects are ``is``-identical to the re-exports
    surfaced via ``evolve.orchestrator``, and
(c) re-asserts the leaf-module invariant (no top-level
    ``from evolve.{agent,orchestrator,cli}`` imports).
"""

from __future__ import annotations

import re
from pathlib import Path


_CANONICAL_NAMES = (
    "run_dry_run",
    "run_validate",
    "run_diff",
    "run_sync_readme",
)


def test_canonical_imports_resolve_from_evolve_orchestrator_oneshots():
    """Every public name documented for the one-shot entry points must be
    importable from the new canonical path."""
    from evolve.orchestrator_oneshots import (  # noqa: F401
        run_dry_run,
        run_validate,
        run_diff,
        run_sync_readme,
    )


def test_orchestrator_reexports_are_is_identical():
    """``patch("evolve.orchestrator.run_X", ...)`` and the existing
    ``from evolve.orchestrator import run_X`` callsites in
    ``evolve/cli.py`` continue to intercept iff the re-export chain
    binds the SAME object the canonical module defines.  Identity, not
    equality — Python's ``patch`` semantics target the binding."""
    import evolve.orchestrator as orch
    import evolve.orchestrator_oneshots as oo

    for name in _CANONICAL_NAMES:
        canonical = getattr(oo, name)
        reexport = getattr(orch, name)
        assert reexport is canonical, (
            f"evolve.orchestrator.{name} is NOT the same object as "
            f"evolve.orchestrator_oneshots.{name} — the re-export chain "
            "broke; ``patch('evolve.orchestrator.{name}', ...)`` will not "
            "intercept calls inside orchestrator-side code."
        )


def test_leaf_module_invariant_no_evolve_top_level_imports():
    """``evolve/orchestrator_oneshots.py`` must NOT have any top-level
    ``from evolve.{agent,orchestrator,cli}( |$|.)`` imports — otherwise the
    extraction has reintroduced the cycle / lazy-import-trap class of bugs
    documented in memory.md round-7 of 20260427_114957 and round-1 of
    20260427_114957 ('Re-export ≠ patch surface when call site uses
    indirection').  Lazy imports inside function bodies are
    permitted and expected (they preserve the
    ``patch('evolve.orchestrator.X')`` test surface)."""
    src_path = Path(__file__).resolve().parent.parent / "evolve" / "orchestrator_oneshots.py"
    src = src_path.read_text()
    forbidden = re.findall(
        r"^from evolve\.(agent|orchestrator|cli)( |$|\.)",
        src,
        re.MULTILINE,
    )
    assert forbidden == [], (
        f"Leaf-module invariant broken: top-level forbidden imports = "
        f"{forbidden}.  Move the offending import into a function body "
        "to preserve the ``patch('evolve.orchestrator.X')`` test surface "
        "(memory.md round-7 of 20260427_114957)."
    )
