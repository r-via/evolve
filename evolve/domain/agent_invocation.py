"""Domain types for agent invocations.

Pure enums + dataclasses — no I/O, no evolve imports.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional


class AgentRole(Enum):
    """Which persona/role an agent call represents."""

    DRAFT = "draft"
    IMPLEMENT = "implement"
    REVIEW = "review"
    CURATE = "curate"
    ARCHIVE = "archive"


class AgentSubtype(Enum):
    """SDK ResultMessage.subtype values."""

    SUCCESS = "success"
    ERROR_MAX_TURNS = "error_max_turns"
    ERROR_DURING_EXECUTION = "error_during_execution"


@dataclass
class AgentResult:
    """Outcome of a single agent SDK invocation."""

    role: AgentRole
    subtype: AgentSubtype
    num_turns: int
    duration_ms: Optional[int] = None
