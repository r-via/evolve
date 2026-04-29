"""Backward-compat shim — real implementation in infrastructure.claude_sdk.diff_agent."""

import warnings as _w

_w.warn(
    "evolve.diff_agent moved to "
    "evolve.infrastructure.claude_sdk.diff_agent",
    DeprecationWarning,
    stacklevel=2,
)

from evolve.infrastructure.claude_sdk.diff_agent import (  # noqa: E402, F401
    build_diff_prompt,
    _run_diff_claude_agent,
    run_diff_agent,
)
