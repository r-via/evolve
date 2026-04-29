"""Tests for evolve/interfaces/watcher.py DDD migration (US-085).

Verifies:
(a) all 6 symbols importable from evolve.interfaces.watcher
(b) is-equality between evolve.watcher.X and evolve.interfaces.watcher.X
(c) evolve/interfaces/watcher.py contains no from evolve.* top-level imports
"""

import warnings
from pathlib import Path


IFACE_WATCHER = (
    Path(__file__).resolve().parent.parent
    / "evolve"
    / "interfaces"
    / "watcher.py"
)

SYMBOLS = [
    "CONVERGED_EXIT",
    "RESUMABLE_SUBCOMMANDS",
    "_add_resume",
    "_spawn_evolve",
    "_log",
    "main",
]


def test_symbols_importable_from_interfaces_watcher():
    """Each symbol is importable from evolve.interfaces.watcher."""
    from evolve.interfaces import watcher as mod

    for name in SYMBOLS:
        assert hasattr(mod, name), f"missing: {name}"


def test_is_equality_with_flat_shim():
    """evolve.watcher.X is evolve.interfaces.watcher.X for every symbol."""
    from evolve.interfaces import watcher as canonical

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        from evolve import watcher as shim

    for name in SYMBOLS:
        assert getattr(shim, name) is getattr(canonical, name), (
            f"{name}: shim is not canonical"
        )


def test_no_evolve_imports_at_top_level():
    """evolve/interfaces/watcher.py source has zero from evolve.* top-level
    imports (leaf-module invariant)."""
    src = IFACE_WATCHER.read_text()
    for line in src.splitlines():
        stripped = line.lstrip()
        # top-level = no leading whitespace
        if line == stripped and stripped.startswith("from evolve"):
            raise AssertionError(
                f"Top-level evolve import found: {stripped}"
            )


def test_add_resume_idempotent():
    """_add_resume is idempotent — already-present --resume kept."""
    from evolve.interfaces.watcher import _add_resume

    args = ["start", ".", "--resume", "--check", "pytest"]
    assert _add_resume(args) == args


def test_add_resume_no_subcommand():
    """_add_resume appends --resume at end when no known subcommand."""
    from evolve.interfaces.watcher import _add_resume

    args = ["--check", "pytest"]
    assert _add_resume(args) == ["--check", "pytest", "--resume"]
