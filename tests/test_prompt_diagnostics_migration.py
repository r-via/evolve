"""Tests for US-074: prompt_diagnostics.py migration to infrastructure/claude_sdk/.

Verifies the DDD migration preserves the re-export chain and leaf-module invariant.
"""

from __future__ import annotations

import ast
from pathlib import Path


# -- AC (a): all symbols importable from evolve.infrastructure.claude_sdk.prompt_diagnostics --

def test_symbols_importable_from_infrastructure():
    """Each symbol is importable from the new DDD location."""
    from evolve.infrastructure.claude_sdk.prompt_diagnostics import (
        _PREV_ATTEMPT_LOG_FMT,
        _MEMORY_WIPED_HEADER_FMT,
        _PRIOR_ROUND_ANOMALY_PATTERNS,
        _detect_prior_round_anomalies,
        build_prev_crash_section,
        build_prior_round_audit_section,
        build_prev_attempt_section,
    )
    # Smoke check — all are not None
    assert _PREV_ATTEMPT_LOG_FMT is not None
    assert _MEMORY_WIPED_HEADER_FMT is not None
    assert _PRIOR_ROUND_ANOMALY_PATTERNS is not None
    assert _detect_prior_round_anomalies is not None
    assert build_prev_crash_section is not None
    assert build_prior_round_audit_section is not None
    assert build_prev_attempt_section is not None


# -- AC (b): is-equality between shim re-export and infrastructure source --

def test_is_equality_shim_to_infrastructure():
    """Re-exported symbols via the shim are identical objects."""
    import evolve.prompt_diagnostics as shim
    import evolve.infrastructure.claude_sdk.prompt_diagnostics as infra

    names = [
        "_PREV_ATTEMPT_LOG_FMT",
        "_MEMORY_WIPED_HEADER_FMT",
        "_PRIOR_ROUND_ANOMALY_PATTERNS",
        "_detect_prior_round_anomalies",
        "build_prev_crash_section",
        "build_prior_round_audit_section",
        "build_prev_attempt_section",
    ]
    for name in names:
        assert getattr(shim, name) is getattr(infra, name), (
            f"{name}: shim object is not identical to infrastructure object"
        )


def test_is_equality_agent_to_infrastructure():
    """Re-exported symbols via agent.py are identical objects (full chain)."""
    import evolve.agent as agent_mod
    import evolve.infrastructure.claude_sdk.prompt_diagnostics as infra

    names = [
        "_PREV_ATTEMPT_LOG_FMT",
        "_MEMORY_WIPED_HEADER_FMT",
        "_PRIOR_ROUND_ANOMALY_PATTERNS",
        "_detect_prior_round_anomalies",
        "build_prev_crash_section",
        "build_prior_round_audit_section",
        "build_prev_attempt_section",
    ]
    for name in names:
        assert getattr(agent_mod, name) is getattr(infra, name), (
            f"{name}: agent re-export is not identical to infrastructure object"
        )


# -- AC (c): leaf-module invariant — no from evolve.* top-level imports --

def test_no_evolve_top_level_imports():
    """The infrastructure module has zero top-level ``from evolve.*`` imports."""
    src_path = (
        Path(__file__).resolve().parent.parent
        / "evolve"
        / "infrastructure"
        / "claude_sdk"
        / "prompt_diagnostics.py"
    )
    source = src_path.read_text()
    tree = ast.parse(source)
    violations = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            if node.module.startswith("evolve.") or node.module == "evolve":
                # Only flag top-level imports (col_offset == 0)
                if node.col_offset == 0:
                    violations.append(
                        f"line {node.lineno}: from {node.module} import ..."
                    )
    assert not violations, (
        f"Leaf-module invariant violated — top-level evolve imports found:\n"
        + "\n".join(violations)
    )


# -- AC (d): infrastructure/claude_sdk/__init__.py re-exports --

def test_init_reexports_prompt_diagnostics():
    """The claude_sdk __init__ re-exports all prompt_diagnostics symbols."""
    import evolve.infrastructure.claude_sdk as sdk_pkg
    import evolve.infrastructure.claude_sdk.prompt_diagnostics as infra

    names = [
        "_PREV_ATTEMPT_LOG_FMT",
        "_MEMORY_WIPED_HEADER_FMT",
        "_PRIOR_ROUND_ANOMALY_PATTERNS",
        "_detect_prior_round_anomalies",
        "build_prev_crash_section",
        "build_prior_round_audit_section",
        "build_prev_attempt_section",
    ]
    for name in names:
        assert hasattr(sdk_pkg, name), f"Missing re-export: {name}"
        assert getattr(sdk_pkg, name) is getattr(infra, name), (
            f"{name}: __init__ re-export is not identical"
        )
