"""evolve.infrastructure.filesystem — run-dir, state.json, conversation logs."""

from evolve.infrastructure.filesystem.state_manager import (
    _RunsLayoutError,
    _check_spec_freshness,
    _compute_backlog_stats,
    _detect_premature_converged,
    _ensure_runs_layout,
    _parse_restart_required,
    _runs_base,
    _write_state_json,
)

__all__ = [
    "_RunsLayoutError",
    "_check_spec_freshness",
    "_compute_backlog_stats",
    "_detect_premature_converged",
    "_ensure_runs_layout",
    "_parse_restart_required",
    "_runs_base",
    "_write_state_json",
]
