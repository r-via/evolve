"""Use case: pull latest evolve version from upstream.

One-shot use case — depends only on evolve.domain.
"""

from __future__ import annotations


def update(
    dry_run: bool = False,
    ref: str | None = None,
) -> int:
    """Pull the latest evolve commit from upstream.

    Parameters
    ----------
    dry_run:
        If True, show what would change without applying.
    ref:
        Git ref or version to update to.

    Returns
    -------
    Exit code (0 = updated, 1 = blocked, 2 = error).

    Raises
    ------
    NotImplementedError
        Stub — wiring to infrastructure pending.
    """
    raise NotImplementedError("update stub — DDD migration in progress")
