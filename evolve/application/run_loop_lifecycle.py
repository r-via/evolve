"""Round lifecycle — attempt-outcome diagnosis and round-success handling.

Application layer — orchestration bounded context.
"""

from __future__ import annotations

import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from evolve.interfaces.tui import TUIProtocol


@dataclass
class _AttemptOutcome:
    attempt_sig: str | None
    checked: int
    unchecked: int
    round_succeeded: bool
    is_review_retry: bool = False
    review_retry_circuit_tripped: bool = False


def _diagnose_attempt_outcome(
    *, run_dir: Path, round_num: int, project_dir: Path,
    improvements_path: Path, cmd: list[str], output: str,
    stalled: bool, returncode: int, attempt: int,
    checked: int, unchecked: int, imp_snapshot_before: bytes,
    mem_size_before: int, head_sha_before: str,
    convo_size_before: int, round_start_head_sha: str,
    round_start_imp: bytes, ui: TUIProtocol,
    hooks: dict[str, str], session_name: str,
    failure_signatures: list[str],
) -> _AttemptOutcome:
    """Diagnose a single round attempt's outcome."""
    from evolve.application.run_loop import (
        MAX_IDENTICAL_FAILURES,
        WATCHDOG_TIMEOUT,
        _BACKLOG_VIOLATION_PREFIX,
        _MEMORY_COMPACTION_MARKER,
        _MEMORY_WIPE_THRESHOLD,
        _check_review_verdict,
        _failure_signature,
        _is_circuit_breaker_tripped,
        _probe_warn,
        _runs_base,
        _save_subprocess_diagnostic,
        fire_hook,
    )
    from evolve.infrastructure.filesystem.improvement_parser import (
        _count_checked,
        _count_unchecked,
        _detect_backlog_violation,
    )
    from evolve.infrastructure.diagnostics.detector import _detect_us_format_violation

    if stalled:
        ui.round_failed(round_num, returncode)
        _save_subprocess_diagnostic(
            run_dir, round_num, cmd, output,
            reason=f"stalled ({WATCHDOG_TIMEOUT}s without output, killed)",
            attempt=attempt,
        )
        return _AttemptOutcome(
            attempt_sig=_failure_signature("stalled", returncode, output),
            checked=checked, unchecked=unchecked, round_succeeded=False,
        )
    if returncode != 0:
        ui.round_failed(round_num, returncode)
        _save_subprocess_diagnostic(
            run_dir, round_num, cmd, output,
            reason=f"crashed (exit code {returncode})",
            attempt=attempt,
        )
        return _AttemptOutcome(
            attempt_sig=_failure_signature("crashed", returncode, output),
            checked=checked, unchecked=unchecked, round_succeeded=False,
        )

    prev_checked = checked
    prev_unchecked = unchecked
    unchecked = _count_unchecked(improvements_path)
    checked = _count_checked(improvements_path)
    ui.progress_summary(checked, unchecked)

    review_verdict, review_findings = _check_review_verdict(run_dir, round_num)
    if review_verdict in ("BLOCKED", "CHANGES REQUESTED"):
        if review_verdict == "BLOCKED":
            reason = (
                f"REVIEW: blocked — adversarial review found "
                f"3+ HIGH findings or a [regression-risk] tag. "
                f"Auto-fixing on next attempt.\n"
                f"{review_findings}"
            )
        else:
            reason = (
                f"REVIEW: changes requested — adversarial review "
                f"found HIGH/MEDIUM findings that must be addressed.\n"
                f"{review_findings}"
            )
        _save_subprocess_diagnostic(
            run_dir, round_num, cmd, output,
            reason=reason, attempt=attempt,
        )
        fire_hook(
            hooks, "on_error",
            session=session_name, round_num=round_num,
            status=(
                "review_blocked"
                if review_verdict == "BLOCKED"
                else "review_changes_requested"
            ),
        )
        sig = _failure_signature(
            "no-progress:REVIEW", returncode, review_findings,
        )
        failure_signatures.append(sig)
        if _is_circuit_breaker_tripped(failure_signatures):
            ui.warn(
                f"Deterministic failure loop detected after "
                f"{MAX_IDENTICAL_FAILURES} identical review failures."
            )
            return _AttemptOutcome(
                attempt_sig=sig, checked=checked, unchecked=unchecked,
                round_succeeded=False,
                review_retry_circuit_tripped=True,
            )
        return _AttemptOutcome(
            attempt_sig=sig, checked=checked, unchecked=unchecked,
            round_succeeded=False, is_review_retry=True,
        )

    _subtype_path = run_dir / f"agent_subtype_round_{round_num}.txt"
    _agent_subtype: str | None = None
    if _subtype_path.is_file():
        _agent_subtype = _subtype_path.read_text().strip() or None

    imp_after = (
        improvements_path.read_bytes() if improvements_path.is_file() else b""
    )
    imp_unchanged = (imp_after == imp_snapshot_before)

    no_commit_msg = False
    try:
        head_after_res = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(project_dir), capture_output=True,
            text=True, timeout=5,
        )
        head_sha_after = (
            head_after_res.stdout.strip()
            if head_after_res.returncode == 0 else ""
        )
        head_moved = (
            bool(head_sha_before)
            and bool(head_sha_after)
            and head_sha_before != head_sha_after
        )
        if head_moved:
            git_log_result = subprocess.run(
                ["git", "log", "-1", "--format=%s"],
                cwd=str(project_dir), capture_output=True, text=True,
                timeout=10,
            )
            if git_log_result.returncode == 0:
                last_commit_msg = git_log_result.stdout.strip()
                if last_commit_msg == f"chore(evolve): round {round_num}":
                    no_commit_msg = True
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass

    memory_path = _runs_base(project_dir) / "memory.md"
    mem_size_after = (
        memory_path.stat().st_size if memory_path.is_file() else 0
    )
    memory_wiped = False
    if (
        mem_size_before > 0
        and mem_size_after < mem_size_before * _MEMORY_WIPE_THRESHOLD
    ):
        commit_body = ""
        try:
            git_body_result = subprocess.run(
                ["git", "log", "-1", "--format=%B"],
                cwd=str(project_dir), capture_output=True, text=True,
                timeout=10,
            )
            if git_body_result.returncode == 0:
                commit_body = git_body_result.stdout
        except (subprocess.TimeoutExpired, FileNotFoundError):
            commit_body = ""
        if _MEMORY_COMPACTION_MARKER not in commit_body:
            memory_wiped = True

    backlog_violated = False
    backlog_new_items: list[str] = []
    if not imp_unchanged:
        try:
            pre_text = imp_snapshot_before.decode("utf-8", errors="replace")
            post_text = imp_after.decode("utf-8", errors="replace")
            backlog_violated, backlog_new_items = (
                _detect_backlog_violation(pre_text, post_text)
            )
        except Exception as e:
            _probe_warn(f"backlog-violation check skipped: {e}")

    us_format_violations: list[str] = []
    if not imp_unchanged:
        try:
            pre_lines = imp_snapshot_before.decode(
                "utf-8", errors="replace"
            ).splitlines()
            us_format_violations = _detect_us_format_violation(
                improvements_path, pre_lines
            )
        except Exception as e:
            _probe_warn(f"US format validation skipped: {e}")

    scope_creep = False
    scope_creep_other_files: list[str] = []
    if backlog_new_items:
        try:
            diff_files = subprocess.run(
                ["git", "diff-tree", "--no-commit-id",
                 "--name-only", "-r", "HEAD"],
                cwd=str(project_dir), capture_output=True, text=True,
                timeout=10,
            )
            if diff_files.returncode == 0:
                touched = [
                    ln.strip()
                    for ln in diff_files.stdout.splitlines()
                    if ln.strip()
                ]
                scope_creep_other_files = [
                    f for f in touched
                    if not (
                        f.endswith("/improvements.md")
                        or f.endswith("/memory.md")
                        or "runs/" in f
                        or ".evolve/" in f
                    )
                ]
                if scope_creep_other_files:
                    scope_creep = True
        except (subprocess.TimeoutExpired, OSError):
            pass

    converged_written = (run_dir / "CONVERGED").is_file()
    effective_imp_unchanged = imp_unchanged and not converged_written
    unchecked_remaining = _count_unchecked(improvements_path)
    backlog_drained_no_converged = (
        unchecked_remaining == 0
        and imp_unchanged
        and not converged_written
    )

    if converged_written and unchecked_remaining == 0 and imp_unchanged:
        return _AttemptOutcome(
            attempt_sig=None, checked=checked, unchecked=unchecked,
            round_succeeded=True,
        )

    try:
        _r_end = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(project_dir), capture_output=True,
            text=True, timeout=5,
        )
        _head_after_round = (
            _r_end.stdout.strip()
            if _r_end.returncode == 0 else ""
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        _head_after_round = ""
    _round_head_moved = (
        bool(round_start_head_sha)
        and bool(_head_after_round)
        and round_start_head_sha != _head_after_round
    )
    _round_imp_changed = (imp_after != round_start_imp)
    _attempt_had_no_new_issues = not (
        no_commit_msg or memory_wiped or backlog_violated
    )
    if (
        (_round_head_moved or _round_imp_changed)
        and _attempt_had_no_new_issues
    ):
        if us_format_violations:
            _v_summary = "\n".join(
                f"  - {v}" for v in us_format_violations
            )
            _save_subprocess_diagnostic(
                run_dir, round_num,
                ["(post-round US format check)"],
                f"Violations:\n{_v_summary}",
                reason=(
                    f"US FORMAT VIOLATION: "
                    f"{len(us_format_violations)} item(s) lack "
                    f"required US template sections:\n{_v_summary}"
                ),
                attempt=0,
            )
        return _AttemptOutcome(
            attempt_sig=None, checked=checked, unchecked=unchecked,
            round_succeeded=True,
        )

    if backlog_drained_no_converged:
        _save_subprocess_diagnostic(
            run_dir, round_num, cmd, output,
            reason=(
                "BACKLOG DRAINED: all [ ] items checked off, "
                "but agent did not write CONVERGED — the correct "
                "next step is Phase 4 (verify every README claim, "
                "then write CONVERGED), not a zero-progress retry."
            ),
            attempt=attempt,
        )
        return _AttemptOutcome(
            attempt_sig=_failure_signature(
                "no-progress:BACKLOG DRAINED",
                returncode,
                f"unchecked={unchecked_remaining}",
            ),
            checked=checked, unchecked=unchecked, round_succeeded=False,
        )
    if scope_creep:
        creep_summary = ", ".join(scope_creep_other_files[:5])
        reason_str = (
            f"Phase 2 rebuild mixed with implementation: "
            f"{len(backlog_new_items)} new ``[ ]`` item(s) "
            f"added AND non-improvements files touched "
            f"({creep_summary}).  Per SPEC § 'Item format' "
            f"and Phase 2 rule, rebuild rounds commit ONLY "
            f"the improvements.md change; the next round "
            f"picks up the first new item."
        )
        _save_subprocess_diagnostic(
            run_dir, round_num, cmd, output,
            reason=f"SCOPE CREEP: {reason_str}",
            attempt=attempt,
        )
        return _AttemptOutcome(
            attempt_sig=_failure_signature(
                "no-progress:SCOPE CREEP",
                returncode,
                f"new={len(backlog_new_items)}|"
                f"other={len(scope_creep_other_files)}",
            ),
            checked=checked, unchecked=unchecked, round_succeeded=False,
        )
    if (
        no_commit_msg or effective_imp_unchanged
        or memory_wiped or backlog_violated
    ):
        no_progress_reasons: list[str] = []
        if no_commit_msg:
            no_progress_reasons.append(
                "no COMMIT_MSG written (fallback commit message)"
            )
        if effective_imp_unchanged:
            no_progress_reasons.append(
                "improvements.md byte-identical to pre-round state"
            )
        if memory_wiped:
            threshold_pct = int(_MEMORY_WIPE_THRESHOLD * 100)
            no_progress_reasons.append(
                f"memory.md shrunk by >{threshold_pct}% "
                f"({mem_size_before}\u2192{mem_size_after} bytes) "
                f"without '{_MEMORY_COMPACTION_MARKER}' in commit message"
            )
        if backlog_violated:
            new_summary = "; ".join(
                ln[:160] for ln in backlog_new_items[:3]
            )
            no_progress_reasons.append(
                f"backlog discipline rule 1 violated: "
                f"{len(backlog_new_items)} new `- [ ]` item(s) "
                f"added while queue non-empty "
                f"(new: {new_summary})"
            )
        reason_str = " AND ".join(no_progress_reasons)
        if memory_wiped:
            prefix = "MEMORY WIPED"
        elif backlog_violated:
            prefix = _BACKLOG_VIOLATION_PREFIX
        elif _agent_subtype == "error_max_turns":
            prefix = "MAX_TURNS"
            no_progress_reasons.append(
                "SDK subtype=error_max_turns — agent hit turn cap "
                "before finishing"
            )
            reason_str = " AND ".join(no_progress_reasons)
        elif _agent_subtype == "error_during_execution":
            prefix = "SDK ERROR"
            no_progress_reasons.append(
                "SDK subtype=error_during_execution — agent "
                "encountered an execution error"
            )
            reason_str = " AND ".join(no_progress_reasons)
        else:
            prefix = "NO PROGRESS"
        _save_subprocess_diagnostic(
            run_dir, round_num, cmd, output,
            reason=f"{prefix}: {reason_str}",
            attempt=attempt,
        )
        return _AttemptOutcome(
            attempt_sig=_failure_signature(
                f"no-progress:{prefix}", returncode, reason_str,
            ),
            checked=checked, unchecked=unchecked, round_succeeded=False,
        )

    convo = run_dir / f"conversation_loop_{round_num}.md"
    made_progress = (
        checked != prev_checked
        or unchecked != prev_unchecked
        or (convo.is_file() and convo.stat().st_size > convo_size_before)
    )
    if made_progress:
        return _AttemptOutcome(
            attempt_sig=None, checked=checked, unchecked=unchecked,
            round_succeeded=True,
        )

    if _agent_subtype == "error_max_turns":
        _save_subprocess_diagnostic(
            run_dir, round_num, cmd, output,
            reason=(
                "MAX_TURNS: agent hit turn cap without "
                "making progress — target may be too "
                "large, consider splitting"
            ),
            attempt=attempt,
        )
        return _AttemptOutcome(
            attempt_sig=_failure_signature(
                "no-progress:MAX_TURNS", returncode, output,
            ),
            checked=checked, unchecked=unchecked, round_succeeded=False,
        )
    if _agent_subtype == "error_during_execution":
        _save_subprocess_diagnostic(
            run_dir, round_num, cmd, output,
            reason=(
                "SDK ERROR: agent stopped with "
                "error_during_execution — check SDK "
                "error in output"
            ),
            attempt=attempt,
        )
        return _AttemptOutcome(
            attempt_sig=_failure_signature(
                "no-progress:SDK ERROR", returncode, output,
            ),
            checked=checked, unchecked=unchecked, round_succeeded=False,
        )
    _save_subprocess_diagnostic(
        run_dir, round_num, cmd, output,
        reason="no progress (agent ran but changed nothing)",
        attempt=attempt,
    )
    return _AttemptOutcome(
        attempt_sig=_failure_signature(
            "no-progress:silent", returncode, output,
        ),
        checked=checked, unchecked=unchecked, round_succeeded=False,
    )


def _handle_round_success(
    *,
    project_dir: Path,
    run_dir: Path,
    improvements_path: Path,
    ui: TUIProtocol,
    hooks: dict[str, str],
    session_name: str,
    round_num: int,
    max_rounds: int,
    started_at: str,
    rounds_start_time: float,
    cmd: list[str],
    output: str,
    attempt: int,
    spec: str | None,
    capture_frames: bool,
    max_cost: float | None,
    forever: bool,
    failure_signatures: list[str],
) -> tuple[Path, TUIProtocol, int] | None:
    """Handle post-round-success work for a successful round."""
    from evolve.application.run_loop import (
        _detect_file_too_large,
        _detect_layering_violation,
        _detect_tdd_violation,
        _enforce_convergence_backstop,
        _FILE_TOO_LARGE_LIMIT,
        _forever_restart,
        _git_commit,
        _is_self_evolving,
        _parse_report_summary,
        _parse_restart_required,
        _probe,
        _probe_ok,
        _probe_warn,
        _run_curation_pass,
        _run_party_mode,
        _run_spec_archival_pass,
        _runs_base,
        _save_subprocess_diagnostic,
        _write_state_json,
        aggregate_usage,
        build_usage_state,
        fire_hook,
        format_cost,
        get_tui,
    )
    from evolve.infrastructure.reporting.generator import _generate_evolution_report
    from evolve.infrastructure.filesystem.improvement_parser import _parse_check_output

    failure_signatures.clear()

    fire_hook(
        hooks, "on_round_end",
        session=session_name, round_num=round_num, status="success",
    )

    ui.capture_frame(f"round_{round_num}_end")

    _check_passed: bool | None = None
    _check_tests: int | None = None
    _check_duration: float | None = None
    check_file = run_dir / f"check_round_{round_num}.txt"
    if check_file.is_file():
        _ct = check_file.read_text(errors="replace")
        _check_passed, _check_tests, _check_duration = _parse_check_output(_ct)

    _usage_total, _usage_cost, _usage_rounds = aggregate_usage(
        run_dir, round_num,
    )
    _usage_state = build_usage_state(
        _usage_total, _usage_cost, _usage_rounds,
    )

    _write_state_json(
        run_dir=run_dir, project_dir=project_dir, round_num=round_num,
        max_rounds=max_rounds, phase="improvement", status="running",
        improvements_path=improvements_path,
        check_passed=_check_passed, check_tests=_check_tests,
        check_duration_s=_check_duration,
        started_at=started_at, usage=_usage_state,
    )

    if max_cost is not None and _usage_cost is not None:
        if _usage_cost >= max_cost:
            _probe_warn(
                f"budget reached: "
                f"{format_cost(_usage_cost)} / {format_cost(max_cost)}"
            )
            ui.budget_reached(round_num, max_cost, _usage_cost)
            _write_state_json(
                run_dir=run_dir, project_dir=project_dir,
                round_num=round_num, max_rounds=max_rounds,
                phase="improvement", status="budget_reached",
                improvements_path=improvements_path,
                check_passed=_check_passed, check_tests=_check_tests,
                check_duration_s=_check_duration,
                started_at=started_at, usage=_usage_state,
            )
            fire_hook(
                hooks, "on_error",
                session=session_name, round_num=round_num,
                status="budget_reached",
            )
            sys.exit(1)

    error_log = run_dir / f"subprocess_error_round_{round_num}.txt"
    if error_log.is_file():
        error_log.unlink()

    _oversized = _detect_file_too_large(project_dir)
    if _oversized:
        _ftl_lines = "\n".join(
            f"  - {p}: {lc} lines" for p, lc in _oversized
        )
        _probe_warn(f"FILE TOO LARGE detected:\n{_ftl_lines}")
        _save_subprocess_diagnostic(
            run_dir, round_num, ["(post-round file-size check)"],
            f"Oversized files:\n{_ftl_lines}",
            reason=(
                f"FILE TOO LARGE: {len(_oversized)} file(s) exceed "
                f"{_FILE_TOO_LARGE_LIMIT} lines:\n{_ftl_lines}"
            ),
            attempt=0,
        )

    _commit_msg_path = run_dir / "COMMIT_MSG"
    _is_structural = False
    if _commit_msg_path.is_file():
        try:
            _cm = _commit_msg_path.read_text(errors="replace")
            _is_structural = _cm.strip().startswith("STRUCTURAL:")
        except OSError:
            pass
    _tdd_viol = _detect_tdd_violation(
        project_dir, run_dir, round_num, _is_structural
    )
    if _tdd_viol:
        _probe_warn(f"TDD VIOLATION detected: {_tdd_viol}")
        _save_subprocess_diagnostic(
            run_dir, round_num, ["(post-round TDD check)"],
            f"Violation: {_tdd_viol}",
            reason=f"TDD VIOLATION: {_tdd_viol}",
            attempt=0,
        )

    _layer_viols = _detect_layering_violation(project_dir)
    if _layer_viols:
        _lv_lines = "\n".join(
            f"  - {f} imports {m} (layer {s} -> layer {t})"
            for f, m, s, t in _layer_viols
        )
        _probe_warn(f"LAYERING VIOLATION detected:\n{_lv_lines}")
        _save_subprocess_diagnostic(
            run_dir, round_num, ["(post-round layering check)"],
            f"Violations:\n{_lv_lines}",
            reason=f"LAYERING VIOLATION: {len(_layer_viols)} inward-violating edge(s):\n{_lv_lines}",
            attempt=0,
        )

    _run_curation_pass(
        project_dir, run_dir, round_num,
        improvements_path, spec, ui,
    )

    _run_spec_archival_pass(
        project_dir, run_dir, round_num, spec, ui,
    )

    restart_marker = _parse_restart_required(run_dir)
    if restart_marker is not None and not _is_self_evolving(project_dir):
        _probe(
            f"RESTART_REQUIRED marker present but project is not "
            f"evolve itself — ignoring (target's structural change "
            f"does not affect the orchestrator)"
        )
        restart_marker = None
    if restart_marker is not None:
        _probe_warn(
            f"RESTART_REQUIRED detected: "
            f"{restart_marker.get('reason', '?')}"
        )
        structural_env = {
            "EVOLVE_STRUCTURAL_REASON": restart_marker.get("reason", ""),
            "EVOLVE_STRUCTURAL_VERIFY": restart_marker.get("verify", ""),
            "EVOLVE_STRUCTURAL_RESUME": restart_marker.get("resume", ""),
            "EVOLVE_STRUCTURAL_ROUND": restart_marker.get("round", ""),
            "EVOLVE_STRUCTURAL_TIMESTAMP": restart_marker.get(
                "timestamp", "",
            ),
        }
        fire_hook(
            hooks, "on_structural_change",
            session=session_name, round_num=round_num,
            status="structural_change",
            extra_env=structural_env,
        )
        ui.structural_change_required(restart_marker)
        sys.exit(3)

    converged_path = run_dir / "CONVERGED"
    if converged_path.is_file():
        spec_file = spec or "README.md"
        spec_path = project_dir / spec_file
        if (
            converged_path.is_file()
            and spec_path.is_file()
            and improvements_path.is_file()
        ):
            if (
                spec_path.stat().st_mtime
                > improvements_path.stat().st_mtime
            ):
                _probe(
                    "convergence rejected: spec is newer than "
                    "improvements.md — removing CONVERGED marker"
                )
                converged_path.unlink()
        _enforce_convergence_backstop(
            converged_path, improvements_path, spec_path,
            run_dir, round_num, cmd, output, attempt, ui,
        )
    if converged_path.is_file():
        reason = converged_path.read_text().strip()
        _probe_ok(f"CONVERGED at round {round_num}: {reason[:80]}")
        ui.converged(round_num, reason)

        ui.capture_frame("converged")
        fire_hook(
            hooks, "on_converged",
            session=session_name, round_num=round_num, status="converged",
        )
        _write_state_json(
            run_dir=run_dir, project_dir=project_dir,
            round_num=round_num, max_rounds=max_rounds,
            phase="convergence", status="converged",
            improvements_path=improvements_path,
            check_passed=_check_passed, check_tests=_check_tests,
            check_duration_s=_check_duration,
            started_at=started_at, usage=_usage_state,
        )
        _generate_evolution_report(
            project_dir, run_dir, max_rounds, round_num,
            converged=True, capture_frames=capture_frames,
        )
        duration_s = time.monotonic() - rounds_start_time
        summary_stats = _parse_report_summary(run_dir)
        ui.completion_summary(
            status="CONVERGED",
            round_num=round_num,
            duration_s=duration_s,
            improvements=summary_stats["improvements"],
            bugs_fixed=summary_stats["bugs_fixed"],
            tests_passing=summary_stats["tests_passing"],
            report_path=str(run_dir / "evolution_report.md"),
            estimated_cost_usd=_usage_cost,
        )

        if forever:
            _run_party_mode(project_dir, run_dir, ui, spec=spec)
        else:
            _probe(
                "convergence reached — skipping party mode "
                "(only runs in --forever mode; without forever, "
                "convergence = stop)"
            )

        if forever:
            adoption_result = _forever_restart(
                project_dir, run_dir, improvements_path, ui, spec=spec,
            )
            if isinstance(adoption_result, tuple):
                spec_adopted, _ = adoption_result
            else:
                spec_adopted = False

            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            new_run_dir = _runs_base(project_dir) / timestamp
            new_run_dir.mkdir(parents=True, exist_ok=True)
            if capture_frames:
                new_ui = get_tui(
                    run_dir=new_run_dir, capture_frames=capture_frames,
                )
            else:
                new_ui = ui
            new_ui.run_dir_info(str(new_run_dir))

            spec_file_for_msg = spec or "README.md"
            if spec_file_for_msg != "README.md" and spec_adopted:
                spec_stem_msg = Path(spec_file_for_msg).stem
                spec_suffix_msg = Path(spec_file_for_msg).suffix or ".md"
                proposal_name_msg = (
                    f"{spec_stem_msg}_proposal{spec_suffix_msg}"
                )
                commit_msg = (
                    f"feat(spec): adopt {proposal_name_msg}\n"
                    "\n"
                    f"- {spec_file_for_msg} updated from {proposal_name_msg}\n"
                    "- improvements.md reset"
                )
            else:
                commit_msg = (
                    "chore(evolve): forever mode — adopt proposal, "
                    "reset improvements"
                )
            _git_commit(project_dir, commit_msg, new_ui)

            return new_run_dir, new_ui, 1

        sys.exit(0)

    return None
