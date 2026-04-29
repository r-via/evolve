"""Tests for US-076: evolve/spec_archival.py → evolve/infrastructure/claude_sdk/spec_archival.py migration.

Validates:
  (a) Each symbol importable from evolve.infrastructure.claude_sdk.spec_archival
  (b) is-equality between evolve.agent.X and evolve.infrastructure.claude_sdk.spec_archival.X
  (c) No top-level from evolve.agent/orchestrator/cli/spec_archival imports in the new module
"""

from pathlib import Path


_SYMBOLS = [
    "ARCHIVAL_LINE_THRESHOLD",
    "ARCHIVAL_ROUND_INTERVAL",
    "_ARCHIVAL_MAX_SHRINK",
    "_should_run_spec_archival",
    "build_spec_archival_prompt",
    "_run_spec_archival_claude_agent",
    "run_spec_archival",
]


def test_symbols_importable_from_infrastructure():
    """All 7 symbols importable from evolve.infrastructure.claude_sdk.spec_archival."""
    import evolve.infrastructure.claude_sdk.spec_archival as mod
    for name in _SYMBOLS:
        assert hasattr(mod, name), f"{name} missing from infrastructure module"


def test_reexport_identity_with_agent():
    """is-equality: evolve.agent.X is evolve.infrastructure.claude_sdk.spec_archival.X."""
    import evolve.agent as agent_mod
    import evolve.infrastructure.claude_sdk.spec_archival as infra_mod
    for name in _SYMBOLS:
        assert getattr(agent_mod, name) is getattr(infra_mod, name), (
            f"identity mismatch for {name}"
        )


def test_no_forbidden_top_level_imports():
    """Infrastructure module has no from evolve.agent/orchestrator/cli/spec_archival top-level imports."""
    src = Path(__file__).resolve().parent.parent / "evolve" / "infrastructure" / "claude_sdk" / "spec_archival.py"
    assert src.is_file()
    import re
    forbidden = re.compile(
        r"^from evolve\.(agent|orchestrator|cli|spec_archival)( |$|\.)"
    )
    for line in src.read_text().splitlines():
        stripped = line.lstrip()
        # Only check top-level imports (no leading whitespace)
        if line == stripped and forbidden.match(stripped):
            raise AssertionError(
                f"Forbidden top-level import: {stripped}"
            )


def test_shim_reexport_identity():
    """evolve.spec_archival.X is evolve.infrastructure.claude_sdk.spec_archival.X (shim chain)."""
    import evolve.spec_archival as shim_mod
    import evolve.infrastructure.claude_sdk.spec_archival as infra_mod
    for name in _SYMBOLS:
        assert getattr(shim_mod, name) is getattr(infra_mod, name), (
            f"shim identity mismatch for {name}"
        )
