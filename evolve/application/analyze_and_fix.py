"""Use case: implement one improvement (Amelia's dev pass).

Authoring bounded context — depends only on evolve.domain.
"""

from __future__ import annotations

from typing import Optional

from evolve.domain.round import RoundResult


def analyze_and_fix(
    round_num: int,
    project_dir: Optional[str] = None,
    target_us: Optional[str] = None,
) -> RoundResult:
    """Implement one improvement from the backlog.

    Parameters
    ----------
    round_num:
        The round number (1-based).
    project_dir:
        Path to the project being evolved.
    target_us:
        The US item identifier to implement.

    Returns
    -------
    RoundResult with outcome details.

    Raises
    ------
    NotImplementedError
        Stub — wiring to infrastructure pending.
    """
    raise NotImplementedError("analyze_and_fix stub — DDD migration in progress")
