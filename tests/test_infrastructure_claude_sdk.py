"""Tests for evolve/infrastructure/claude_sdk/ DDD migration (US-069).

Verifies the migration from evolve/agent_runtime.py to
evolve/infrastructure/claude_sdk/runtime.py preserves all symbols
and re-export identity.
"""

import re
from pathlib import Path

import evolve.infrastructure.claude_sdk.runtime as shim_mod
import evolve.infrastructure.claude_sdk.runtime as runtime_mod

# Symbols that must be present in the infrastructure module
_EXPECTED_SYMBOLS = [
    "MODEL",
    "MAX_TURNS",
    "DRAFT_EFFORT",
    "REVIEW_EFFORT",
    "_patch_sdk_parser",
    "_summarise_tool_input",
    "_run_agent_with_retries",
]


def test_all_symbols_importable_from_runtime():
    """Each hoisted symbol is importable from evolve.infrastructure.claude_sdk.runtime."""
    for name in _EXPECTED_SYMBOLS:
        assert hasattr(runtime_mod, name), (
            f"{name} not found in evolve.infrastructure.claude_sdk.runtime"
        )


def test_re_export_identity():
    """evolve.agent_runtime.X is evolve.infrastructure.claude_sdk.runtime.X."""
    for name in _EXPECTED_SYMBOLS:
        assert getattr(shim_mod, name) is getattr(runtime_mod, name), (
            f"Identity mismatch for {name}: "
            f"shim={id(getattr(shim_mod, name))}, "
            f"runtime={id(getattr(runtime_mod, name))}"
        )


def test_init_re_exports():
    """evolve.infrastructure.claude_sdk.__init__ re-exports all 7 symbols."""
    import evolve.infrastructure.claude_sdk as pkg
    for name in _EXPECTED_SYMBOLS:
        assert hasattr(pkg, name), (
            f"{name} not found in evolve.infrastructure.claude_sdk"
        )
        assert getattr(pkg, name) is getattr(runtime_mod, name)


def test_runtime_no_evolve_top_level_imports():
    """runtime.py has no top-level ``from evolve.*`` imports (leaf invariant)."""
    src = (Path(__file__).resolve().parent.parent
           / "evolve" / "infrastructure" / "claude_sdk" / "runtime.py"
           ).read_text()
    matches = re.findall(r"^from evolve\.", src, flags=re.MULTILINE)
    assert matches == [], (
        f"runtime.py must not have module-top 'from evolve.*' imports; "
        f"found: {matches}"
    )


def test_shim_imports_only_from_infrastructure():
    """agent_runtime.py shim imports only from evolve.infrastructure.claude_sdk."""
    src = (Path(__file__).resolve().parent.parent
           / "evolve" / "agent_runtime.py").read_text()
    matches = re.findall(r"^from evolve\.\S+", src, flags=re.MULTILINE)
    for m in matches:
        assert m.startswith("from evolve.infrastructure.claude_sdk"), (
            f"agent_runtime.py should only import from "
            f"evolve.infrastructure.claude_sdk, found: {m}"
        )
