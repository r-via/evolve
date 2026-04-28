"""Backward-compat shim — real code lives in evolve.infrastructure.git.adapter.

All symbols re-exported so ``from evolve.git import _ensure_git`` etc.
continue to work unchanged.
"""

from evolve.infrastructure.git import (
    _ensure_git,
    _git_commit,
    _git_show_at,
    _setup_forever_branch,
)

__all__ = [
    "_ensure_git",
    "_git_commit",
    "_git_show_at",
    "_setup_forever_branch",
]
