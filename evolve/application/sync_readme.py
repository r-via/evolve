"""Use case: refresh README.md from the current spec.

One-shot use case — depends only on evolve.domain.
"""

from __future__ import annotations


def sync_readme(
    project_dir: str | None = None,
    spec_path: str | None = None,
    apply: bool = False,
) -> int:
    """Sync README.md to reflect the current spec.

    Parameters
    ----------
    project_dir:
        Path to the project.
    spec_path:
        Path to the spec file.
    apply:
        If True, write directly to README.md; otherwise produce a proposal.

    Returns
    -------
    Exit code (0 = written, 1 = already in sync, 2 = error).

    Raises
    ------
    NotImplementedError
        Stub — wiring to infrastructure pending.
    """
    raise NotImplementedError("sync_readme stub — DDD migration in progress")
