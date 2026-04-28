"""Domain types for round lifecycle.

Pure enums + dataclasses — no I/O, no evolve imports.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional


class RoundKind(Enum):
    """Which pipeline call a round represents."""

    DRAFT = "draft"
    IMPLEMENT = "implement"
    REVIEW = "review"


@dataclass
class RoundResult:
    """Outcome of a single round."""

    round_num: int
    kind: RoundKind
    succeeded: bool
    subtype: Optional[str] = None  # SDK ResultMessage.subtype
    num_turns: Optional[int] = None


@dataclass
class RoundAttempt:
    """One subprocess invocation within a round's retry loop."""

    attempt_num: int
    subtype: Optional[str] = None  # SDK ResultMessage.subtype
    diagnostic: Optional[str] = None
    succeeded: bool = False


@dataclass
class Round:
    """Aggregate root for a single evolution round."""

    round_num: int
    kind: RoundKind
    attempts: List[RoundAttempt] = field(default_factory=list)
    result: Optional[RoundResult] = None
