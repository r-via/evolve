"""Backward-compatibility shim — costs has moved to evolve.costs.

This shim re-exports all public names from ``evolve.costs`` so existing
imports continue to work for one release cycle.  It will be removed in
a future version.

.. deprecated::
    Import from ``evolve.costs`` instead of ``costs``.
"""

from __future__ import annotations

import warnings as _warnings

_warnings.warn(
    "Importing from the top-level 'costs' module is deprecated. "
    "Use 'from evolve.costs import ...' instead.",
    DeprecationWarning,
    stacklevel=2,
)

from evolve.costs import *  # noqa: F401,F403
from evolve.costs import (  # noqa: F401 — explicit re-exports for type checkers
    RATES,
    TokenUsage,
    aggregate_usage,
    build_usage_state,
    estimate_cost,
    format_cost,
)
