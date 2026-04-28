"""Backward-compat shim — hooks moved to evolve.infrastructure.hooks.

All public symbols are re-exported from the new location.
"""

from evolve.infrastructure.hooks import (
    HOOK_TIMEOUT,
    SUPPORTED_EVENTS,
    fire_hook,
    load_hooks,
)

__all__ = [
    "HOOK_TIMEOUT",
    "SUPPORTED_EVENTS",
    "fire_hook",
    "load_hooks",
]
