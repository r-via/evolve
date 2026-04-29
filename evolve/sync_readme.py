"""Backward-compat shim — real implementation in infrastructure.claude_sdk.sync_readme."""

import warnings as _w

_w.warn(
    "evolve.sync_readme moved to "
    "evolve.infrastructure.claude_sdk.sync_readme",
    DeprecationWarning,
    stacklevel=2,
)

from evolve.infrastructure.claude_sdk.sync_readme import (  # noqa: E402, F401
    SYNC_README_NO_CHANGES_SENTINEL,
    build_sync_readme_prompt,
    _run_sync_readme_claude_agent,
    run_sync_readme_agent,
)
