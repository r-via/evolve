"""evolve — Self-improving evolution loop for any project.

Package marker.  Re-exports all public and private names from the CLI
module (``evolve.cli``) for backward compatibility — ``from evolve import
main``, ``from evolve import _resolve_config``, etc. all continue to work.

The canonical location of all CLI code is ``evolve/cli.py``.
"""

# Re-export everything from evolve.cli for backward compatibility.
# `from X import *` skips private names, so we explicitly import every
# private name that tests or other modules use.
from evolve.cli import *  # noqa: F401, F403 — public names
from evolve.cli import (  # noqa: F401 — private names used by tests
    _check_deps,
    _clean_sessions,
    _DEFAULT_EVOLVE_TOML,
    _DEFAULT_MEMORY_MD,
    _init_config,
    _load_config,
    _parse_round_args,
    _render_default_memory_md,
    _resolve_config,
    _show_history,
    _show_status,
    _validate_effort,
)
