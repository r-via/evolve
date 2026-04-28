"""Use case: adversarial review of a single round.

Authoring bounded context — depends only on evolve.domain.
"""

from __future__ import annotations

from typing import Optional

from evolve.domain.review_verdict import ReviewVerdict


def review_round(
    round_num: int,
    diff: Optional[str] = None,
) -> ReviewVerdict:
    """Run Zara's adversarial review on a round's changes.

    Parameters
    ----------
    round_num:
        The round number being reviewed.
    diff:
        The git diff of the round's commit.

    Returns
    -------
    ReviewVerdict (APPROVED, CHANGES_REQUESTED, or BLOCKED).

    Raises
    ------
    NotImplementedError
        Stub — wiring to infrastructure pending.
    """
    raise NotImplementedError("review_round stub — DDD migration in progress")
