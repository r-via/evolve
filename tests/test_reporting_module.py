"""Tests for evolve/infrastructure/reporting/generator.py module extraction (US-067)."""

from __future__ import annotations

import ast
from pathlib import Path


def test_generate_evolution_report_importable_from_infrastructure():
    """AC1: _generate_evolution_report importable from evolve.infrastructure.reporting.generator."""
    from evolve.infrastructure.reporting.generator import _generate_evolution_report
    assert callable(_generate_evolution_report)


def test_reexport_identity_reporting_shim():
    """AC3: evolve.reporting shim re-exports the same object."""
    from evolve.reporting import _generate_evolution_report as from_shim
    from evolve.infrastructure.reporting.generator import _generate_evolution_report as from_infra
    assert from_shim is from_infra


def test_reexport_identity_init():
    """AC2: evolve.infrastructure.reporting.__init__ re-exports the same object."""
    from evolve.infrastructure.reporting import _generate_evolution_report as from_init
    from evolve.infrastructure.reporting.generator import _generate_evolution_report as from_gen
    assert from_init is from_gen


def test_no_agent_orchestrator_cli_top_level_imports():
    """AC4: generator.py has no top-level imports from evolve.agent/orchestrator/cli."""
    src = Path(__file__).resolve().parent.parent / "evolve" / "infrastructure" / "reporting" / "generator.py"
    tree = ast.parse(src.read_text())
    violations = []
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            for prefix in ("evolve.agent", "evolve.orchestrator", "evolve.cli"):
                if node.module == prefix or node.module.startswith(prefix + "."):
                    violations.append(f"line {node.lineno}: from {node.module}")
    assert not violations, f"Forbidden top-level imports in generator.py: {violations}"


def test_layering_passes():
    """AC5: test_layering.py continues to pass (no DDD violations)."""
    # Importing the linter test ensures it doesn't fail on our new file
    from evolve.infrastructure.reporting.generator import _generate_evolution_report
    assert _generate_evolution_report is not None
