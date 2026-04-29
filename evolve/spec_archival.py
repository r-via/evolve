"""Backward-compat shim — real code lives in evolve.infrastructure.claude_sdk.spec_archival."""

from evolve.infrastructure.claude_sdk.spec_archival import (  # noqa: F401
    ARCHIVAL_LINE_THRESHOLD,
    ARCHIVAL_ROUND_INTERVAL,
    _ARCHIVAL_MAX_SHRINK,
    _should_run_spec_archival,
    build_spec_archival_prompt,
    _run_spec_archival_claude_agent,
    run_spec_archival,
)
