"""Backward-compat shim — real implementation in infrastructure.claude_sdk.oneshot_agents."""

import warnings as _w

_w.warn(
    "evolve.oneshot_agents moved to "
    "evolve.infrastructure.claude_sdk.oneshot_agents",
    DeprecationWarning,
    stacklevel=2,
)

from evolve.infrastructure.claude_sdk.oneshot_agents import (  # noqa: E402, F401
    SYNC_README_NO_CHANGES_SENTINEL,
    _build_check_section,
    build_validate_prompt,
    build_dry_run_prompt,
    _run_readonly_claude_agent,
    _run_dry_run_claude_agent,
    run_dry_run_agent,
    _run_validate_claude_agent,
    run_validate_agent,
    build_diff_prompt,
    _run_diff_claude_agent,
    run_diff_agent,
    build_sync_readme_prompt,
    _run_sync_readme_claude_agent,
    run_sync_readme_agent,
)
