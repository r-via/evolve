"""Use case: archive stable SPEC sections.

Memory/SPEC-lifecycle bounded context — depends only on evolve.domain.
"""

from __future__ import annotations

from evolve.domain.spec_compliance import SpecClaim


def archive_spec(
    spec_path: str | None = None,
) -> list[SpecClaim]:
    """Run Sid's archival pass on SPEC.md.

    Parameters
    ----------
    spec_path:
        Path to the spec file.

    Returns
    -------
    List of SpecClaim objects representing archived sections.

    Raises
    ------
    NotImplementedError
        Stub — wiring to infrastructure pending.
    """
    raise NotImplementedError("archive_spec stub — DDD migration in progress")
