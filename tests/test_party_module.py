"""Module-extraction tests for evolve.infrastructure.claude_sdk.party (US-078).

Verifies:
(a) both symbols importable from the new module,
(b) re-export identity holds through the shim chain,
(c) no disallowed top-level imports in the infrastructure file,
(d) shim emits DeprecationWarning on import,
(e) _forever_restart has no dead code after return.
"""

import warnings
from pathlib import Path


def test_symbols_importable_from_infrastructure():
    """All extracted symbols are importable from evolve.infrastructure.claude_sdk.party."""
    from evolve.infrastructure.claude_sdk.party import (
        _run_party_mode,
        _forever_restart,
    )
    assert callable(_run_party_mode)
    assert callable(_forever_restart)


def test_reexport_identity_party_shim():
    """evolve.party.X is evolve.infrastructure.claude_sdk.party.X."""
    from evolve.infrastructure.claude_sdk import party as infra_mod
    from evolve import party as shim_mod

    assert shim_mod._run_party_mode is infra_mod._run_party_mode
    assert shim_mod._forever_restart is infra_mod._forever_restart


def test_reexport_identity_orchestrator():
    """evolve.orchestrator.X is evolve.infrastructure.claude_sdk.party.X."""
    from evolve.infrastructure.claude_sdk import party as infra_mod
    from evolve import orchestrator as orch_mod

    assert orch_mod._run_party_mode is infra_mod._run_party_mode
    assert orch_mod._forever_restart is infra_mod._forever_restart


def test_no_disallowed_top_level_imports():
    """Infrastructure file has no top-level from evolve.agent/orchestrator/cli imports."""
    src = (
        Path(__file__).resolve().parent.parent
        / "evolve"
        / "infrastructure"
        / "claude_sdk"
        / "party.py"
    ).read_text()
    for line in src.splitlines():
        stripped = line.lstrip()
        # Only check top-level (no leading whitespace)
        if line == stripped and stripped.startswith("from evolve."):
            # Allowed: from evolve.infrastructure.*
            assert stripped.startswith("from evolve.infrastructure"), (
                f"Disallowed top-level import: {stripped}"
            )


def test_shim_emits_deprecation_warning():
    """evolve.party shim emits DeprecationWarning on import."""
    import importlib
    import sys

    # Remove cached module to re-trigger the warning
    mod = sys.modules.pop("evolve.party", None)
    try:
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            importlib.import_module("evolve.party")
        deprecation_msgs = [
            w for w in caught
            if issubclass(w.category, DeprecationWarning)
            and "evolve.party" in str(w.message)
        ]
        assert len(deprecation_msgs) >= 1, (
            "evolve.party shim should emit a DeprecationWarning"
        )
    finally:
        # Restore original module if it existed
        if mod is not None:
            sys.modules["evolve.party"] = mod


def test_forever_restart_no_dead_code():
    """_forever_restart has no unreachable code after return statements."""
    import ast

    src_path = (
        Path(__file__).resolve().parent.parent
        / "evolve"
        / "infrastructure"
        / "claude_sdk"
        / "party.py"
    )
    tree = ast.parse(src_path.read_text())

    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_forever_restart":
            body = node.body
            for i, stmt in enumerate(body):
                if isinstance(stmt, ast.Return) and i < len(body) - 1:
                    # Statements after a return are dead code
                    remaining = body[i + 1:]
                    assert all(
                        isinstance(s, (ast.Pass, ast.Expr))
                        and isinstance(getattr(s, "value", None), ast.Constant)
                        for s in remaining
                    ) or len(remaining) == 0, (
                        f"Dead code after return in _forever_restart "
                        f"at line {stmt.lineno}"
                    )
            break
