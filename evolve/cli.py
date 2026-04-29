"""Backward-compat shim — real code lives in evolve.interfaces.cli.

The entry point for the ``evolve`` command is ``main()`` in this module
(re-exported from ``evolve.interfaces.cli.main``).
"""

from evolve.interfaces.cli.config import (
    EFFORT_LEVELS,
    _load_config,
    _resolve_config,
    _validate_effort,
)
from evolve.interfaces.cli.main import (
    main,
    _check_deps,
    _init_config,
    _parse_round_args,
    _render_default_memory_md,
    _DEFAULT_EVOLVE_TOML,
    _DEFAULT_MEMORY_MD,
)
from evolve.interfaces.cli.utils import (
    _clean_sessions,
    _show_history,
    _show_status,
)

__all__ = [
    "EFFORT_LEVELS",
    "_DEFAULT_EVOLVE_TOML",
    "_DEFAULT_MEMORY_MD",
    "_check_deps",
    "_clean_sessions",
    "_init_config",
    "_load_config",
    "_parse_round_args",
    "_render_default_memory_md",
    "_resolve_config",
    "_show_history",
    "_show_status",
    "_validate_effort",
    "main",
]

if __name__ == "__main__":
    main()
