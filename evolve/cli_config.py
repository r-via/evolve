"""Backward-compat shim — real code lives in ``evolve/interfaces/cli/config.py``.

Migrated per US-082 (DDD migration step 27).  Existing imports
(``from evolve.cli_config import _resolve_config``,
``from evolve.cli import _resolve_config`` via the cli.py re-export,
and ``patch("evolve.cli._resolve_config", ...)`` test targets) keep
working unchanged via this shim + the cli.py re-export chain.
"""

import warnings as _warnings

_warnings.warn(
    "evolve.cli_config moved to evolve.interfaces.cli.config",
    DeprecationWarning,
    stacklevel=2,
)

from evolve.interfaces.cli.config import (  # noqa: E402,F401
    EFFORT_LEVELS,
    _load_config,
    _resolve_config,
    _validate_effort,
)
