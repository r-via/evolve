"""Domain types for the improvement backlog.

Pure dataclasses — no I/O, no evolve imports.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class USItem:
    """A single user-story item in improvements.md."""

    id: str  # e.g. "US-052"
    summary: str
    type_tag: str  # "functional" | "performance"
    priority: str  # "P1" | "P2" | "P3"
    checked: bool
    acceptance_criteria: List[str] = field(default_factory=list)
    needs_package: bool = False
    blocked: bool = False
    blocked_reason: Optional[str] = None


@dataclass
class BacklogState:
    """Aggregate counts for the backlog."""

    pending: int
    done: int
    blocked: int


@dataclass
class Backlog:
    """The full backlog: items + aggregate state."""

    items: List[USItem] = field(default_factory=list)
    state: BacklogState = field(
        default_factory=lambda: BacklogState(pending=0, done=0, blocked=0)
    )
