import subprocess
import time
import asyncio
import json
import sys
import re
import threading
from pathlib import Path

"""Backward-compat shim — real code lives in evolve.application and evolve.infrastructure.

Orchestrator migrated to the application layer (SPEC § DDD migration).
"""

from evolve.application.run_loop import _run_rounds
from evolve.application.run_round import _run_single_round_body, run_single_round
from evolve.application.run_loop_lifecycle import (
    _AttemptOutcome,
    _diagnose_attempt_outcome,
    _handle_round_success,
)
from evolve.application.diff import diff as run_diff
from evolve.application.dry_run import dry_run as run_dry_run
from evolve.application.sync_readme import sync_readme as run_sync_readme
from evolve.application.validate import validate as run_validate
from evolve.application.run_loop_startup import evolve_loop

from evolve.costs import TokenUsage, aggregate_usage, build_usage_state, estimate_cost, format_cost
from evolve.diagnostics import (
    MAX_IDENTICAL_FAILURES,
    _auto_detect_check,
    _check_review_verdict,
    _DEFAULT_README_STALE_THRESHOLD_DAYS,
    _detect_file_too_large,
    _detect_layering_violation,
    _detect_tdd_violation,
    _detect_us_format_violation,
    _emit_stale_readme_advisory,
    _failure_signature,
    _FILE_TOO_LARGE_LIMIT,
    _generate_evolution_report,
    _is_circuit_breaker_tripped,
    _README_STALE_ADVISORY_FMT,
    _save_subprocess_diagnostic,
)
from evolve.git import _ensure_git, _git_commit, _git_show_at, _setup_forever_branch
from evolve.hooks import fire_hook, load_hooks
from evolve.orchestrator_constants import (
    MAX_DEBUG_RETRIES,
    _BACKLOG_VIOLATION_HEADER,
    _BACKLOG_VIOLATION_PREFIX,
    _MEMORY_COMPACTION_MARKER,
    _MEMORY_WIPE_THRESHOLD,
)
from evolve.orchestrator_helpers import (
    _PROBE_OK_PREFIX,
    _PROBE_PREFIX,
    _PROBE_WARN_PREFIX,
    _enforce_convergence_backstop,
    _is_self_evolving,
    _parse_report_summary,
    _probe,
    _probe_ok,
    _probe_warn,
    _run_curation_pass,
    _run_spec_archival_pass,
    _scaffold_shared_runtime_files,
    _should_run_spec_archival,
)
from evolve.party import _forever_restart, _run_party_mode
from evolve.state import (
    _RunsLayoutError,
    _check_spec_freshness,
    _compute_backlog_stats,
    _count_blocked,
    _count_checked,
    _count_unchecked,
    _detect_backlog_violation,
    _detect_premature_converged,
    _ensure_runs_layout,
    _extract_unchecked_lines,
    _extract_unchecked_set,
    _get_current_improvement,
    _is_needs_package,
    _parse_check_output,
    _parse_restart_required,
    _runs_base,
    _write_state_json,
)
from evolve.subprocess_monitor import WATCHDOG_TIMEOUT, _run_monitored_subprocess
from evolve.tui import TUIProtocol, get_tui

__all__ = [
    "MAX_DEBUG_RETRIES",
    "MAX_IDENTICAL_FAILURES",
    "TUIProtocol",
    "TokenUsage",
    "WATCHDOG_TIMEOUT",
    "_AttemptOutcome",
    "_BACKLOG_VIOLATION_HEADER",
    "_BACKLOG_VIOLATION_PREFIX",
    "_DEFAULT_README_STALE_THRESHOLD_DAYS",
    "_FILE_TOO_LARGE_LIMIT",
    "_MEMORY_COMPACTION_MARKER",
    "_MEMORY_WIPE_THRESHOLD",
    "_PROBE_OK_PREFIX",
    "_PROBE_PREFIX",
    "_PROBE_WARN_PREFIX",
    "_README_STALE_ADVISORY_FMT",
    "_RunsLayoutError",
    "_auto_detect_check",
    "_check_review_verdict",
    "_check_spec_freshness",
    "_compute_backlog_stats",
    "_count_blocked",
    "_count_checked",
    "_count_unchecked",
    "_detect_backlog_violation",
    "_detect_file_too_large",
    "_detect_layering_violation",
    "_detect_premature_converged",
    "_detect_tdd_violation",
    "_detect_us_format_violation",
    "_diagnose_attempt_outcome",
    "_emit_stale_readme_advisory",
    "_enforce_convergence_backstop",
    "_ensure_git",
    "_ensure_runs_layout",
    "_extract_unchecked_lines",
    "_extract_unchecked_set",
    "_failure_signature",
    "_forever_restart",
    "_generate_evolution_report",
    "_get_current_improvement",
    "_git_commit",
    "_git_show_at",
    "_handle_round_success",
    "_is_circuit_breaker_tripped",
    "_is_needs_package",
    "_is_self_evolving",
    "_parse_check_output",
    "_parse_report_summary",
    "_parse_restart_required",
    "_probe",
    "_probe_ok",
    "_probe_warn",
    "_run_curation_pass",
    "_run_monitored_subprocess",
    "_run_party_mode",
    "_run_rounds",
    "_run_single_round_body",
    "_run_spec_archival_pass",
    "_runs_base",
    "_save_subprocess_diagnostic",
    "_scaffold_shared_runtime_files",
    "_setup_forever_branch",
    "_should_run_spec_archival",
    "_write_state_json",
    "aggregate_usage",
    "build_usage_state",
    "estimate_cost",
    "evolve_loop",
    "fire_hook",
    "format_cost",
    "get_tui",
    "load_hooks",
    "run_diff",
    "run_dry_run",
    "run_single_round",
    "run_sync_readme",
    "run_validate",
]
