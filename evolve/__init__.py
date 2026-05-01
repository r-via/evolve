"""evolve — Self-improving evolution loop for any project.

Package marker.
"""

from evolve.interfaces.cli.main import *  # noqa: F401, F403 — public names
from evolve.interfaces.cli.main import (  # noqa: F401 — private names used by tests
    _check_deps,
    _DEFAULT_EVOLVE_TOML,
    _DEFAULT_MEMORY_MD,
    _init_config,
    _parse_round_args,
    _render_default_memory_md,
)
from evolve.interfaces.cli.utils import (
    _clean_sessions,
    _show_history,
    _show_status,
)
from evolve.interfaces.cli.config import (
    _load_config,
    _resolve_config,
    _validate_effort,
    EFFORT_LEVELS,
)
