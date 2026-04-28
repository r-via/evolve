"""Domain types for spec compliance verification.

Pure dataclasses — no I/O, no evolve imports.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class SpecClaim:
    """A single claim extracted from the spec."""

    section: str
    description: str
    implemented: bool = False


@dataclass
class ClaimVerification:
    """Result of verifying a single spec claim against the codebase."""

    claim: SpecClaim
    evidence: Optional[str] = None
    passed: bool = False
