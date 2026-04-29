"""Backward-compat shim — real implementation in evolve.infrastructure.claude_sdk.party.

DDD migration step 23 (US-078).
"""

import warnings

warnings.warn(
    "evolve.party moved to evolve.infrastructure.claude_sdk.party",
    DeprecationWarning,
    stacklevel=2,
)

from evolve.infrastructure.claude_sdk.party import (  # noqa: F401
    _run_party_mode,
    _forever_restart,
)

__all__ = ["_run_party_mode", "_forever_restart"]
