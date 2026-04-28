"""Tests for evolve/infrastructure/hooks/executor.py module extraction (US-065).

Verifies:
  (a) All public symbols importable from evolve.infrastructure.hooks.executor
  (b) is-equality between evolve.hooks.X and evolve.infrastructure.hooks.executor.X
  (c) executor.py has no from evolve.* top-level imports (leaf invariant)
"""

from pathlib import Path


def test_symbols_importable_from_executor():
    from evolve.infrastructure.hooks.executor import (
        HOOK_TIMEOUT,
        SUPPORTED_EVENTS,
        fire_hook,
        load_hooks,
    )
    assert callable(load_hooks)
    assert callable(fire_hook)
    assert isinstance(SUPPORTED_EVENTS, frozenset)
    assert isinstance(HOOK_TIMEOUT, int)


def test_reexport_identity():
    import evolve.hooks as shim
    import evolve.infrastructure.hooks.executor as executor

    assert shim.load_hooks is executor.load_hooks
    assert shim.fire_hook is executor.fire_hook
    assert shim.SUPPORTED_EVENTS is executor.SUPPORTED_EVENTS
    assert shim.HOOK_TIMEOUT is executor.HOOK_TIMEOUT


def test_executor_no_evolve_top_level_imports():
    src = (
        Path(__file__).resolve().parent.parent
        / "evolve"
        / "infrastructure"
        / "hooks"
        / "executor.py"
    )
    for line in src.read_text().splitlines():
        stripped = line.lstrip()
        if stripped != line:
            continue  # indented — skip (function-local)
        assert not stripped.startswith("from evolve."), (
            f"Top-level evolve import in executor.py: {stripped}"
        )


def test_infrastructure_init_reexports():
    """Infrastructure __init__.py re-exports match executor symbols."""
    import evolve.infrastructure.hooks as pkg
    import evolve.infrastructure.hooks.executor as executor

    assert pkg.load_hooks is executor.load_hooks
    assert pkg.fire_hook is executor.fire_hook
    assert pkg.SUPPORTED_EVENTS is executor.SUPPORTED_EVENTS
    assert pkg.HOOK_TIMEOUT is executor.HOOK_TIMEOUT
