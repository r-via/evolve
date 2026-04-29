"""Tests for evolve/interfaces/ DDD layer (US-056).

Verifies all 3 __init__.py files are importable, watcher.py is importable
and raises NotImplementedError, and interfaces files contain no forbidden
imports (agent, orchestrator, cli flat modules).
"""

import ast
import importlib
from pathlib import Path

import pytest

IFACE_ROOT = Path(__file__).resolve().parent.parent / "evolve" / "interfaces"


def test_interfaces_package_importable():
    """The top-level interfaces package is importable."""
    import evolve.interfaces  # noqa: F401


def test_cli_sub_package_importable():
    """evolve.interfaces.cli is importable."""
    mod = importlib.import_module("evolve.interfaces.cli")
    assert mod is not None


def test_tui_sub_package_importable():
    """evolve.interfaces.tui is importable."""
    mod = importlib.import_module("evolve.interfaces.tui")
    assert mod is not None


def test_watcher_importable_and_has_main():
    """evolve.interfaces.watcher is importable and exposes main()."""
    from evolve.interfaces.watcher import main, CONVERGED_EXIT, _add_resume

    assert callable(main)
    assert CONVERGED_EXIT == 0
    assert _add_resume(["start", "."]) == ["start", "--resume", "."]


def test_init_files_exist():
    """All 3 __init__.py files exist on disk."""
    assert (IFACE_ROOT / "__init__.py").exists()
    assert (IFACE_ROOT / "cli" / "__init__.py").exists()
    assert (IFACE_ROOT / "tui" / "__init__.py").exists()


def test_watcher_file_exists():
    """watcher.py exists on disk."""
    assert (IFACE_ROOT / "watcher.py").exists()


def test_no_forbidden_imports_in_interfaces():
    """Interfaces files contain no top-level imports from legacy flat
    modules (agent, orchestrator, cli) — SPEC dependency rule.

    Allowed: from evolve.application.*, from evolve.domain.*,
    from evolve.infrastructure.*
    """
    forbidden_prefixes = (
        "from evolve.agent",
        "from evolve.orchestrator",
        "from evolve.cli",
    )
    violations = []
    for py_file in IFACE_ROOT.rglob("*.py"):
        try:
            tree = ast.parse(py_file.read_text())
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                for prefix in forbidden_prefixes:
                    mod_str = f"from {node.module}"
                    if mod_str.startswith(prefix):
                        violations.append(
                            f"{py_file.relative_to(IFACE_ROOT)}: {mod_str}"
                        )
    assert not violations, (
        f"Forbidden imports in interfaces layer:\n" + "\n".join(violations)
    )
