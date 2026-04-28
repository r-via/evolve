"""Domain types for adversarial review verdicts.

Pure enums + dataclasses — no I/O, no evolve imports.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional


class ReviewVerdict(Enum):
    """Verdict from Zara's adversarial review."""

    APPROVED = "approved"
    CHANGES_REQUESTED = "changes_requested"
    BLOCKED = "blocked"


@dataclass
class Finding:
    """A single finding from an adversarial review."""

    severity: str  # "HIGH" | "MEDIUM" | "LOW"
    description: str
    file_path: Optional[str] = None
    line: Optional[int] = None
