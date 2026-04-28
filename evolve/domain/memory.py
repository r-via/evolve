"""Domain types for the cumulative memory log.

Pure enums + dataclasses — no I/O, no evolve imports.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional


@dataclass
class MemoryEntry:
    """A single entry in memory.md."""

    section: str  # "Errors" | "Decisions" | "Patterns" | "Insights"
    title: str
    round_ref: Optional[str] = None
    body: str = ""


class CompactionDecision(Enum):
    """Mira's per-entry triage verdict during curation."""

    KEEP = "keep"
    ARCHIVE = "archive"
    DELETE = "delete"


@dataclass
class MemoryLog:
    """The full memory log: list of entries."""

    entries: List[MemoryEntry] = field(default_factory=list)
