"""Diagnostics helpers — backward-compat shim.

Real implementation lives in ``evolve.infrastructure.diagnostics.detector``.
This shim preserves existing ``from evolve.diagnostics import X`` call sites.
"""

from __future__ import annotations

# Re-export everything from the infrastructure layer
from evolve.infrastructure.diagnostics import (  # noqa: F401
    MAX_IDENTICAL_FAILURES,
    _auto_detect_check,
    _check_review_verdict,
    _DDD_ALLOWED,
    _DDD_LAYERS,
    _DEFAULT_README_STALE_THRESHOLD_DAYS,
    _detect_file_too_large,
    _detect_layering_violation,
    _detect_legacy_layout_violation,
    _detect_tdd_violation,
    _detect_us_format_violation,
    _emit_stale_readme_advisory,
    _failure_signature,
    _FILE_TOO_LARGE_LIMIT,
    _is_circuit_breaker_tripped,
    _README_STALE_ADVISORY_FMT,
    _save_subprocess_diagnostic,
    _US_HEADER_RE,
    _US_REQUIRED_SECTIONS,
)

# Re-exports from other modules (kept here for consolidated access)
from evolve.reporting import _generate_evolution_report  # noqa: F401
from evolve.state import (  # noqa: F401
    _detect_backlog_violation,
    _detect_premature_converged,
    _runs_base,
)
from evolve.tui import TUIProtocol  # noqa: F401

__all__ = [
    "MAX_IDENTICAL_FAILURES",
    "_auto_detect_check",
    "_check_review_verdict",
    "_DDD_ALLOWED",
    "_DDD_LAYERS",
    "_DEFAULT_README_STALE_THRESHOLD_DAYS",
    "_detect_backlog_violation",
    "_detect_file_too_large",
    "_detect_layering_violation",
    "_detect_legacy_layout_violation",
    "_detect_premature_converged",
    "_detect_tdd_violation",
    "_detect_us_format_violation",
    "_emit_stale_readme_advisory",
    "_failure_signature",
    "_FILE_TOO_LARGE_LIMIT",
    "_generate_evolution_report",
    "_is_circuit_breaker_tripped",
    "_README_STALE_ADVISORY_FMT",
    "_save_subprocess_diagnostic",
    "_US_HEADER_RE",
    "_US_REQUIRED_SECTIONS",
]
