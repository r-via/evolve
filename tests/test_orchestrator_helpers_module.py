"""US-037: tests for the ``evolve.orchestrator_helpers`` extraction.

Three invariants per the US definition of done:

1. Every extracted symbol is importable from ``evolve.orchestrator_helpers``.
2. The same identity (``is``) is reachable via ``evolve.orchestrator``
   (re-export chain — preserves ``patch("evolve.orchestrator.X")`` test
   targets and ``monkeypatch.setattr(orchestrator_mod, "X", fake)``).
3. ``evolve/orchestrator_helpers.py`` is a leaf module — no top-level
   import from ``evolve.agent`` / ``evolve.orchestrator`` / ``evolve.cli``.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

import evolve.orchestrator as orchestrator_mod
import evolve.orchestrator_helpers as helpers_mod


_HOISTED_SYMBOLS = (
    "_PROBE_PREFIX",
    "_PROBE_WARN_PREFIX",
    "_PROBE_OK_PREFIX",
    "_probe",
    "_probe_warn",
    "_probe_ok",
    "_scaffold_shared_runtime_files",
    "_is_self_evolving",
    "_enforce_convergence_backstop",
    "_parse_report_summary",
    "_run_curation_pass",
    "_should_run_spec_archival",
    "_run_spec_archival_pass",
)


@pytest.mark.parametrize("name", _HOISTED_SYMBOLS)
def test_symbol_importable_from_helpers(name: str) -> None:
    """AC 1 — every hoisted symbol is exposed by the new leaf module."""
    assert hasattr(helpers_mod, name), (
        f"{name} missing from evolve.orchestrator_helpers"
    )


@pytest.mark.parametrize("name", _HOISTED_SYMBOLS)
def test_orchestrator_reexports_same_object(name: str) -> None:
    """AC 1 — re-export identity check.

    ``patch("evolve.orchestrator.X")`` and ``monkeypatch.setattr(
    orchestrator_mod, "X", fake)`` rely on the orchestrator module
    binding the SAME object the helpers module defines.  If the
    re-export chain breaks (e.g. someone re-defines X in
    orchestrator.py), this test fails first.
    """
    assert getattr(orchestrator_mod, name) is getattr(helpers_mod, name)


def test_helpers_is_leaf_module() -> None:
    """AC 2 — leaf-module invariant.

    ``evolve/orchestrator_helpers.py`` MUST NOT import from
    ``evolve.agent``, ``evolve.orchestrator``, or ``evolve.cli`` at
    module top — those are the three-way cycle traps documented in
    ``memory.md`` round-6-of-20260427_114957 (lazy-import trap).
    Function-local imports are allowed; this regex matches only
    line-start (top-level) imports.
    """
    src = Path(helpers_mod.__file__).read_text()
    pattern = re.compile(
        r"^from evolve\.(agent|orchestrator|cli)( |$|\.)",
        re.MULTILINE,
    )
    matches = pattern.findall(src)
    assert matches == [], (
        f"orchestrator_helpers.py has forbidden top-level imports: {matches}"
    )


def test_helpers_module_has_no_extra_orchestrator_imports() -> None:
    """Defensive — the leaf invariant also forbids importing the
    orchestrator's other split modules at top level if doing so would
    introduce an import cycle.  Confirmed allowed: stdlib,
    ``evolve.diagnostics``, ``evolve.git``, ``evolve.state``,
    ``evolve.tui``.  Anything else needs review."""
    src = Path(helpers_mod.__file__).read_text()
    top_level_evolve_imports = re.findall(
        r"^from (evolve\.\w+) import",
        src,
        re.MULTILINE,
    )
    allowed = {
        "evolve.diagnostics",
        "evolve.git",
        "evolve.state",
        "evolve.tui",
    }
    forbidden = set(top_level_evolve_imports) - allowed
    assert not forbidden, (
        f"orchestrator_helpers.py imports unexpected evolve modules: {forbidden}"
    )
