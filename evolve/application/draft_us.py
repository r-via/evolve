"""Use case: draft a single user story.

Authoring bounded context — depends only on evolve.domain.
"""

from __future__ import annotations

from typing import Optional

from evolve.domain.improvement import USItem


def draft_us(
    spec_path: Optional[str] = None,
    memory_path: Optional[str] = None,
) -> USItem:
    """Draft one new user story for the backlog.

    Parameters
    ----------
    spec_path:
        Path to the spec file (SPEC.md or --spec target).
    memory_path:
        Path to memory.md for cross-round context.

    Returns
    -------
    USItem representing the newly drafted story.

    Raises
    ------
    NotImplementedError
        Stub — wiring to infrastructure pending.
    """
    raise NotImplementedError("draft_us stub — DDD migration in progress")
