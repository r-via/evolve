"""Backward-compat shim — real code lives in evolve.application.

Orchestrator helpers migrated to the application layer (SPEC § DDD migration).
"""

from evolve.application.run_loop_helpers import (
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

__all__ = [
    "_PROBE_OK_PREFIX",
    "_PROBE_PREFIX",
    "_PROBE_WARN_PREFIX",
    "_enforce_convergence_backstop",
    "_is_self_evolving",
    "_parse_report_summary",
    "_probe",
    "_probe_ok",
    "_probe_warn",
    "_run_curation_pass",
    "_run_spec_archival_pass",
    "_scaffold_shared_runtime_files",
    "_should_run_spec_archival",
]
