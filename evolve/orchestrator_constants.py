"""Backward-compat shim — real code in evolve.infrastructure.filesystem.orchestrator_constants."""

from evolve.infrastructure.filesystem.orchestrator_constants import (  # noqa: F401
    MAX_DEBUG_RETRIES,
    _BACKLOG_VIOLATION_HEADER,
    _BACKLOG_VIOLATION_PREFIX,
    _MEMORY_COMPACTION_MARKER,
    _MEMORY_WIPE_THRESHOLD,
)

__all__ = [
    "MAX_DEBUG_RETRIES",
    "_BACKLOG_VIOLATION_HEADER",
    "_BACKLOG_VIOLATION_PREFIX",
    "_MEMORY_COMPACTION_MARKER",
    "_MEMORY_WIPE_THRESHOLD",
]
