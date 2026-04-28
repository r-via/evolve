"""Round lifecycle — attempt-outcome diagnosis and round-success handling."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from evolve.tui import TUIProtocol

# Re-export from round_success.py (US-041 split); preserves patch surfaces.
from evolve.round_success import _handle_round_success  # noqa: F401


@dataclass
class _AttemptOutcome:
    """Result of ``_diagnose_attempt_outcome``."""
    attempt_sig: str | None
    checked: int
    unchecked: int
    round_succeeded: bool
    is_review_retry: bool = False
    review_retry_circuit_tripped: bool = False


def _diagnose_attempt_outcome(
    *,
    run_dir: Path,
    round_num: int,
    project_dir: Path,
    improvements_path: Path,
    cmd: list[str],
    output: str,
    stalled: bool,
    returncode: int,
    attempt: int,
    checked: int,
    unchecked: int,
    imp_snapshot_before: bytes,
    mem_size_before: int,
    head_sha_before: str,
    convo_size_before: int,
    round_start_head_sha: str,
    round_start_imp: bytes,
    ui: TUIProtocol,
    hooks: dict[str, str],
    session_name: str,
    failure_signatures: list[str],
) -> _AttemptOutcome:
    """Diagnose a single round attempt's outcome."""
    # Lazy-import via evolve.orchestrator to preserve test patches —
    # tests patch many of these via ``patch("evolve.orchestrator.X")``.
    from evolve.orchestrator import (
        MAX_IDENTICAL_FAILURES,
        WATCHDOG_TIMEOUT,
        _BACKLOG_VIOLATION_PREFIX,
        _MEMORY_COMPACTION_MARKER,
        _MEMORY_WIPE_THRESHOLD,
        _check_review_verdict,
        _count_checked,
        _count_unchecked,
        _detect_backlog_violation,
        _detect_us_format_violation,
        _failure_signature,
        _is_circuit_breaker_tripped,
        _probe_warn,
        _runs_base,
        _save_subprocess_diagnostic,
        fire_hook,
    )

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

    # Subprocess exited OK — check for actual progress
    prev_checked = checked
    prev_unchecked = unchecked
    unchecked = _count_unchecked(improvements_path)
    checked = _count_checked(improvements_path)
    ui.progress_summary(checked, unchecked)

    # --- Adversarial review verdict routing ---
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

    # --- Read SDK subtype (authoritative termination signal) ---
    _subtype_path = run_dir / f"agent_subtype_round_{round_num}.txt"
    _agent_subtype: str | None = None
    if _subtype_path.is_file():
        _agent_subtype = _subtype_path.read_text().strip() or None

    # --- Zero-progress detection ---
    imp_after = (
        improvements_path.read_bytes() if improvements_path.is_file() else b""
    )
    imp_unchanged = (imp_after == imp_snapshot_before)

    # 2. No-commit-msg (fallback commit) detection — only for THIS attempt
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

    # 3. Memory-wipe sanity gate
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

    # 4. Backlog discipline rule 1
    backlog_violated = False
    backlog_new_items: list[str] = []
    if not imp_unchanged:
        try:
            pre_text = imp_snapshot_before.decode("utf-8", errors="replace")
            post_text = imp_after.decode("utf-8", errors="replace")
            backlog_violated, backlog_new_items = (
                _detect_backlog_violation(pre_text, post_text)
            )
        except Exception as e:  # pragma: no cover — defensive
            _probe_warn(f"backlog-violation check skipped: {e}")

    # 5. US format validation (pre-commit check)
    us_format_violations: list[str] = []
    if not imp_unchanged:
        try:
            pre_lines = imp_snapshot_before.decode(
                "utf-8", errors="replace"
            ).splitlines()
            us_format_violations = _detect_us_format_violation(
                improvements_path, pre_lines
            )
        except Exception as e:  # pragma: no cover — defensive
            _probe_warn(f"US format validation skipped: {e}")

    # Scope creep — rebuild + implement in one round
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

    # Effective imp_unchanged + backlog drained
    converged_written = (run_dir / "CONVERGED").is_file()
    effective_imp_unchanged = imp_unchanged and not converged_written
    unchecked_remaining = _count_unchecked(improvements_path)
    backlog_drained_no_converged = (
        unchecked_remaining == 0
        and imp_unchanged
        and not converged_written
    )

    # Convergence-already-detected carve-out.  A draft round invoked
    # on a drained backlog with the CONVERGED marker already present
    # is the correct terminal state: the agent observed convergence,
    # decided there is nothing to draft, and returned without edits.
    # Without this carve-out the round falls through to the silent
    # "agent ran but changed nothing" no-progress catchall and the
    # parent loop's convergence check (which would normally read
    # CONVERGED and exit cleanly) is never reached because the round
    # is marked as failed.  Treating this as a successful round lets
    # the parent loop see CONVERGED and stop the session.
    if converged_written and unchecked_remaining == 0 and imp_unchanged:
        return _AttemptOutcome(
            attempt_sig=None, checked=checked, unchecked=unchecked,
            round_succeeded=True,
        )

    # Round-level "already-done" escape hatch
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
        # US format validation — advisory diagnostic (does not block)
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

    # No detection fired — check made_progress fallback
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

    # Subtype-aware silent no-progress diagnostic
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

