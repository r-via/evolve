"""Backward-compat shim — real code lives in evolve.application.

Round lifecycle handling migrated to the application layer.
"""

from evolve.application.run_loop_lifecycle import (
    _AttemptOutcome,
    _diagnose_attempt_outcome,
)
from evolve.round_success import _handle_round_success  # noqa: F401

__all__ = [
    "_AttemptOutcome",
    "_diagnose_attempt_outcome",
    "_handle_round_success",
]
