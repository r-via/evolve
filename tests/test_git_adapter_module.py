"""Tests for evolve.infrastructure.git.adapter — DDD migration step 10 (US-064).

Verifies:
  (a) all 4 symbols importable from evolve.infrastructure.git
  (b) is-equality with evolve.git re-exports
  (c) adapter.py has no top-level from evolve.* imports
  (d) test_layering.py passes (no DDD violations)
"""

from pathlib import Path


def test_symbols_importable_from_infrastructure_git():
    """Each symbol is importable from the infrastructure.git package."""
    from evolve.infrastructure.git import (
        _ensure_git,
        _git_commit,
        _git_show_at,
        _setup_forever_branch,
    )
    assert callable(_ensure_git)
    assert callable(_git_commit)
    assert callable(_git_show_at)
    assert callable(_setup_forever_branch)


def test_reexport_identity_with_evolve_git():
    """evolve.git.X is evolve.infrastructure.git.X (re-export identity)."""
    import evolve.git as flat
    import evolve.infrastructure.git as infra

    assert flat._ensure_git is infra._ensure_git
    assert flat._git_commit is infra._git_commit
    assert flat._git_show_at is infra._git_show_at
    assert flat._setup_forever_branch is infra._setup_forever_branch


def test_adapter_no_toplevel_evolve_imports():
    """adapter.py has no top-level `from evolve.` imports (leaf invariant)."""
    src = (
        Path(__file__).resolve().parent.parent
        / "evolve"
        / "infrastructure"
        / "git"
        / "adapter.py"
    ).read_text()
    for line in src.splitlines():
        stripped = line.lstrip()
        # Only check top-level imports (col 0)
        if line == stripped and stripped.startswith("from evolve."):
            raise AssertionError(
                f"Top-level evolve import found in adapter.py: {stripped}"
            )
