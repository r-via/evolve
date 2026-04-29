"""Module-extraction tests for evolve.infrastructure.claude_sdk.party (US-078).

Verifies:
(a) both symbols importable from the new module,
(b) re-export identity holds through the shim chain,
(c) no disallowed top-level imports in the infrastructure file.
"""

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
