"""Round success handling — post-success work for a successful round.

Extracted from ``evolve/round_lifecycle.py::_handle_round_success`` per
US-041 to keep ``round_lifecycle.py`` under the SPEC § "Hard rule:
source files MUST NOT exceed 500 lines" cap.

Single helper:

- ``_handle_round_success`` — encapsulates the post-round-success block
  (hook firing, check parsing, state.json, budget enforcement, FILE
  TOO LARGE detection, memory curation, SPEC archival, structural-
  change detection, convergence + party mode + forever restart).

Heavy dependencies on orchestrator internals (``_run_curation_pass``,
``_run_party_mode``, ``_forever_restart``, etc.) are lazy-imported via
``from evolve.orchestrator import ...`` inside the helper body to
preserve ``patch("evolve.orchestrator.X", ...)`` test surfaces (US-036
/ US-037 / US-038 / US-040 lesson).

Leaf-module invariant: zero top-level imports from
``evolve.(agent|orchestrator|cli|round_lifecycle)`` — verified by
``tests/test_round_success_module.py``.
"""

from __future__ import annotations

import sys
import time
from datetime import datetime
from pathlib import Path

from evolve.tui import TUIProtocol


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
    """Handle post-round-success work for a successful round.

    Replaces the L1132-L1444 block of the original ``_run_rounds`` body.

    Returns ``(new_run_dir, new_ui, new_start_round)`` for the forever-
    restart case (caller breaks out of the for loop and continues the
    while loop with the new state).  Returns ``None`` otherwise — the
    caller falls through to the next round in the for loop.

    May call ``sys.exit()`` for budget reached (1), structural change
    (3), or normal convergence (0) — exit codes match the original
    behaviour exactly.
    """
    # Lazy-imports preserve patch surfaces.
    from evolve.orchestrator import (
        _detect_file_too_large,
        _enforce_convergence_backstop,
        _FILE_TOO_LARGE_LIMIT,
        _forever_restart,
        _generate_evolution_report,
        _git_commit,
        _is_self_evolving,
        _parse_check_output,
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

    # Round succeeded — reset the circuit breaker so a single recovery
    # clears the deterministic-failure counter.
    failure_signatures.clear()

    # Fire on_round_end hook for successful round
    fire_hook(
        hooks, "on_round_end",
        session=session_name, round_num=round_num, status="success",
    )

    # Capture round-end frame
    ui.capture_frame(f"round_{round_num}_end")

    # Parse last check results for state.json
    _check_passed: bool | None = None
    _check_tests: int | None = None
    _check_duration: float | None = None
    check_file = run_dir / f"check_round_{round_num}.txt"
    if check_file.is_file():
        _ct = check_file.read_text(errors="replace")
        _check_passed, _check_tests, _check_duration = _parse_check_output(_ct)

    # Aggregate token usage across all rounds for cost tracking
    _usage_total, _usage_cost, _usage_rounds = aggregate_usage(
        run_dir, round_num,
    )
    _usage_state = build_usage_state(
        _usage_total, _usage_cost, _usage_rounds,
    )

    # Update state.json after every round
    _write_state_json(
        run_dir=run_dir, project_dir=project_dir, round_num=round_num,
        max_rounds=max_rounds, phase="improvement", status="running",
        improvements_path=improvements_path,
        check_passed=_check_passed, check_tests=_check_tests,
        check_duration_s=_check_duration,
        started_at=started_at, usage=_usage_state,
    )

    # Budget enforcement — pause session if cost exceeds --max-cost
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

    # Clean up diagnostic file on success (no longer relevant)
    error_log = run_dir / f"subprocess_error_round_{round_num}.txt"
    if error_log.is_file():
        error_log.unlink()

    # FILE TOO LARGE detection (advisory diagnostic for next round)
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

    # Memory curation (Mira) — between rounds, after post-check
    _run_curation_pass(
        project_dir, run_dir, round_num,
        improvements_path, spec, ui,
    )

    # SPEC archival (Sid) — between rounds, after post-check + curation
    _run_spec_archival_pass(
        project_dir, run_dir, round_num, spec, ui,
    )

    # Structural change detection (RESTART_REQUIRED handling)
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

    # Convergence — requires spec freshness gate AND CONVERGED file
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

        # Party mode — only in --forever mode
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
