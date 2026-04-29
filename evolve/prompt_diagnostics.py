"""Backward-compat shim — real code lives in evolve.infrastructure.claude_sdk.prompt_diagnostics.

Migrated as part of DDD restructuring (US-074, migration step 19).
All symbols are re-exported so existing ``from evolve.prompt_diagnostics import X``
call sites and ``patch("evolve.prompt_diagnostics.X")`` test targets continue to work.
"""

from evolve.infrastructure.claude_sdk.prompt_diagnostics import (  # noqa: F401
    _PREV_ATTEMPT_LOG_FMT,
    _MEMORY_WIPED_HEADER_FMT,
    _PRIOR_ROUND_ANOMALY_PATTERNS,
    _detect_prior_round_anomalies,
    build_prev_crash_section,
    build_prior_round_audit_section,
    build_prev_attempt_section,
)
