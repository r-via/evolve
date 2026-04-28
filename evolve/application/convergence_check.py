"""Use case: decide whether the project has converged to its spec.

Orchestration bounded context — depends only on evolve.domain.
"""

from __future__ import annotations

from typing import List

from evolve.domain.convergence import ConvergenceVerdict, ConvergenceGate


def check_convergence(
    gates: List[ConvergenceGate],
) -> ConvergenceVerdict:
    """Evaluate all convergence gates and return a verdict.

    Parameters
    ----------
    gates:
        Ordered list of gates to evaluate.

    Returns
    -------
    ConvergenceVerdict.CONVERGED when all gates pass,
    ConvergenceVerdict.NOT_CONVERGED otherwise.

    Raises
    ------
    NotImplementedError
        Stub — wiring to infrastructure pending.
    """
    raise NotImplementedError(
        "check_convergence stub — DDD migration in progress"
    )
