"""Tests for evolve/infrastructure/filesystem/state_manager.py (US-068).

Verifies the DDD migration of state-management symbols from the flat
``evolve/state.py`` into the infrastructure filesystem layer.
"""

from __future__ import annotations

from pathlib import Path

import evolve.infrastructure.filesystem.state_manager as sm
import evolve.state as state_mod


# ── AC1: all 8 symbols importable from new module ──────────────────

_SYMBOLS = [
    "_runs_base",
    "_RunsLayoutError",
    "_ensure_runs_layout",
    "_check_spec_freshness",
    "_detect_premature_converged",
    "_parse_restart_required",
    "_compute_backlog_stats",
    "_write_state_json",
]


def test_all_symbols_importable_from_state_manager():
    """Each of the 8 symbols is importable from evolve.infrastructure.filesystem.state_manager."""
    for name in _SYMBOLS:
        assert hasattr(sm, name), f"{name} not found in state_manager module"


# ── AC2: is-equality (re-export identity check) ────────────────────

def test_reexport_identity():
    """evolve.state.X is evolve.infrastructure.filesystem.state_manager.X."""
    for name in _SYMBOLS:
        infra_obj = getattr(sm, name)
        shim_obj = getattr(state_mod, name)
        assert infra_obj is shim_obj, (
            f"{name}: identity mismatch — "
            f"state_manager id={id(infra_obj)}, state shim id={id(shim_obj)}"
        )


# ── AC3: leaf-module invariant (no evolve.agent/orchestrator/cli) ──

def test_no_forbidden_top_level_imports():
    """state_manager.py source has no from evolve.agent/orchestrator/cli top-level imports."""
    src = Path(sm.__file__).read_text()
    forbidden = [
        "from evolve.agent",
        "from evolve.orchestrator",
        "from evolve.cli",
    ]
    for pat in forbidden:
        # Check top-level only (lines starting at column 0)
        for line in src.splitlines():
            stripped = line.lstrip()
            if stripped.startswith(pat) and line[0] not in (" ", "\t"):
                raise AssertionError(
                    f"Forbidden top-level import in state_manager.py: {line.strip()}"
                )


# ── AC5: layering test compatibility ───────────────────────────────

def test_layering_linter_safe():
    """state_manager.py has zero ``from evolve.*`` top-level imports that the linter would flag."""
    import ast

    src = Path(sm.__file__).read_text()
    tree = ast.parse(src)
    violations = []
    for node in ast.walk(tree):
        mod = None
        if isinstance(node, ast.ImportFrom) and node.module:
            mod = node.module
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("evolve."):
                    mod = alias.name
                    break
        if mod and mod.startswith("evolve."):
            # Classify: any evolve.X that is NOT domain or infrastructure is a violation
            parts = mod.split(".")
            if len(parts) >= 2 and parts[1] not in ("domain", "infrastructure"):
                violations.append(f"{mod} at line {node.lineno}")
    assert not violations, (
        "Layering-unsafe imports in state_manager.py:\n" + "\n".join(violations)
    )


# ── state_improvements re-exports still work through shim ──────────

def test_state_improvements_reexports_preserved():
    """evolve.state still re-exports state_improvements symbols."""
    si_names = [
        "_count_checked",
        "_count_unchecked",
        "_count_blocked",
        "_detect_backlog_violation",
        "_extract_unchecked_lines",
        "_extract_unchecked_set",
        "_get_current_improvement",
        "_is_needs_package",
        "_parse_check_output",
    ]
    for name in si_names:
        assert hasattr(state_mod, name), (
            f"{name} missing from evolve.state shim — "
            f"state_improvements re-export chain broken"
        )
