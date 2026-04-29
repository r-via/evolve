"""Backward-compat shim — real code in evolve.infrastructure.claude_sdk.memory_curation.

Migrated in US-075 (DDD migration step 20).
"""

from evolve.infrastructure.claude_sdk.memory_curation import (  # noqa: F401
    CURATION_LINE_THRESHOLD,
    CURATION_ROUND_INTERVAL,
    _CURATION_MAX_SHRINK,
    _should_run_curation,
    build_memory_curation_prompt,
    _run_memory_curation_claude_agent,
    run_memory_curation,
)
