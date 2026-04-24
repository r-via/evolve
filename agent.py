"""Backward-compatibility shim — agent has moved to evolve.agent.

This shim re-exports all public names from ``evolve.agent`` so existing
imports continue to work for one release cycle.  It will be removed in
a future version.
"""

import warnings as _warnings

_warnings.warn(
    "Importing from the root-level 'agent' module is deprecated. "
    "Use 'from evolve.agent import ...' instead.",
    DeprecationWarning,
    stacklevel=2,
)

from evolve.agent import *  # noqa: F401,F403,E402
from evolve.agent import (  # noqa: E402  — explicit re-exports for private names used by tests
    _detect_current_attempt,
    _detect_prior_round_anomalies,
    _load_project_context,
    _patch_sdk_parser,
    _build_multimodal_prompt,
    _is_benign_runtime_error,
    _should_retry_rate_limit,
    _run_agent_with_retries,
    _run_readonly_claude_agent,
    _run_dry_run_claude_agent,
    _run_validate_claude_agent,
    _run_diff_claude_agent,
    _run_sync_readme_claude_agent,
    _build_check_section,
    _PREV_ATTEMPT_LOG_FMT,
    _MEMORY_WIPED_HEADER_FMT,
    _PRIOR_ROUND_ANOMALY_PATTERNS,
)
