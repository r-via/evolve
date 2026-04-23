"""Backward-compatibility shim — hooks has moved to evolve.hooks.

This shim re-exports all public names from ``evolve.hooks`` so existing
imports continue to work for one release cycle.  It will be removed in
a future version.

.. deprecated::
    Import from ``evolve.hooks`` instead of ``hooks``.
"""

from __future__ import annotations

import warnings as _warnings

_warnings.warn(
    "Importing from the top-level 'hooks' module is deprecated. "
    "Use 'from evolve.hooks import ...' instead.",
    DeprecationWarning,
    stacklevel=2,
)

from evolve.hooks import *  # noqa: F401,F403
from evolve.hooks import (  # noqa: F401 — explicit re-exports for type checkers
    HOOK_TIMEOUT,
    SUPPORTED_EVENTS,
    fire_hook,
    load_hooks,
    logger,
)
