"""Evolution loop orchestrator.

Each round runs as a separate subprocess so code changes are picked up immediately.
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

from evolve.costs import TokenUsage, aggregate_usage, build_usage_state, estimate_cost, format_cost
from evolve.diagnostics import (
    MAX_IDENTICAL_FAILURES,
    _auto_detect_check,
    _check_review_verdict,
    _DEFAULT_README_STALE_THRESHOLD_DAYS,
    _detect_file_too_large,
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


# Maximum number of debug retries when a round fails, stalls, or makes no progress.
MAX_DEBUG_RETRIES = 2

# Memory-wipe sanity gate constants — keep the runtime check aligned with
# SPEC.md § "memory.md" — "Byte-size sanity gate".  Changing either value
# here is the single source of truth for both the detection logic in
# _run_rounds and the tests that exercise it.
#
#   _MEMORY_COMPACTION_MARKER — the literal string the agent must include
#       in its commit message (on its own line, per SPEC) to legitimise a
#       large memory.md shrink.  Absence of the marker on a >threshold
#       shrink triggers a debug retry with the "silently wiped memory.md"
#       diagnostic header.
#   _MEMORY_WIPE_THRESHOLD   — fractional shrink floor below which memory.md
#       is considered wiped.  0.5 means "memory.md after the round is
#       smaller than half of its pre-round size" → retry.
_MEMORY_COMPACTION_MARKER = "memory: compaction"
_MEMORY_WIPE_THRESHOLD = 0.5

# Backlog discipline rule 1 (empty-queue gate) constants — keep the runtime
# check aligned with SPEC.md § "Backlog discipline".  The agent is forbidden
# from adding a new `- [ ]` item while any other `- [ ]` item already exists
# in improvements.md.  When detected, the orchestrator triggers a debug retry
# whose diagnostic prefix carries the documented header so agent.py's prompt
# builder can render the dedicated section.
_BACKLOG_VIOLATION_PREFIX = "BACKLOG VIOLATION"
_BACKLOG_VIOLATION_HEADER = (
    "CRITICAL \u2014 Backlog discipline violation: "
    "new item added while queue non-empty"
)


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
    """Run evolution rounds from start_round to max_rounds.

    Each round is launched as a subprocess via ``_run_monitored_subprocess``.
    Failed rounds are retried up to ``MAX_DEBUG_RETRIES`` times.  On
    convergence the session exits (or restarts in forever mode).

    Args:
        project_dir: Root directory of the project.
        run_dir: Session directory for round artifacts.
        improvements_path: Path to the improvements.md file.
        ui: TUI instance for status output.
        start_round: First round number to execute.
        max_rounds: Maximum round number (inclusive).
        check_cmd: Shell command to verify the project.
        allow_installs: If True, allow improvements requiring new packages.
        timeout: Timeout for the check command in seconds.
        model: Claude model identifier.
        forever: If True, restart after convergence instead of exiting.
        hooks: Event hook configuration dict (from ``load_hooks``).
        spec: Path to the spec file relative to project_dir (default: README.md).
        capture_frames: If True, capture TUI frames as PNG at key moments.
        max_cost: Budget cap in USD. Session pauses when exceeded.
    """
    if hooks is None:
        hooks = {}
    _rounds_start_time = time.monotonic()
    _started_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    _probe(f"_run_rounds starting from round {start_round} to {max_rounds}")
    # Circuit-breaker state: rolling list of failure fingerprints from rounds
    # whose retries all exhausted.  Cleared on every successful round so that
    # a single recovery resets the counter.  See SPEC § "Circuit breakers".
    _failure_signatures: list[str] = []
    while True:
        for round_num in range(start_round, max_rounds + 1):
            # Phase 2 — Spec freshness gate: if spec is newer than
            # improvements.md, mark unchecked items as stale so the agent
            # rebuilds the backlog from the updated spec.
            spec_fresh = _check_spec_freshness(project_dir, improvements_path, spec=spec)
            if not spec_fresh:
                _probe(f"spec freshness gate: spec is newer than improvements.md — backlog marked stale")

            current = _get_current_improvement(improvements_path, allow_installs=allow_installs)
            checked = _count_checked(improvements_path)
            unchecked = _count_unchecked(improvements_path)
            _probe(f"round {round_num}/{max_rounds} — checked={checked}, unchecked={unchecked}, target={current or '(none)'}")

            # Compute session cost so far (from completed rounds) for TUI header
            _header_cost: float | None = None
            if round_num > 1:
                _, _header_cost, _ = aggregate_usage(run_dir, round_num - 1)

            if current:
                ui.round_header(round_num, max_rounds, target=current,
                                checked=checked, total=checked + unchecked,
                                estimated_cost_usd=_header_cost)
            elif unchecked > 0:
                # All remaining unchecked items are blocked (needs-package without --allow-installs)
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

            # Fire on_round_start hook
            session_name = run_dir.name
            fire_hook(hooks, "on_round_start", session=session_name, round_num=round_num, status="running")

            # Launch round as subprocess — picks up code changes from previous round.
            # Use ``python -m evolve`` so this works regardless of whether the
            # project is laid out as a flat module (legacy) or a package (current).
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

            # Snapshot git HEAD and improvements.md at ROUND start so
            # later attempts can tell whether EARLIER attempts in the
            # same round already produced real progress — in which
            # case a subsequent attempt that finds "nothing to do"
            # is NOT a failure, it's the round wrapping up.
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

            # --- Debug retry loop: run the round, diagnose failures, retry ---
            _probe(f"launching subprocess for round {round_num}")
            round_succeeded = False

            def _register_and_check_circuit(sig: str) -> None:
                """Record a failed-attempt signature and exit 4 if the last
                ``MAX_IDENTICAL_FAILURES`` in a row all match — SPEC.md §
                "Circuit breakers".  Per-attempt registration catches
                deterministic within-round loops (e.g. pytest hanging
                identically on every debug retry) at their first
                observable repetition rather than after N failed rounds.
                """
                _failure_signatures.append(sig)
                if _is_circuit_breaker_tripped(_failure_signatures):
                    ui.error(
                        f"Same failure signature {MAX_IDENTICAL_FAILURES} "
                        f"attempts in a row (sig={_failure_signatures[-1]}) "
                        "— deterministic loop detected, exiting for "
                        "supervisor restart"
                    )
                    sys.exit(4)

            for attempt in range(1, MAX_DEBUG_RETRIES + 2):  # 1..MAX_DEBUG_RETRIES+1
                # Surface the retry reason before re-launching the subprocess
                # so operators understand why a second "[probe] round N
                # starting" line appears without a fresh round_header block
                # (the header is only printed once per round in _run_rounds,
                # while retries happen inside this inner loop).  Source the
                # reason from the diagnostic file written by the previous
                # attempt — every retry-triggering branch calls
                # _save_subprocess_diagnostic with a human-readable reason,
                # so this single read covers all retry paths.
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

                # Snapshot conversation log size before subprocess so we can detect new output
                convo = run_dir / f"conversation_loop_{round_num}.md"
                convo_size_before = convo.stat().st_size if convo.is_file() else 0

                # Snapshot improvements.md bytes before subprocess for zero-progress detection
                imp_snapshot_before = improvements_path.read_bytes() if improvements_path.is_file() else b""

                # Snapshot memory.md byte size before subprocess for the
                # memory-wipe sanity gate.  If the agent shrinks memory.md
                # by more than 50% in a single round without explicitly
                # declaring `memory: compaction` in its commit message, we
                # treat it as a silent wipe and trigger a debug retry
                # (same family as zero-progress detection).  See
                # SPEC.md § "memory.md" — "Byte-size sanity gate".
                memory_path = _runs_base(project_dir) / "memory.md"
                mem_size_before = memory_path.stat().st_size if memory_path.is_file() else 0

                # Snapshot git HEAD before the attempt so the
                # "fallback commit message" detection below can
                # distinguish "THIS attempt used the fallback" (bug
                # worth flagging) from "a PRIOR attempt used the
                # fallback and this attempt just found nothing new
                # to commit" (legitimate — the work was done earlier).
                # Previously the no_commit_msg check inspected HEAD
                # globally and false-positive'd on any attempt that
                # landed on a stale fallback commit, which exhausted
                # retries and exited the round even when the agent
                # had already done substantive work in an earlier
                # attempt.
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

                # --- Diagnose subprocess outcome ---
                # US-040: extracted to evolve.round_lifecycle.
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

                # Register this attempt's signature with the circuit
                # breaker — fires sys.exit(4) when the last
                # ``MAX_IDENTICAL_FAILURES`` attempts share one
                # fingerprint.  Running this after the diagnostic save
                # means the final attempt's paper trail is on disk
                # before we exit.
                if _attempt_sig is not None:
                    _register_and_check_circuit(_attempt_sig)

                # Capture error frame before retry
                ui.capture_frame(f"error_round_{round_num}")

                # Fire on_error hook for failed round
                fire_hook(hooks, "on_error", session=session_name, round_num=round_num, status="error")

                # If retries remain, inform and loop
                if attempt <= MAX_DEBUG_RETRIES:
                    ui.warn(
                        f"Debug retry {attempt}/{MAX_DEBUG_RETRIES} for round {round_num} "
                        "— re-running with diagnostic context"
                    )
                else:
                    # All retries exhausted.  The per-attempt circuit
                    # breaker above already fired sys.exit(4) when the
                    # three attempts shared a signature, so reaching
                    # this branch means the failures were heterogeneous
                    # — a classic "retries exhausted with mixed
                    # diagnostics" case, which stays exit 2 (non-
                    # forever) or skip-to-next-round (forever).
                    ui.no_progress()
                    if not forever:
                        sys.exit(2)
                    ui.warn(
                        f"Round {round_num} failed after {MAX_DEBUG_RETRIES + 1} attempts "
                        "— skipping to next round"
                    )
                    break

            if not round_succeeded:
                continue  # skip convergence check, move to next round

            # Round succeeded — delegated to evolve.round_lifecycle
            # (US-040).  Returns ``(new_run_dir, new_ui, new_start_round)``
            # for the forever-restart case; ``None`` otherwise.  May
            # ``sys.exit()`` for budget reached (1), structural change (3),
            # or normal convergence (0).
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
            # for loop completed without break — max rounds reached
            unchecked = _count_unchecked(improvements_path)
            checked = _count_checked(improvements_path)
            _probe_warn(f"max rounds reached ({max_rounds}) — checked={checked}, unchecked={unchecked}")

            # Aggregate final usage for max_rounds state
            _mr_total, _mr_cost, _mr_rounds = aggregate_usage(
                run_dir, max_rounds
            )
            _mr_usage = build_usage_state(_mr_total, _mr_cost, _mr_rounds)

            # Update state.json to max_rounds
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

            # Generate evolution report
            _generate_evolution_report(project_dir, run_dir, max_rounds, max_rounds, converged=False, capture_frames=capture_frames)

            # Display completion summary panel
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


# US-038: run_single_round + _run_single_round_body extracted to
# evolve/round_runner.py.  Re-export keeps patch surfaces intact —
# tests/test_round_runner_module.py asserts is-identity.
from evolve.round_runner import _run_single_round_body, run_single_round  # noqa: E402

# US-040: _diagnose_attempt_outcome + _handle_round_success extracted
# to evolve/round_lifecycle.py to keep this module under the SPEC
# § "Hard rule: source files MUST NOT exceed 500 lines" cap.  The
# helpers lazy-import their orchestrator-resident dependencies
# (``_save_subprocess_diagnostic`` etc.) via ``from evolve.orchestrator
# import ...`` inside their bodies, preserving ``patch("evolve.
# orchestrator.X")`` test surfaces.  Tests/test_round_lifecycle_module.py
# asserts ``is``-identity for both symbols.
from evolve.round_lifecycle import (  # noqa: E402
    _AttemptOutcome,
    _diagnose_attempt_outcome,
    _handle_round_success,
)


# Re-exports for backward compatibility — the four one-shot orchestrator
# entry points were extracted to ``evolve/orchestrator_oneshots.py`` (US-036)
# to satisfy the SPEC § "Hard rule: source files MUST NOT exceed 500 lines"
# cap.  Tests and ``evolve/cli.py`` continue to ``from evolve.orchestrator
# import run_dry_run`` (etc.) and to ``patch("evolve.orchestrator.run_X")``
# via this re-export — ``is``-identical bindings are guaranteed by
# ``tests/test_orchestrator_oneshots_module.py``.
from evolve.orchestrator_oneshots import (  # noqa: E402
    run_diff,
    run_dry_run,
    run_sync_readme,
    run_validate,
)

# US-042: ``evolve_loop`` extracted to evolve/orchestrator_startup.py
# (verified by tests/test_orchestrator_startup_module.py).
from evolve.orchestrator_startup import evolve_loop  # noqa: E402

# _run_party_mode and _forever_restart extracted to evolve/party.py
# (re-exported via ``from evolve.party import …`` above).

