"""Backward-compatibility shim — use ``evolve.orchestrator`` instead.

This module re-exports all public and private names that were historically
importable from ``loop``.  It exists for one release cycle and will be
removed in a future version.
"""

from __future__ import annotations

import warnings as _warnings

_warnings.warn(
    "Importing from the root-level 'loop' module is deprecated. "
    "Use 'from evolve.orchestrator import ...' instead.",
    DeprecationWarning,
    stacklevel=2,
)

# --- Re-exports from evolve.orchestrator (loop.py's own code) ---
from evolve.orchestrator import *  # noqa: F401, F403

from evolve.orchestrator import (  # noqa: F401 — private names used by tests
    _auto_detect_check,
    _BACKLOG_VIOLATION_HEADER,
    _BACKLOG_VIOLATION_PREFIX,
    _DEFAULT_README_STALE_THRESHOLD_DAYS,
    _emit_stale_readme_advisory,
    _enforce_convergence_backstop,
    _failure_signature,
    _generate_evolution_report,
    _is_circuit_breaker_tripped,
    _MEMORY_COMPACTION_MARKER,
    _MEMORY_WIPE_THRESHOLD,
    _parse_report_summary,
    _README_STALE_ADVISORY_FMT,
    _run_monitored_subprocess,
    _run_rounds,
    _run_single_round_body,
    _save_subprocess_diagnostic,
)

# --- Re-exports from submodules that were historically importable via loop ---
from evolve.state import (  # noqa: F401
    _check_spec_freshness,
    _compute_backlog_stats,
    _count_blocked,
    _count_checked,
    _count_unchecked,
    _detect_backlog_violation,
    _detect_premature_converged,
    _extract_unchecked_lines,
    _extract_unchecked_set,
    _get_current_improvement,
    _is_needs_package,
    _parse_check_output,
    _parse_restart_required,
    _write_state_json,
)
from evolve.git import (  # noqa: F401
    _ensure_git,
    _git_commit,
    _git_show_at,
    _setup_forever_branch,
)
from evolve.party import (  # noqa: F401
    _forever_restart,
    _run_party_mode,
)
from evolve.tui import TUIProtocol, get_tui  # noqa: F401
from evolve.costs import (  # noqa: F401
    TokenUsage,
    aggregate_usage,
    build_usage_state,
    estimate_cost,
    format_cost,
)
from evolve.hooks import fire_hook, load_hooks  # noqa: F401
