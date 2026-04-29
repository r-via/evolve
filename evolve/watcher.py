"""Backward-compat shim — real implementation moved to evolve.interfaces.watcher.

.. deprecated::
    Import from ``evolve.interfaces.watcher`` instead.
"""

import warnings as _warnings

_warnings.warn(
    "evolve.watcher moved to evolve.interfaces.watcher",
    DeprecationWarning,
    stacklevel=2,
)

from evolve.interfaces.watcher import (  # noqa: E402, F401
    CONVERGED_EXIT,
    RESUMABLE_SUBCOMMANDS,
    _add_resume,
    _log,
    _spawn_evolve,
    main,
)

if __name__ == "__main__":
    main()
