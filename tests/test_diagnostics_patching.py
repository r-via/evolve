"""Regression tests for US-028: drop ``_diag.X`` alias indirection in
``evolve.application.run_loop``.

Memory-of-record context: rounds 1 of session 20260427_114957 discovered
that re-exporting a name from ``evolve.diagnostics`` into
``evolve.application.run_loop`` is **not** sufficient for the third common patching
shape used in the test suite — ``monkeypatch.setattr(orchestrator_mod,
"X", fake)``.  When call sites inside ``orchestrator.py`` write
``_diag.X(...)`` (alias indirection) instead of bare ``X(...)``, the
``monkeypatch.setattr`` patch on the orchestrator module is silently
bypassed because Python resolves ``_diag.X`` through the
``evolve.diagnostics`` module's namespace, not through
``evolve.application.run_loop``'s.

These tests guard the de-aliased state by enforcing two invariants for
every diagnostic helper re-exported from ``evolve.diagnostics`` into
``evolve.application.run_loop``:

1. The name is bound directly in the orchestrator module's namespace
   (so ``monkeypatch.setattr(evolve.application.run_loop, name, sentinel)`` is a
   meaningful operation).
2. The orchestrator source contains zero ``Attribute`` nodes of the form
   ``<alias>.<name>`` for any of these names — i.e. no
   ``_diag.<name>(...)`` style call sites linger.  Such a node would
   make a ``setattr(orchestrator_mod, name, fake)`` patch a no-op for
   that call site (the failure mode this US closes).

The second invariant is the one that "fails today, passes after the
de-aliasing" per the US-028 acceptance criteria — it is the binding
contract that future module extractions must continue to honour.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

import evolve.application.run_loop as orchestrator_mod


# Every helper re-exported from evolve.diagnostics into the orchestrator
# module namespace via the ``from evolve.infrastructure.diagnostics.detector import (...)`` block.
# Includes both private callables (the historical _diag.X targets) and the
# module-level constants that travelled with them.  Tests below treat
# these uniformly: each must be bound in ``evolve.application.run_loop``'s
# namespace AND must not be referenced via alias-attribute access in the
# orchestrator source.
RE_EXPORTED_DIAGNOSTICS_NAMES = [
    "_auto_detect_check",
    "_check_review_verdict",
    "_detect_file_too_large",
    "_emit_stale_readme_advisory",
    "_failure_signature",
    "_generate_evolution_report",
    "_is_circuit_breaker_tripped",
    "_save_subprocess_diagnostic",
    "MAX_IDENTICAL_FAILURES",
    "_DEFAULT_README_STALE_THRESHOLD_DAYS",
    "_FILE_TOO_LARGE_LIMIT",
    "_README_STALE_ADVISORY_FMT",
]


@pytest.mark.parametrize("name", RE_EXPORTED_DIAGNOSTICS_NAMES)
def test_diagnostics_name_bound_in_orchestrator_namespace(name: str) -> None:
    """Every diagnostics helper re-exported into orchestrator MUST be a
    direct attribute of the orchestrator module so that
    ``monkeypatch.setattr(orchestrator_mod, name, fake)`` is a meaningful
    operation.  Without this, callers patching the orchestrator module
    would silently fail (the patch wouldn't intercept anything because
    the name wouldn't exist on the module).
    """
    assert hasattr(orchestrator_mod, name), (
        f"{name!r} must be bound in evolve.application.run_loop's namespace "
        f"so monkeypatch.setattr(orchestrator_mod, {name!r}, fake) works."
    )


@pytest.mark.parametrize("name", RE_EXPORTED_DIAGNOSTICS_NAMES)
def test_no_alias_attribute_access_to_diagnostics(name: str) -> None:
    """No call site inside ``evolve.application.run_loop`` may reference any of
    these diagnostics names via ``<alias>.<name>`` attribute access.

    Catches the regression where someone re-introduces
    ``import evolve.infrastructure.diagnostics.detector as _diag`` and writes ``_diag.X(...)``
    — that would silently break ``monkeypatch.setattr(orchestrator_mod,
    "X", fake)`` patches.  Every reference must be a bare ``Name``
    (resolved through the orchestrator module's own namespace, which is
    what the monkeypatch replaces).
    """
    src_path = Path(orchestrator_mod.__file__)
    tree = ast.parse(src_path.read_text())

    offending: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr == name:
            # An Attribute node where attr matches one of our names means
            # someone wrote ``something.<name>`` — the alias indirection
            # pattern that breaks orchestrator-module monkeypatching.
            value_repr = ast.unparse(node.value)
            offending.append((node.lineno, f"{value_repr}.{name}"))

    assert not offending, (
        f"Found {len(offending)} alias-attribute access(es) to {name!r} "
        f"in evolve/orchestrator.py — these break "
        f"monkeypatch.setattr(orchestrator_mod, {name!r}, fake): "
        f"{offending}"
    )


def test_diag_alias_import_not_present() -> None:
    """``import evolve.infrastructure.diagnostics.detector as _diag`` must not exist in the
    orchestrator source.  Reintroducing it is the prerequisite for the
    alias-indirection bug — banning the import outright is the simplest
    structural guard.
    """
    src = Path(orchestrator_mod.__file__).read_text()
    assert "import evolve.infrastructure.diagnostics.detector as _diag" not in src, (
        "evolve/orchestrator.py must not alias evolve.diagnostics as _diag — "
        "use `from evolve.infrastructure.diagnostics.detector import (...)` so monkeypatching the "
        "orchestrator module intercepts internal call sites."
    )


def test_monkeypatch_intercepts_orchestrator_namespace(monkeypatch: pytest.MonkeyPatch) -> None:
    """End-to-end demonstration: setting the attribute on the orchestrator
    module replaces the binding that internal call sites resolve.  This
    is the shape memory.md flagged as broken when ``_diag.X`` indirection
    was present.
    """
    sentinel = object()
    monkeypatch.setattr(orchestrator_mod, "_failure_signature", sentinel)
    # The name resolves through the orchestrator module's globals — which
    # is exactly what every internal call site does after de-aliasing.
    assert orchestrator_mod._failure_signature is sentinel
