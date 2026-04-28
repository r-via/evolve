"""Domain types for convergence decisions.

Pure enums + dataclasses — no I/O, no evolve imports.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional


class ConvergenceVerdict(Enum):
    """Whether the project has converged to its spec."""

    CONVERGED = "converged"
    NOT_CONVERGED = "not_converged"


@dataclass
class ConvergenceGate:
    """A single gate that must pass for convergence."""

    name: str
    passed: bool
    reason: Optional[str] = None
