"""Backward-compat shim — real code lives in evolve.infrastructure.claude_sdk.prompt_builder.

Migrated as part of DDD restructuring (US-073, migration step 18).
All symbols are re-exported so existing ``from evolve.prompt_builder import X``
call sites and ``patch("evolve.prompt_builder.X")`` test targets continue to work.

The re-exports from ``evolve.prompt_diagnostics`` are preserved here so the
3-link chain (``agent`` → ``prompt_builder`` → ``prompt_diagnostics``) keeps
``patch("evolve.agent.X")`` interception working by ``is``-identity.
"""

from evolve.infrastructure.claude_sdk.prompt_builder import (  # noqa: F401
    PromptBlocks,
    _load_project_context,
    build_prompt_blocks,
    build_prompt,
)

# Re-exports from prompt_diagnostics — preserved for the agent.py re-export
# chain (``agent`` → ``prompt_builder`` → ``prompt_diagnostics``).
from evolve.prompt_diagnostics import (  # noqa: F401
    _PREV_ATTEMPT_LOG_FMT,
    _MEMORY_WIPED_HEADER_FMT,
    _PRIOR_ROUND_ANOMALY_PATTERNS,
    _detect_prior_round_anomalies,
    build_prev_crash_section,
    build_prior_round_audit_section,
    build_prev_attempt_section,
)
