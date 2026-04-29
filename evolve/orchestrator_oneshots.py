"""Backward-compat shim — real code lives in evolve.application.

One-shot orchestrator entry points migrated to individual use-case modules
in the application layer (SPEC § DDD migration).
"""

from evolve.application.diff import diff as run_diff
from evolve.application.dry_run import dry_run as run_dry_run
from evolve.application.sync_readme import sync_readme as run_sync_readme
from evolve.application.validate import validate as run_validate

__all__ = [
    "run_diff",
    "run_dry_run",
    "run_sync_readme",
    "run_validate",
]
