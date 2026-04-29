"""Backward-compat shim — real code lives in evolve.infrastructure.diagnostics.

All symbols re-exported so existing ``from evolve.subprocess_monitor import X``
etc. continue to work unchanged.
"""

from evolve.infrastructure.diagnostics.subprocess_monitor import (
    WATCHDOG_TIMEOUT,
    _run_monitored_subprocess,
)

__all__ = [
    "WATCHDOG_TIMEOUT",
    "_run_monitored_subprocess",
]
