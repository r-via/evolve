"""State management — backward-compat shim.

All code migrated to ``evolve.infrastructure.filesystem.state_manager``
(DDD migration step 14). This shim preserves existing
``from evolve.state import _runs_base`` (and friends) call sites.

Also re-exports improvement-parsing helpers from ``evolve.state_improvements``
(US-044) so ``from evolve.state import _count_unchecked`` keeps working.
"""

from __future__ import annotations

# Re-export from infrastructure filesystem adapter
from evolve.infrastructure.filesystem import (
    _RunsLayoutError,
    _check_spec_freshness,
    _compute_backlog_stats,
    _detect_premature_converged,
    _ensure_runs_layout,
    _parse_restart_required,
    _runs_base,
    _write_state_json,
)

# Re-export improvement-parsing helpers from the leaf module
# (US-044: keeps state.py under SPEC.md § "Hard rule" 500-line cap).
# Existing ``from evolve.state import _count_unchecked`` (and friends)
# call sites — orchestrator, diagnostics, agent, tests — continue to
# work via this re-export chain.
from evolve.state_improvements import (
    _count_blocked,
    _count_checked,
    _count_unchecked,
    _detect_backlog_violation,
    _extract_unchecked_lines,
    _extract_unchecked_set,
    _get_current_improvement,
    _is_needs_package,
    _parse_check_output,
)

__all__ = [
    # From infrastructure.filesystem.state_manager
    "_RunsLayoutError",
    "_check_spec_freshness",
    "_compute_backlog_stats",
    "_detect_premature_converged",
    "_ensure_runs_layout",
    "_parse_restart_required",
    "_runs_base",
    "_write_state_json",
    # From state_improvements
    "_count_blocked",
    "_count_checked",
    "_count_unchecked",
    "_detect_backlog_violation",
    "_extract_unchecked_lines",
    "_extract_unchecked_set",
    "_get_current_improvement",
    "_is_needs_package",
    "_parse_check_output",
]
