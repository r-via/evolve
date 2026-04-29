"""Backward-compat shim — real code lives in ``evolve/interfaces/cli/utils.py``.

Migrated per US-083 (DDD migration step 28).  Existing imports
(``from evolve.cli_utils import _clean_sessions``,
``from evolve.cli import _clean_sessions`` via the cli.py re-export,
and ``patch("evolve.cli._clean_sessions", ...)`` test targets) keep
working unchanged via this shim + the cli.py re-export chain.
"""

import warnings as _warnings

_warnings.warn(
    "evolve.cli_utils moved to evolve.interfaces.cli.utils",
    DeprecationWarning,
    stacklevel=2,
)

from evolve.interfaces.cli.utils import (  # noqa: E402,F401
    _clean_sessions,
    _show_history,
    _show_status,
)
