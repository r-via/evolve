"""Use case: run N evolution rounds (the session loop).

Orchestration bounded context — depends only on evolve.domain.
"""

from __future__ import annotations

from typing import List, Optional

from evolve.domain.round import RoundResult


def run_loop(
    project_dir: Optional[str] = None,
    max_rounds: int = 10,
) -> List[RoundResult]:
    """Execute the evolution loop for up to *max_rounds* rounds.

    Parameters
    ----------
    project_dir:
        Path to the project being evolved.
    max_rounds:
        Maximum number of rounds before stopping.

    Returns
    -------
    List of RoundResult, one per completed round.

    Raises
    ------
    NotImplementedError
        Stub — wiring to infrastructure pending.
    """
    raise NotImplementedError("run_loop stub — DDD migration in progress")
