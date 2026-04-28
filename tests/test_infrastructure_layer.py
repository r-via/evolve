"""Tests for evolve/infrastructure/ DDD layer (US-055).

Verifies all 8 __init__.py files are importable and that infrastructure
files contain no forbidden imports (application, interfaces, cli).
"""

import ast
from pathlib import Path

INFRA_ROOT = Path(__file__).resolve().parent.parent / "evolve" / "infrastructure"

# All sub-packages that must exist under infrastructure/
SUB_PACKAGES = [
    "claude_sdk",
    "git",
    "filesystem",
    "hooks",
    "costs",
    "diagnostics",
    "reporting",
]


def test_infrastructure_package_importable():
    """The top-level infrastructure package is importable."""
    import evolve.infrastructure  # noqa: F401


def test_all_sub_packages_importable():
    """Each sub-package under infrastructure/ is importable."""
    import importlib

    for sub in SUB_PACKAGES:
        mod = importlib.import_module(f"evolve.infrastructure.{sub}")
        assert mod is not None, f"evolve.infrastructure.{sub} failed to import"


def test_infrastructure_init_files_exist():
    """All 8 __init__.py files exist on disk."""
    assert (INFRA_ROOT / "__init__.py").exists()
    for sub in SUB_PACKAGES:
        init = INFRA_ROOT / sub / "__init__.py"
        assert init.exists(), f"Missing {init}"


def test_no_forbidden_imports_in_infrastructure():
    """Infrastructure files contain no top-level imports from
    application, interfaces, or cli — SPEC dependency rule."""
    forbidden_prefixes = (
        "from evolve.application",
        "from evolve.interfaces",
        "from evolve.cli",
        "from evolve.agent",
        "from evolve.orchestrator",
    )
    violations = []
    for py_file in INFRA_ROOT.rglob("*.py"):
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
                            f"{py_file.relative_to(INFRA_ROOT)}: {mod_str}"
                        )
    assert not violations, (
        f"Forbidden imports in infrastructure layer:\n"
        + "\n".join(violations)
    )
