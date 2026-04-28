"""Use case: spec-vs-implementation gap detection.

One-shot use case — depends only on evolve.domain.
"""

from __future__ import annotations


def diff(
    project_dir: str | None = None,
    spec_path: str | None = None,
) -> int:
    """Show delta between spec and implementation.

    Parameters
    ----------
    project_dir:
        Path to the project being analyzed.
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
    raise NotImplementedError("diff stub — DDD migration in progress")
