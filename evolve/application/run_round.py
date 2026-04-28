"""Use case: run a single evolution round.

Orchestration bounded context — depends only on evolve.domain.
"""

from __future__ import annotations

from typing import Optional

from evolve.domain.round import RoundKind, RoundResult


def run_round(
    round_num: int,
    kind: RoundKind,
    project_dir: Optional[str] = None,
) -> RoundResult:
    """Execute one evolution round.

    Parameters
    ----------
    round_num:
        The round number (1-based).
    kind:
        Which pipeline call this round represents.
    project_dir:
        Path to the project being evolved.

    Returns
    -------
    RoundResult with outcome details.

    Raises
    ------
    NotImplementedError
        Stub — wiring to infrastructure pending.
    """
    raise NotImplementedError("run_round stub — DDD migration in progress")
