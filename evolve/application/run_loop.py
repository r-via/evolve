"""Use case: run N evolution rounds (the session loop).

Application layer — orchestration bounded context.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

__mod = __import__("evolve.costs", fromlist=["TokenUsage", "aggregate_usage", "build_usage_state", "estimate_cost", "format_cost"])
TokenUsage = __mod.TokenUsage
aggregate_usage = __mod.aggregate_usage
build_usage_state = __mod.build_usage_state
estimate_cost = __mod.estimate_cost
format_cost = __mod.format_cost
__mod = __import__("evolve.diagnostics", fromlist=["MAX_IDENTICAL_FAILURES", "_auto_detect_check", "_check_review_verdict", "_DEFAULT_README_STALE_THRESHOLD_DAYS", "_detect_file_too_large", "_detect_layering_violation", "_detect_tdd_violation", "_detect_us_format_violation", "_emit_stale_readme_advisory", "_failure_signature", "_FILE_TOO_LARGE_LIMIT", "_generate_evolution_report", "_is_circuit_breaker_tripped", "_README_STALE_ADVISORY_FMT", "_save_subprocess_diagnostic"])
MAX_IDENTICAL_FAILURES = __mod.MAX_IDENTICAL_FAILURES
_auto_detect_check = __mod._auto_detect_check
_check_review_verdict = __mod._check_review_verdict
_DEFAULT_README_STALE_THRESHOLD_DAYS = __mod._DEFAULT_README_STALE_THRESHOLD_DAYS
_detect_file_too_large = __mod._detect_file_too_large
_detect_layering_violation = __mod._detect_layering_violation
_detect_tdd_violation = __mod._detect_tdd_violation
_detect_us_format_violation = __mod._detect_us_format_violation
_emit_stale_readme_advisory = __mod._emit_stale_readme_advisory
_failure_signature = __mod._failure_signature
_FILE_TOO_LARGE_LIMIT = __mod._FILE_TOO_LARGE_LIMIT
_generate_evolution_report = __mod._generate_evolution_report
_is_circuit_breaker_tripped = __mod._is_circuit_breaker_tripped
_README_STALE_ADVISORY_FMT = __mod._README_STALE_ADVISORY_FMT
_save_subprocess_diagnostic = __mod._save_subprocess_diagnostic
__mod = __import__("evolve.git", fromlist=["_ensure_git", "_git_commit", "_git_show_at", "_setup_forever_branch"])
_ensure_git = __mod._ensure_git
_git_commit = __mod._git_commit
_git_show_at = __mod._git_show_at
_setup_forever_branch = __mod._setup_forever_branch
__mod = __import__("evolve.hooks", fromlist=["fire_hook", "load_hooks"])
fire_hook = __mod.fire_hook
load_hooks = __mod.load_hooks
__mod = __import__("evolve.orchestrator_constants", fromlist=["MAX_DEBUG_RETRIES", "_BACKLOG_VIOLATION_HEADER", "_BACKLOG_VIOLATION_PREFIX", "_MEMORY_COMPACTION_MARKER", "_MEMORY_WIPE_THRESHOLD"])
MAX_DEBUG_RETRIES = __mod.MAX_DEBUG_RETRIES
_BACKLOG_VIOLATION_HEADER = __mod._BACKLOG_VIOLATION_HEADER
_BACKLOG_VIOLATION_PREFIX = __mod._BACKLOG_VIOLATION_PREFIX
_MEMORY_COMPACTION_MARKER = __mod._MEMORY_COMPACTION_MARKER
_MEMORY_WIPE_THRESHOLD = __mod._MEMORY_WIPE_THRESHOLD
__mod = __import__("evolve.orchestrator_helpers", fromlist=["_PROBE_OK_PREFIX", "_PROBE_PREFIX", "_PROBE_WARN_PREFIX", "_enforce_convergence_backstop", "_is_self_evolving", "_parse_report_summary", "_probe", "_probe_ok", "_probe_warn", "_run_curation_pass", "_run_spec_archival_pass", "_scaffold_shared_runtime_files", "_should_run_spec_archival"])
_PROBE_OK_PREFIX = __mod._PROBE_OK_PREFIX
_PROBE_PREFIX = __mod._PROBE_PREFIX
_PROBE_WARN_PREFIX = __mod._PROBE_WARN_PREFIX
_enforce_convergence_backstop = __mod._enforce_convergence_backstop
_is_self_evolving = __mod._is_self_evolving
_parse_report_summary = __mod._parse_report_summary
_probe = __mod._probe
_probe_ok = __mod._probe_ok
_probe_warn = __mod._probe_warn
_run_curation_pass = __mod._run_curation_pass
_run_spec_archival_pass = __mod._run_spec_archival_pass
_scaffold_shared_runtime_files = __mod._scaffold_shared_runtime_files
_should_run_spec_archival = __mod._should_run_spec_archival
__mod = __import__("evolve.party", fromlist=["_forever_restart", "_run_party_mode"])
_forever_restart = __mod._forever_restart
_run_party_mode = __mod._run_party_mode
__mod = __import__("evolve.state", fromlist=["_RunsLayoutError", "_check_spec_freshness", "_compute_backlog_stats", "_count_blocked", "_count_checked", "_count_unchecked", "_detect_backlog_violation", "_detect_premature_converged", "_ensure_runs_layout", "_extract_unchecked_lines", "_extract_unchecked_set", "_get_current_improvement", "_is_needs_package", "_parse_check_output", "_parse_restart_required", "_runs_base", "_write_state_json"])
_RunsLayoutError = __mod._RunsLayoutError
_check_spec_freshness = __mod._check_spec_freshness
_compute_backlog_stats = __mod._compute_backlog_stats
_count_blocked = __mod._count_blocked
_count_checked = __mod._count_checked
_count_unchecked = __mod._count_unchecked
_detect_backlog_violation = __mod._detect_backlog_violation
_detect_premature_converged = __mod._detect_premature_converged
_ensure_runs_layout = __mod._ensure_runs_layout
_extract_unchecked_lines = __mod._extract_unchecked_lines
_extract_unchecked_set = __mod._extract_unchecked_set
_get_current_improvement = __mod._get_current_improvement
_is_needs_package = __mod._is_needs_package
_parse_check_output = __mod._parse_check_output
_parse_restart_required = __mod._parse_restart_required
_runs_base = __mod._runs_base
_write_state_json = __mod._write_state_json
__mod = __import__("evolve.subprocess_monitor", fromlist=["WATCHDOG_TIMEOUT", "_run_monitored_subprocess"])
WATCHDOG_TIMEOUT = __mod.WATCHDOG_TIMEOUT
_run_monitored_subprocess = __mod._run_monitored_subprocess
__mod = __import__("evolve.tui", fromlist=["TUIProtocol", "get_tui"])
TUIProtocol = __mod.TUIProtocol
get_tui = __mod.get_tui

# Re-exports for backward-compat via shim
__mod = __import__("evolve.round_runner", fromlist=["_run_single_round_body", "run_single_round"])
_run_single_round_body = __mod._run_single_round_body
run_single_round = __mod.run_single_round
__mod = __import__("evolve.round_lifecycle", fromlist=["_AttemptOutcome", "_diagnose_attempt_outcome", "_handle_round_success"])
_AttemptOutcome = __mod._AttemptOutcome
_diagnose_attempt_outcome = __mod._diagnose_attempt_outcome
_handle_round_success = __mod._handle_round_success
__mod = __import__("evolve.orchestrator_oneshots", fromlist=["run_diff", "run_dry_run", "run_sync_readme", "run_validate"])
run_diff = __mod.run_diff
run_dry_run = __mod.run_dry_run
run_sync_readme = __mod.run_sync_readme
run_validate = __mod.run_validate
__mod = __import__("evolve.orchestrator_startup", fromlist=["evolve_loop"])
evolve_loop = __mod.evolve_loop


def _run_rounds(
    project_dir: Path,
    run_dir: Path,
    improvements_path: Path,
    ui: TUIProtocol,
    start_round: int,
    max_rounds: int,
    check_cmd: str | None,
    allow_installs: bool,
    timeout: int,
    model: str,
    forever: bool = False,
    hooks: dict[str, str] | None = None,
    spec: str | None = None,
    capture_frames: bool = False,
    effort: str | None = "medium",
    max_cost: float | None = None,
) -> None:
    """Run evolution rounds from start_round to max_rounds."""
    if hooks is None:
        hooks = {}
    _rounds_start_time = time.monotonic()
    _started_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    _probe(f"_run_rounds starting from round {start_round} to {max_rounds}")
    _failure_signatures: list[str] = []
    while True:
        for round_num in range(start_round, max_rounds + 1):
            spec_fresh = _check_spec_freshness(project_dir, improvements_path, spec=spec)
            if not spec_fresh:
                _probe(f"spec freshness gate: spec is newer than improvements.md — backlog marked stale")

            current = _get_current_improvement(improvements_path, allow_installs=allow_installs)
            checked = _count_checked(improvements_path)
            unchecked = _count_unchecked(improvements_path)
            _probe(f"round {round_num}/{max_rounds} — checked={checked}, unchecked={unchecked}, target={current or '(none)'}")

            _header_cost: float | None = None
            if round_num > 1:
                _, _header_cost, _ = aggregate_usage(run_dir, round_num - 1)

            if current:
                ui.round_header(round_num, max_rounds, target=current,
                                checked=checked, total=checked + unchecked,
                                estimated_cost_usd=_header_cost)
            elif unchecked > 0:
                blocked = _count_blocked(improvements_path)
                if blocked == unchecked:
                    ui.round_header(round_num, max_rounds,
                                    estimated_cost_usd=_header_cost)
                    ui.blocked_message(blocked)
                    sys.exit(1)
                ui.round_header(round_num, max_rounds, target="(initial analysis)",
                                estimated_cost_usd=_header_cost)
            else:
                ui.round_header(round_num, max_rounds, target="(initial analysis)",
                                estimated_cost_usd=_header_cost)

            session_name = run_dir.name
            fire_hook(hooks, "on_round_start", session=session_name, round_num=round_num, status="running")

            cmd = [
                sys.executable, "-m", "evolve",
                "_round",
                str(project_dir),
                "--round-num", str(round_num),
                "--timeout", str(timeout),
                "--run-dir", str(run_dir),
                "--model", model,
            ]
            if check_cmd:
                cmd += ["--check", check_cmd]
            if allow_installs:
                cmd += ["--allow-installs"]
            if spec:
                cmd += ["--spec", spec]
            if effort:
                cmd += ["--effort", effort]

            try:
                _r0 = subprocess.run(
                    ["git", "rev-parse", "HEAD"],
                    cwd=str(project_dir), capture_output=True,
                    text=True, timeout=5,
                )
                round_start_head_sha = (
                    _r0.stdout.strip() if _r0.returncode == 0 else ""
                )
            except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
                round_start_head_sha = ""
            round_start_imp = (
                improvements_path.read_bytes()
                if improvements_path.is_file() else b""
            )

            _probe(f"launching subprocess for round {round_num}")
            round_succeeded = False

            def _register_and_check_circuit(sig: str) -> None:
                _failure_signatures.append(sig)
                if _is_circuit_breaker_tripped(_failure_signatures):
                    ui.error(
                        f"Same failure signature {MAX_IDENTICAL_FAILURES} "
                        f"attempts in a row (sig={_failure_signatures[-1]}) "
                        "— deterministic loop detected, exiting for "
                        "supervisor restart"
                    )
                    sys.exit(4)

            for attempt in range(1, MAX_DEBUG_RETRIES + 2):
                if attempt > 1:
                    diag_path = run_dir / f"subprocess_error_round_{round_num}.txt"
                    retry_reason = ""
                    if diag_path.is_file():
                        try:
                            first_line = diag_path.read_text().splitlines()[0]
                            m = re.match(
                                r"^Round \d+ — (.+) \(attempt \d+\)$",
                                first_line,
                            )
                            if m:
                                retry_reason = m.group(1)
                        except (OSError, IndexError):
                            pass
                    _probe_warn(
                        f"round {round_num} retry "
                        f"{attempt}/{MAX_DEBUG_RETRIES + 1}"
                        + (f" — reason={retry_reason}" if retry_reason else "")
                    )

                convo = run_dir / f"conversation_loop_{round_num}.md"
                convo_size_before = convo.stat().st_size if convo.is_file() else 0
                imp_snapshot_before = improvements_path.read_bytes() if improvements_path.is_file() else b""
                memory_path = _runs_base(project_dir) / "memory.md"
                mem_size_before = memory_path.stat().st_size if memory_path.is_file() else 0

                try:
                    _head_before = subprocess.run(
                        ["git", "rev-parse", "HEAD"],
                        cwd=str(project_dir), capture_output=True,
                        text=True, timeout=5,
                    )
                    head_sha_before = (
                        _head_before.stdout.strip()
                        if _head_before.returncode == 0 else ""
                    )
                except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
                    head_sha_before = ""

                returncode, output, stalled = _run_monitored_subprocess(
                    cmd, str(project_dir), ui, round_num,
                )

                _outcome = _diagnose_attempt_outcome(
                    run_dir=run_dir,
                    round_num=round_num,
                    project_dir=project_dir,
                    improvements_path=improvements_path,
                    cmd=cmd,
                    output=output,
                    stalled=stalled,
                    returncode=returncode,
                    attempt=attempt,
                    checked=checked,
                    unchecked=unchecked,
                    imp_snapshot_before=imp_snapshot_before,
                    mem_size_before=mem_size_before,
                    head_sha_before=head_sha_before,
                    convo_size_before=convo_size_before,
                    round_start_head_sha=round_start_head_sha,
                    round_start_imp=round_start_imp,
                    ui=ui,
                    hooks=hooks,
                    session_name=session_name,
                    failure_signatures=_failure_signatures,
                )
                checked = _outcome.checked
                unchecked = _outcome.unchecked
                if _outcome.review_retry_circuit_tripped:
                    return
                if _outcome.round_succeeded:
                    round_succeeded = True
                    break
                if _outcome.is_review_retry:
                    continue
                _attempt_sig = _outcome.attempt_sig

                if _attempt_sig is not None:
                    _register_and_check_circuit(_attempt_sig)

                ui.capture_frame(f"error_round_{round_num}")
                fire_hook(hooks, "on_error", session=session_name, round_num=round_num, status="error")

                if attempt <= MAX_DEBUG_RETRIES:
                    ui.warn(
                        f"Debug retry {attempt}/{MAX_DEBUG_RETRIES} for round {round_num} "
                        "— re-running with diagnostic context"
                    )
                else:
                    ui.no_progress()
                    if not forever:
                        sys.exit(2)
                    ui.warn(
                        f"Round {round_num} failed after {MAX_DEBUG_RETRIES + 1} attempts "
                        "— skipping to next round"
                    )
                    break

            if not round_succeeded:
                continue

            _success_result = _handle_round_success(
                project_dir=project_dir,
                run_dir=run_dir,
                improvements_path=improvements_path,
                ui=ui,
                hooks=hooks,
                session_name=session_name,
                round_num=round_num,
                max_rounds=max_rounds,
                started_at=_started_at,
                rounds_start_time=_rounds_start_time,
                cmd=cmd,
                output=output,
                attempt=attempt,
                spec=spec,
                capture_frames=capture_frames,
                max_cost=max_cost,
                forever=forever,
                failure_signatures=_failure_signatures,
            )
            if _success_result is not None:
                run_dir, ui, start_round = _success_result
                break
        else:
            unchecked = _count_unchecked(improvements_path)
            checked = _count_checked(improvements_path)
            _probe_warn(f"max rounds reached ({max_rounds}) — checked={checked}, unchecked={unchecked}")

            _mr_total, _mr_cost, _mr_rounds = aggregate_usage(
                run_dir, max_rounds
            )
            _mr_usage = build_usage_state(_mr_total, _mr_cost, _mr_rounds)

            _write_state_json(
                run_dir=run_dir,
                project_dir=project_dir,
                round_num=max_rounds,
                max_rounds=max_rounds,
                phase="improvement",
                status="max_rounds",
                improvements_path=improvements_path,
                started_at=_started_at,
                usage=_mr_usage,
            )

            _generate_evolution_report(project_dir, run_dir, max_rounds, max_rounds, converged=False, capture_frames=capture_frames)

            duration_s = time.monotonic() - _rounds_start_time
            summary_stats = _parse_report_summary(run_dir)
            ui.completion_summary(
                status="MAX_ROUNDS",
                round_num=max_rounds,
                duration_s=duration_s,
                improvements=summary_stats["improvements"],
                bugs_fixed=summary_stats["bugs_fixed"],
                tests_passing=summary_stats["tests_passing"],
                report_path=str(run_dir / "evolution_report.md"),
                estimated_cost_usd=_mr_cost,
            )

            ui.max_rounds(max_rounds, checked, unchecked)
            sys.exit(1)
