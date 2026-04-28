"""Use case: curate memory.md between rounds.

Memory/SPEC-lifecycle bounded context — depends only on evolve.domain.
"""

from __future__ import annotations

from evolve.domain.memory import MemoryLog


def curate_memory(
    memory_log: MemoryLog | None = None,
) -> MemoryLog:
    """Run Mira's four-pass curation on memory.md.

    Parameters
    ----------
    memory_log:
        The current memory log to curate.

    Returns
    -------
    MemoryLog with curated entries.

    Raises
    ------
    NotImplementedError
        Stub — wiring to infrastructure pending.
    """
    raise NotImplementedError("curate_memory stub — DDD migration in progress")
