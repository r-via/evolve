"""Use case: decide whether a failed round should be retried.

Orchestration bounded context — depends only on evolve.domain.
"""

from __future__ import annotations

from evolve.domain.round import RoundResult


def should_retry(result: RoundResult, attempt: int = 1) -> bool:
    """Determine whether the round should be retried.

    Parameters
    ----------
    result:
        Outcome of the just-completed attempt.
    attempt:
        Current attempt number (1-based, max 3).

    Returns
    -------
    True if a retry is warranted, False otherwise.

    Raises
    ------
    NotImplementedError
        Stub — wiring to infrastructure pending.
    """
    raise NotImplementedError("should_retry stub — DDD migration in progress")
