"""Backward-compat shim — real code lives in evolve.application.

Round runner migrated to the application layer (SPEC § DDD migration).
"""

from evolve.application.run_round import (
    _run_single_round_body,
    run_single_round,
)

__all__ = [
    "_run_single_round_body",
    "run_single_round",
]
