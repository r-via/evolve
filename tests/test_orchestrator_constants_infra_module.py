"""Regression tests for the DDD migration of orchestrator_constants.

US-087: ``evolve/orchestrator_constants.py`` →
``evolve/infrastructure/filesystem/orchestrator_constants.py``.

Validates the three standard migration invariants:
1. Every constant is importable from the infrastructure module.
2. ``is``-equality between flat shim and infrastructure module.
3. Infrastructure module has no forbidden top-level imports.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_LEAF_INVARIANT = re.compile(
    r"^from evolve\.(agent|orchestrator|cli|state)( |$|\.)",
    re.MULTILINE,
)

_SYMBOLS = (
    "MAX_DEBUG_RETRIES",
    "_MEMORY_COMPACTION_MARKER",
    "_MEMORY_WIPE_THRESHOLD",
    "_BACKLOG_VIOLATION_PREFIX",
    "_BACKLOG_VIOLATION_HEADER",
)


@pytest.mark.parametrize("name", _SYMBOLS)
def test_importable_from_infrastructure(name):
    """Each constant importable from evolve.infrastructure.filesystem.orchestrator_constants."""
    import evolve.infrastructure.filesystem.orchestrator_constants as infra_mod

    assert hasattr(infra_mod, name), (
        f"{name} not exported by evolve.infrastructure.filesystem.orchestrator_constants"
    )


@pytest.mark.parametrize("name", _SYMBOLS)
def test_flat_shim_identity(name):
    """Flat shim re-export preserves is-identity with infrastructure module."""
    import evolve.infrastructure.filesystem.orchestrator_constants as infra_mod
    import evolve.orchestrator_constants as flat_mod

    assert getattr(flat_mod, name) is getattr(infra_mod, name), (
        f"{name} identity broken between flat shim and infrastructure module"
    )


@pytest.mark.parametrize("name", _SYMBOLS)
def test_filesystem_init_reexports(name):
    """evolve.infrastructure.filesystem re-exports the constant."""
    import evolve.infrastructure.filesystem as fs_mod

    assert hasattr(fs_mod, name), (
        f"{name} not re-exported by evolve.infrastructure.filesystem"
    )


def test_infrastructure_module_is_leaf():
    """No forbidden top-level imports in the infrastructure module."""
    src_path = (
        Path(__file__).resolve().parent.parent
        / "evolve"
        / "infrastructure"
        / "filesystem"
        / "orchestrator_constants.py"
    )
    src = src_path.read_text(encoding="utf-8")
    matches = _LEAF_INVARIANT.findall(src)
    assert matches == [], (
        f"infrastructure orchestrator_constants.py violates leaf-module invariant: {matches}"
    )
