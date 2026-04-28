"""Use case: spec compliance validation.

One-shot use case — depends only on evolve.domain.
"""

from __future__ import annotations


def validate(
    project_dir: str | None = None,
    spec_path: str | None = None,
) -> int:
    """Run claim-by-claim spec validation.

    Parameters
    ----------
    project_dir:
        Path to the project being validated.
    spec_path:
        Path to the spec file.

    Returns
    -------
    Exit code (0 = compliant, 1 = gaps found, 2 = error).

    Raises
    ------
    NotImplementedError
        Stub — wiring to infrastructure pending.
    """
    raise NotImplementedError("validate stub — DDD migration in progress")
