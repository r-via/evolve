"""evolve.interfaces.cli — CLI entry point sub-package.

Re-exports config-resolution symbols from ``config.py`` for
convenient import via ``from evolve.interfaces.cli import _resolve_config``.
"""

from evolve.interfaces.cli.config import (  # noqa: F401
    EFFORT_LEVELS,
    _load_config,
    _resolve_config,
    _validate_effort,
)
