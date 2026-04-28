"""Use case: read-only dry-run analysis.

One-shot use case — depends only on evolve.domain.
"""

from __future__ import annotations


def dry_run(
    project_dir: str | None = None,
    spec_path: str | None = None,
) -> int:
    """Run read-only analysis and produce a dry_run_report.md.

    Parameters
    ----------
    project_dir:
        Path to the project being analyzed.
    spec_path:
        Path to the spec file.

    Returns
    -------
    Exit code (0 = success, 2 = error).

    Raises
    ------
    NotImplementedError
        Stub — wiring to infrastructure pending.
    """
    raise NotImplementedError("dry_run stub — DDD migration in progress")
