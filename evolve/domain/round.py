"""Domain types for round lifecycle.

Pure enums + dataclasses — no I/O, no evolve imports.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional


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
