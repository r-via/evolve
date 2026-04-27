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
from evolve.tui import TUIProtocol, get_tui


# ANSI-styled ``[probe]`` prefix.  The round subprocess writes to
# stdout; the parent orchestrator captures each line and routes it
# through ``RichTUI.subprocess_output`` which runs ``Text.from_ansi``
# on it, so embedding escape codes here gives us styled probe output
# in the Rich console without requiring a TUIProtocol method.  Dim
# cyan is visually quiet (probe lines are frequent and shouldn't
# compete with the agent's tool calls for attention) while still
# standing apart from plain text.
_PROBE_PREFIX = "\x1b[2;36m[probe]\x1b[0m"   # dim cyan
_PROBE_WARN_PREFIX = "\x1b[33m[probe]\x1b[0m"  # yellow — timeouts, failures
_PROBE_OK_PREFIX = "\x1b[32m[probe]\x1b[0m"    # green — PASSED, converged


def _probe(msg: str) -> None:
    """Emit a styled orchestrator probe line to stdout.

    Used by every orchestrator-side trace print so the ``[probe]``
    prefix renders consistently (dim cyan) in the Rich parent TUI
    and stays recognisable in plain-text logs.  Always flushes so
    the line arrives before the next tool call or subprocess event.
    """
    print(f"{_PROBE_PREFIX} {msg}", flush=True)


def _probe_warn(msg: str) -> None:
    """Probe line flagged as a warning (yellow prefix)."""
    print(f"{_PROBE_WARN_PREFIX} {msg}", flush=True)


def _probe_ok(msg: str) -> None:
    """Probe line flagged as a success (green prefix)."""
    print(f"{_PROBE_OK_PREFIX} {msg}", flush=True)


def _scaffold_shared_runtime_files(project_dir: Path, spec: str | None) -> None:
    """Pre-create shared cross-round runtime files at ``{runs_base}``.

    Predictable files that every evolution session needs — the
    backlog (``improvements.md``) and the cumulative learning log
    (``memory.md``) — are created by code, not by the agent's
    system prompt.  Rationale:

    - The agent's prompt can INSTRUCT it to write in a particular
      path but cannot GUARANTEE the path: a prompt glitch, a model
      change, or an ambiguous interpretation of "runs/improvements.md"
      vs "{run_dir}/improvements.md" can land the file in the wrong
      place, costing rounds to recover.
    - Pre-existing files at the canonical location are
      unambiguous — the agent reads and appends to them, rather
      than deciding where to create them.
    - Both files are trivial to scaffold with a sane default
      template; no work is lost and the contract is simple.

    Idempotent: existing files are never overwritten.  Both files
    land under ``_runs_base(project_dir)`` — the canonical
    ``.evolve/runs/`` on fresh projects, the legacy ``runs/`` on
    projects still mid-migration.

    Args:
        project_dir: Root directory of the project being evolved.
        spec: Optional spec filename (``SPEC.md``, ``README.md``, …)
            — forwarded to the memory scaffolder so the default
            pointer prose names the actual spec file.
    """
    runs_base = _runs_base(project_dir)
    runs_base.mkdir(parents=True, exist_ok=True)

    # improvements.md — shared backlog
    imp_path = runs_base / "improvements.md"
    if not imp_path.is_file():
        imp_path.write_text(
            "# Improvements\n\n"
            "Backlog of user-story items driving evolution — "
            "see SPEC.md § \"Item format — user story with "
            "acceptance criteria\".  Entries are appended by the "
            "Winston → John → final-draft persona pipeline; the "
            "orchestrator's pre-commit check rejects free-form "
            "additions.  New sessions append here; this file is "
            "shared across rounds and across sessions of the same "
            "project.\n"
        )

    # memory.md — cumulative learning log with typed sections
    mem_path = runs_base / "memory.md"
    if not mem_path.is_file():
        # Reuse the CLI's memory template renderer when available so
        # the cold-start scaffold is identical whether the operator
        # runs ``evolve init`` first or jumps straight to
        # ``evolve start``.  Falls back to a minimal inline template
        # if the import fails (defensive).
        try:
            from evolve.cli import _render_default_memory_md
            mem_path.write_text(_render_default_memory_md(spec))
        except ImportError:  # pragma: no cover — defensive fallback
            mem_path.write_text(
                "# Agent Memory\n\n"
                "## Errors\n\n## Decisions\n\n## Patterns\n\n## Insights\n"
            )


def _is_self_evolving(project_dir: Path) -> bool:
    """Return True when evolve is evolving its own source tree.

    The ``RESTART_REQUIRED`` structural-change protocol protects the
    running orchestrator from stale imports after a rename, __init__.py
    edit, or entry-point move.  That only matters when the project
    being evolved IS the orchestrator's own code — typically, when
    ``project_dir`` resolves to the directory that contains the
    currently-imported ``evolve/`` package (this module's own parent).

    When evolve is driving a third-party project (the common case —
    ``python -m evolve start /path/to/foo``), structural changes in
    ``foo/`` never touch ``evolve/`` and the orchestrator's imports
    stay valid.  RESTART_REQUIRED in that case would be pure theatre:
    the marker still gets written as an audit trail, but the
    orchestrator keeps running.

    Comparison is done on resolved absolute paths to survive symlinks
    and relative invocations.

    Args:
        project_dir: Root directory of the project being evolved.

    Returns:
        True iff ``project_dir`` resolves to the same directory as the
        project that contains the currently-imported ``evolve`` package.
    """
    try:
        evolve_package_dir = Path(__file__).resolve().parent  # .../evolve
        evolve_project_root = evolve_package_dir.parent       # .../ (repo root)
        return project_dir.resolve() == evolve_project_root
    except (OSError, RuntimeError):
        # If we can't resolve (e.g. symlink loop, stale parent dir),
        # err on the side of caution — treat as self-evolving so the
        # safety protocol still fires.  A false positive is a harmless
        # exit 3; a false negative could leave a stale orchestrator.
        return True


def _enforce_convergence_backstop(
    converged_path: Path,
    improvements_path: Path,
    spec_path: Path,
    run_dir: Path,
    round_num: int,
    cmd: list[str],
    output: str,
    attempt: int,
    ui,
) -> bool:
    """Independently re-verify convergence gates after the agent wrote ``CONVERGED``.

    When either documented gate in SPEC.md § "Convergence" is violated,
    this function unlinks the ``CONVERGED`` marker, saves a
    ``subprocess_error_round_N.txt`` diagnostic with a ``PREMATURE
    CONVERGED: <reason>`` prefix, and emits ``ui.error``. The next round
    will pick up the diagnostic via ``agent.py``'s ``build_prompt`` and
    surface a dedicated ``CRITICAL — Premature CONVERGED`` header so the
    agent addresses the violated gate before attempting convergence
    again.

    This is the orchestrator-side trust boundary — without it, Phase 4
    criteria remain 100% agent-judged.

    Args:
        converged_path: Path to the ``CONVERGED`` marker file.
        improvements_path: Path to ``improvements.md``.
        spec_path: Path to the spec file.
        run_dir: Session directory (used to write the diagnostic).
        round_num: Current round number.
        cmd: Original subprocess command (echoed into the diagnostic).
        output: Subprocess output (echoed into the diagnostic).
        attempt: Current attempt number (echoed into the diagnostic).
        ui: TUI instance for ``ui.error`` emission.

    Returns:
        True iff the backstop rejected convergence (marker unlinked,
        diagnostic saved); False when both gates pass.
    """
    if not converged_path.is_file():
        return False
    is_premature, reason = _detect_premature_converged(
        improvements_path, spec_path
    )
    if not is_premature:
        return False
    ui.error(f"Premature CONVERGED rejected: {reason}")
    _probe(f"convergence-gate backstop rejected: {reason}")
    converged_path.unlink()
    _save_subprocess_diagnostic(
        run_dir,
        round_num,
        cmd,
        output,
        reason=f"PREMATURE CONVERGED: {reason}",
        attempt=attempt,
    )
    return True


def _parse_report_summary(run_dir: Path) -> dict:
    """Parse evolution_report.md to extract completion summary stats.

    Returns a dict with keys: improvements, bugs_fixed, tests_passing.
    """
    report_path = run_dir / "evolution_report.md"
    improvements = 0
    bugs_fixed = 0
    tests_passing: int | None = None

    if report_path.is_file():
        text = report_path.read_text(errors="replace")
        m = re.search(r"(\d+)\s+improvements completed", text)
        if m:
            improvements = int(m.group(1))
        m = re.search(r"(\d+)\s+bugs fixed", text)
        if m:
            bugs_fixed = int(m.group(1))

    # Get latest test count from the most recent check_round_N.txt
    check_files = sorted(run_dir.glob("check_round_*.txt"))
    if check_files:
        last_check = check_files[-1].read_text(errors="replace")
        m = re.search(r"(\d+)\s+passed", last_check)
        if m:
            tests_passing = int(m.group(1))

    return {
        "improvements": improvements,
        "bugs_fixed": bugs_fixed,
        "tests_passing": tests_passing,
    }


def evolve_loop(
    project_dir: Path,
    max_rounds: int = 10,
    check_cmd: str | None = None,
    allow_installs: bool = False,
    timeout: int = 20,
    model: str = "claude-opus-4-6",
    resume: bool = False,
    forever: bool = False,
    spec: str | None = None,
    yolo: bool | None = None,
    capture_frames: bool = False,
    effort: str | None = "medium",
    max_cost: float | None = None,
) -> None:
    """Orchestrate evolution by launching each round as a subprocess.

    Creates a timestamped session directory, then delegates to ``_run_rounds``
    for the main loop.  Supports ``--resume`` to continue an interrupted
    session and ``--forever`` for autonomous indefinite evolution.

    Args:
        project_dir: Root directory of the project being evolved.
        max_rounds: Maximum number of evolution rounds.
        check_cmd: Shell command to verify the project after each round.
        allow_installs: If True, allow improvements requiring new packages.
        timeout: Timeout in seconds for the check command.
        model: Claude model identifier to use.
        resume: If True, resume the most recent interrupted session.
        forever: If True, run indefinitely on a dedicated branch.
        spec: Path to the spec file relative to project_dir (default: README.md).
        yolo: Deprecated alias for *allow_installs*. Will be removed in a future version.
        capture_frames: If True, capture TUI frames as PNG at key moments.
        max_cost: Budget cap in USD. When cumulative cost exceeds this, the
            session pauses after the current round.
    """
    if yolo is not None:
        allow_installs = yolo

    # SPEC § "Migration from legacy runs/" — ensure .evolve/runs/ layout
    # and migrate legacy runs/ if needed, before any path resolution.
    try:
        _ensure_runs_layout(project_dir)
    except _RunsLayoutError as exc:
        ui_early = get_tui()
        ui_early.error(f"Runs layout error: {exc}")
        import sys as _sys
        _sys.exit(2)

    improvements_path = _runs_base(project_dir) / "improvements.md"

    # Pre-create shared runtime files if missing — ``improvements.md``
    # and ``memory.md`` are canonical cross-round state and must exist
    # at ``{runs_base}`` before the first round so the agent doesn't
    # have to guess where to create them (the old default behaviour
    # sometimes produced per-session copies under ``{run_dir}``).
    # The agent's system prompt is prescriptive about paths but
    # prescriptive instructions cannot replace the file actually
    # existing — when a predictable file is expected, code creates
    # it, not instructions.
    _scaffold_shared_runtime_files(project_dir, spec)

    from evolve.agent import MAX_TURNS as _MAX_TURNS
    _probe(
        f"evolve_loop starting — project={project_dir.name}, "
        f"max_rounds={max_rounds}, check={check_cmd or '(auto-detect)'}, "
        f"model={model}, effort={effort}, max_turns={_MAX_TURNS}"
    )
    # Announce the two independent timing axes once at startup so the
    # reader doesn't have to reconstruct them from scattered messages:
    #   * check_timeout = hard ceiling on pre/post-check pytest runs.
    #   * watchdog      = max stdout silence before SIGKILL on the
    #                     round subprocess.  The round-wide heartbeat
    #                     (every 30s) is what keeps this quiet; the
    #                     axis is about silence, not elapsed time.
    _probe(
        f"timing axes — check_timeout: {timeout}s (pre/post), "
        f"watchdog: {WATCHDOG_TIMEOUT}s silence, heartbeat: every 30s"
    )

    # Load event hooks from project config
    hooks = load_hooks(project_dir)
    if hooks:
        _probe(f"loaded {len(hooks)} hook(s): {', '.join(hooks.keys())}")

    # Startup-time stale-README advisory (SPEC § "Stale-README pre-flight
    # check") — pure observability, runs once before the first round.
    _emit_stale_readme_advisory(project_dir, spec, get_tui())

    # Auto-detect check command if not provided
    if check_cmd is None:
        detected = _auto_detect_check(project_dir)
        if detected:
            ui_early = get_tui()
            ui_early.info(f"  Auto-detected check command: {detected}")
            check_cmd = detected
            _probe(f"auto-detected check command: {detected}")

    start_round = 1

    # In forever mode, create a separate branch and run indefinitely
    if forever:
        _probe("forever mode enabled — creating dedicated branch")
        _setup_forever_branch(project_dir)
        # Use a very large max_rounds so the loop runs until convergence
        max_rounds = 999999

    if resume:
        # Find the most recent session and detect last completed round
        runs_dir = _runs_base(project_dir)
        if runs_dir.is_dir():
            sessions = sorted(
                [d for d in runs_dir.iterdir() if d.is_dir() and d.name[0].isdigit()],
                reverse=True,
            )
            if sessions:
                run_dir = sessions[0]
                # Detect last completed round from conversation logs
                def _convo_sort_key(p: Path) -> int:
                    try:
                        return int(p.stem.rsplit("_", 1)[1])
                    except (ValueError, IndexError):
                        return -1

                convos = sorted(run_dir.glob("conversation_loop_*.md"), key=_convo_sort_key)
                if convos:
                    last = convos[-1].stem  # conversation_loop_N
                    try:
                        last_round = int(last.rsplit("_", 1)[1])
                        start_round = last_round + 1
                    except (ValueError, IndexError):
                        pass
                ui = get_tui(run_dir=run_dir, capture_frames=capture_frames)
                ui.run_dir_info(f"{run_dir} (resumed from round {start_round})")

                # Ensure git
                _ensure_git(project_dir, ui)

                # Jump to loop body
                return _run_rounds(
                    project_dir, run_dir, improvements_path, ui,
                    start_round, max_rounds, check_cmd, allow_installs, timeout, model,
                    forever=forever, hooks=hooks, spec=spec,
                    capture_frames=capture_frames,
                    effort=effort,
                    max_cost=max_cost,
                )

    # Create timestamped run directory
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = _runs_base(project_dir) / timestamp
    run_dir.mkdir(parents=True, exist_ok=True)
    ui = get_tui(run_dir=run_dir, capture_frames=capture_frames)
    ui.run_dir_info(str(run_dir))

    # Ensure git
    _ensure_git(project_dir, ui)

    _run_rounds(
        project_dir, run_dir, improvements_path, ui,
        1, max_rounds, check_cmd, allow_installs, timeout, model,
        forever=forever, hooks=hooks, spec=spec,
        capture_frames=capture_frames,
        effort=effort,
        max_cost=max_cost,
    )


# Maximum number of debug retries when a round fails, stalls, or makes no progress.
MAX_DEBUG_RETRIES = 2
# Seconds of silence before the watchdog considers a subprocess stalled.
WATCHDOG_TIMEOUT = 120

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


def _run_monitored_subprocess(
    cmd: list[str],
    cwd: str,
    ui: TUIProtocol,
    round_num: int,
    watchdog_timeout: int = WATCHDOG_TIMEOUT,
) -> tuple[int, str, bool]:
    """Run a subprocess with real-time output streaming and stall detection.

    Spawns the command, streams stdout in real-time, and monitors for
    inactivity.  If no output is produced for ``watchdog_timeout`` seconds
    the process is killed.

    Args:
        cmd: Command list to execute.
        cwd: Working directory for the subprocess.
        ui: TUI instance for status messages.
        round_num: Current round number (for diagnostic messages).
        watchdog_timeout: Seconds of silence before killing the process.

    Returns:
        A tuple ``(returncode, output, stalled)`` where *stalled* is True
        when the watchdog killed the process due to inactivity.
    """
    # -u ensures Python doesn't buffer stdout/stderr in the child process.
    if cmd[0] == sys.executable and "-u" not in cmd:
        cmd = [cmd[0], "-u"] + cmd[1:]

    proc = subprocess.Popen(
        cmd, cwd=cwd,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, bufsize=1,
    )

    output_lines: list[str] = []
    last_activity = time.monotonic()
    lock = threading.Lock()

    def _reader():
        """Read subprocess stdout line-by-line, updating the watchdog timer.

        Runs in a daemon thread.  Each line is appended to *output_lines*
        (under *lock*) and echoed to ``sys.stdout`` so the orchestrator's
        watchdog sees continuous activity.  Updates *last_activity* on every
        line to prevent the watchdog from killing an active process.
        """
        nonlocal last_activity
        assert proc.stdout is not None
        for line in proc.stdout:
            with lock:
                output_lines.append(line)
                last_activity = time.monotonic()
            # Route through the TUI so (a) subprocess output lands in the
            # Rich record buffer for frame capture, and (b) JsonTUI emits
            # a structured event per line. RichTUI preserves ANSI codes via
            # console.out(markup=False, highlight=False); PlainTUI falls
            # back to sys.stdout.write for parity with the old behavior.
            ui.subprocess_output(line)

    reader_thread = threading.Thread(target=_reader, daemon=True)
    reader_thread.start()

    # Check-in interval scales with ``watchdog_timeout`` — a 2-second
    # test watchdog wakes up every 200ms while a 120-second production
    # watchdog checks in every 1s.  We use ``proc.wait(timeout=...)``
    # rather than ``time.sleep + poll``: wait() returns *immediately*
    # when the subprocess exits (saving up to one interval per call)
    # and raises ``TimeoutExpired`` only when the process is still
    # alive at the deadline — at which point we check the silence
    # watchdog.  Capped at 1.0s so CPU overhead stays negligible.
    _wait_interval = min(1.0, max(0.1, watchdog_timeout / 10.0))
    stalled = False
    while True:
        try:
            proc.wait(timeout=_wait_interval)
            break  # subprocess exited cleanly
        except subprocess.TimeoutExpired:
            with lock:
                idle = time.monotonic() - last_activity
            if idle > watchdog_timeout:
                stalled = True
                ui.warn(
                    f"Round {round_num} stalled ({int(idle)}s without output) "
                    "— killing subprocess"
                )
                proc.kill()
                break

    reader_thread.join(timeout=5)
    output = "".join(output_lines)
    rc = proc.returncode if proc.returncode is not None else -9
    return rc, output, stalled


def _run_curation_pass(
    project_dir: Path,
    run_dir: Path,
    round_num: int,
    improvements_path: Path,
    spec: str | None,
    ui: TUIProtocol,
) -> None:
    """Run memory curation (Mira) between rounds if triggered.

    SPEC § "Dedicated memory curation (Mira)".  Delegates to
    ``agent.run_memory_curation`` which handles prompt building, SDK
    invocation, shrinkage checks, and abort recovery.  This function
    is a thin orchestrator-side wrapper that resolves paths and logs
    the verdict.
    """
    from evolve.agent import run_memory_curation

    memory_path = _runs_base(project_dir) / "memory.md"
    spec_path = project_dir / (spec or "README.md") if spec else project_dir / "README.md"

    verdict = run_memory_curation(
        project_dir=project_dir,
        run_dir=run_dir,
        round_num=round_num,
        memory_path=memory_path,
        spec_path=spec_path,
    )

    if verdict == "SKIPPED":
        return  # silent — no log needed
    elif verdict == "CURATED":
        _probe(f"memory curation: CURATED at round {round_num}")
        # SPEC § "Dedicated memory curation (Mira)" — verdict routing:
        # CURATED → commit with `memory: compaction` marker so the
        # byte-size sanity gate (§ "Byte-size sanity gate") accepts
        # the shrink.  The curation modified memory.md in-place and
        # wrote an audit log; commit both.
        commit_msg = (
            f"chore(memory): curation round {round_num}\n\n"
            f"memory: compaction\n"
        )
        _git_commit(project_dir, commit_msg, ui)
    elif verdict == "ABORTED":
        _probe_warn(f"memory curation: ABORTED at round {round_num} (>80% shrink)")
    elif verdict == "SDK_FAIL":
        _probe_warn(f"memory curation: SDK_FAIL at round {round_num}")


def _should_run_spec_archival(project_dir: Path, round_num: int, spec: str | None = None) -> bool:
    """Return True when SPEC archival should run this round.

    Thin wrapper around ``agent._should_run_spec_archival`` that resolves
    the spec path from the project directory.  SPEC § "SPEC archival (Sid)"
    AC 3: this function lives in ``evolve/orchestrator.py``.
    """
    from evolve.agent import _should_run_spec_archival as _agent_check

    spec_path = project_dir / (spec or "README.md")
    return _agent_check(spec_path, round_num)


def _run_spec_archival_pass(
    project_dir: Path,
    run_dir: Path,
    round_num: int,
    spec: str | None,
    ui: TUIProtocol,
) -> None:
    """Run SPEC archival (Sid) between rounds if triggered.

    SPEC § "SPEC archival (Sid)".  Delegates to
    ``agent.run_spec_archival`` which handles prompt building, SDK
    invocation, shrinkage checks, and abort recovery.  This function
    is a thin orchestrator-side wrapper that resolves paths and logs
    the verdict.
    """
    from evolve.agent import run_spec_archival

    spec_path = project_dir / (spec or "README.md")
    if not spec_path.is_file():
        return

    verdict = run_spec_archival(
        project_dir=project_dir,
        run_dir=run_dir,
        round_num=round_num,
        spec_path=spec_path,
    )

    if verdict == "SKIPPED":
        return  # silent
    elif verdict == "ARCHIVED":
        _probe(f"SPEC archival: ARCHIVED at round {round_num}")
        commit_msg = (
            f"chore(spec): archival round {round_num}\n\n"
            f"SPEC sections moved to SPEC/archive/.\n"
        )
        _git_commit(project_dir, commit_msg, ui)
    elif verdict == "ABORTED":
        _probe_warn(f"SPEC archival: ABORTED at round {round_num} (>80% shrink)")
    elif verdict == "SDK_FAIL":
        _probe_warn(f"SPEC archival: SDK_FAIL at round {round_num}")


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
                # Signature of this attempt's failure, captured in each
                # failure arm and fed to the circuit breaker at the end
                # of the attempt (after the diagnostic file is saved, so
                # exit 4 leaves behind a complete paper trail).
                _attempt_sig: str | None = None
                if stalled:
                    ui.round_failed(round_num, returncode)
                    _save_subprocess_diagnostic(
                        run_dir, round_num, cmd, output,
                        reason=f"stalled ({WATCHDOG_TIMEOUT}s without output, killed)",
                        attempt=attempt,
                    )
                    _attempt_sig = _failure_signature("stalled", returncode, output)
                elif returncode != 0:
                    ui.round_failed(round_num, returncode)
                    _save_subprocess_diagnostic(
                        run_dir, round_num, cmd, output,
                        reason=f"crashed (exit code {returncode})",
                        attempt=attempt,
                    )
                    _attempt_sig = _failure_signature("crashed", returncode, output)
                else:
                    # Subprocess exited OK — check for actual progress
                    prev_checked = checked
                    prev_unchecked = unchecked
                    unchecked = _count_unchecked(improvements_path)
                    checked = _count_checked(improvements_path)
                    ui.progress_summary(checked, unchecked)

                    # --- Adversarial review verdict routing ---
                    # SPEC § "Adversarial round review (Phase 3.6)":
                    # The agent writes review_round_N.md during Phase 3.6.
                    # The orchestrator reads it and routes the verdict.
                    review_verdict, review_findings = _check_review_verdict(
                        run_dir, round_num
                    )
                    if review_verdict in ("BLOCKED", "CHANGES REQUESTED"):
                        # SPEC § "Adversarial round review (Phase 3.6)":
                        # both verdicts auto-retry to fix HIGH + MEDIUM
                        # findings.  The operator never arbitrates manually —
                        # the deterministic-loop guard below caps runaway
                        # retries.  BLOCKED differs from CHANGES REQUESTED
                        # only in severity volume (3+ HIGH or regression-risk
                        # tag vs 1-2 HIGH), but the auto-fix protocol is the
                        # same: feed the findings back to the next attempt
                        # under a REVIEW: header.
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
                            reason=reason,
                            attempt=attempt,
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
                        _attempt_sig = _failure_signature(
                            "no-progress:REVIEW", returncode, review_findings
                        )
                        # Fall through to retry registration — the next attempt
                        # sees the REVIEW: prefix and addresses the findings.
                        # Skip the normal zero-progress checks for this attempt.
                        if _attempt_sig:
                            _failure_signatures.append(_attempt_sig)
                            if _is_circuit_breaker_tripped(_failure_signatures):
                                ui.warn(
                                    f"Deterministic failure loop detected after "
                                    f"{MAX_IDENTICAL_FAILURES} identical review failures."
                                )
                                return
                            continue  # next attempt

                    # --- Read SDK subtype (authoritative termination signal) ---
                    # SPEC § "Authoritative termination signal from the SDK":
                    # the subtype written by _run_single_round_body is the
                    # single source of truth for WHY the agent stopped.
                    _subtype_path = run_dir / f"agent_subtype_round_{round_num}.txt"
                    _agent_subtype: str | None = None
                    if _subtype_path.is_file():
                        _agent_subtype = _subtype_path.read_text().strip() or None

                    # --- Zero-progress detection ---
                    # 1. Check if improvements.md is byte-identical to pre-round snapshot
                    imp_after = improvements_path.read_bytes() if improvements_path.is_file() else b""
                    imp_unchanged = (imp_after == imp_snapshot_before)

                    # 2. Check if agent committed without COMMIT_MSG (fallback commit).
                    #
                    # The flag fires ONLY when THIS attempt produced a new
                    # HEAD whose message matches the fallback template.
                    # If HEAD didn't move during the attempt (nothing to
                    # commit because earlier attempts already did the
                    # work), the flag stays False — a stale fallback
                    # commit from a prior attempt is not THIS attempt's
                    # bug.
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
                                cwd=str(project_dir), capture_output=True, text=True, timeout=10,
                            )
                            if git_log_result.returncode == 0:
                                last_commit_msg = git_log_result.stdout.strip()
                                if last_commit_msg == f"chore(evolve): round {round_num}":
                                    no_commit_msg = True
                    except (subprocess.TimeoutExpired, FileNotFoundError):
                        pass

                    # 3. Memory-wipe sanity gate: detect silent wipes of
                    #    memory.md.  A >50% shrink without an explicit
                    #    "memory: compaction" line in the commit message
                    #    is treated as a wipe and triggers a retry.  This
                    #    catches agents that "compact" memory.md by
                    #    emptying sections they couldn't read.
                    mem_size_after = memory_path.stat().st_size if memory_path.is_file() else 0
                    memory_wiped = False
                    if (
                        mem_size_before > 0
                        and mem_size_after < mem_size_before * _MEMORY_WIPE_THRESHOLD
                    ):
                        commit_body = ""
                        try:
                            git_body_result = subprocess.run(
                                ["git", "log", "-1", "--format=%B"],
                                cwd=str(project_dir), capture_output=True, text=True, timeout=10,
                            )
                            if git_body_result.returncode == 0:
                                commit_body = git_body_result.stdout
                        except (subprocess.TimeoutExpired, FileNotFoundError):
                            # Can't read commit body — treat the shrink as
                            # a wipe to stay on the safe side.
                            commit_body = ""
                        if _MEMORY_COMPACTION_MARKER not in commit_body:
                            memory_wiped = True

                    # 4. Backlog discipline rule 1: detect "new [ ] item added
                    #    while queue non-empty".  See SPEC.md § "Backlog
                    #    discipline" rule 1.  We only check when improvements.md
                    #    actually changed (otherwise imp_unchanged path takes
                    #    over) and run the comparison on the snapshotted
                    #    pre-round bytes vs the current file.
                    backlog_violated = False
                    backlog_new_items: list[str] = []
                    if not imp_unchanged:
                        try:
                            pre_text = imp_snapshot_before.decode(
                                "utf-8", errors="replace"
                            )
                            post_text = imp_after.decode(
                                "utf-8", errors="replace"
                            )
                            backlog_violated, backlog_new_items = (
                                _detect_backlog_violation(pre_text, post_text)
                            )
                        except Exception as e:  # pragma: no cover — defensive
                            _probe_warn(f"backlog-violation check skipped: {e}")

                    # "Scope creep" detection: rebuild + implement in one round.
                    #
                    # When improvements.md has new ``[ ]`` items added AND
                    # the round's commit also touched non-improvements
                    # files (code / tests / docs), the agent mixed two
                    # round kinds into one — drafting and implementation.
                    # This is the pattern the operator flagged as "evolve
                    # enchaîne les US sans lancer de nouveau round": the
                    # Phase 2 rebuild instruction used to say "your round
                    # target becomes the FIRST of the newly rebuilt items"
                    # which permitted the mix.  It's now forbidden — a
                    # rebuild round commits only the improvements.md
                    # change, and the next round's fresh agent picks up
                    # the first new item.
                    scope_creep = False
                    scope_creep_other_files: list[str] = []
                    if backlog_new_items:
                        try:
                            import subprocess as _sp
                            diff_files = _sp.run(
                                ["git", "diff-tree", "--no-commit-id",
                                 "--name-only", "-r", "HEAD"],
                                cwd=str(project_dir),
                                capture_output=True,
                                text=True,
                                timeout=10,
                            )
                            if diff_files.returncode == 0:
                                touched = [
                                    ln.strip()
                                    for ln in diff_files.stdout.splitlines()
                                    if ln.strip()
                                ]
                                # Files that count as "implementation"
                                # changes — anything not under runs/
                                # state (improvements.md, memory.md,
                                # conversation_loop_*.md, etc.) and not
                                # the session's own artifacts.
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
                        except (_sp.TimeoutExpired, OSError):
                            pass  # git unavailable → skip the check

                    # Convergence rounds legitimately leave improvements.md
                    # unchanged (all items already checked).  When the agent
                    # wrote CONVERGED, skip the imp_unchanged signal — the
                    # convergence-gate backstop (below) handles premature
                    # convergence independently.
                    converged_written = (run_dir / "CONVERGED").is_file()
                    effective_imp_unchanged = imp_unchanged and not converged_written

                    # "Backlog drained but CONVERGED skipped" case.
                    #
                    # When all ``[ ]`` items are checked off, the agent has
                    # legitimately nothing to implement this round — the
                    # correct next step is Phase 4 convergence (write
                    # CONVERGED after verifying the README claims).  If the
                    # agent stopped short of Phase 4 (e.g. ran out of turns
                    # after the last check-off, or didn't confidently
                    # decide), ``imp_unchanged=True`` and
                    # ``no_commit_msg=True`` both fire and the zero-progress
                    # retry kicks in — which would force the agent to "fix"
                    # a non-bug, wasting the retry budget.
                    #
                    # Detect the drained-but-not-converged state and route
                    # to a dedicated diagnostic so the retry prompt nudges
                    # the agent straight to Phase 4 instead of treating
                    # the round as a no-progress bug.
                    unchecked_remaining = _count_unchecked(improvements_path)
                    backlog_drained_no_converged = (
                        unchecked_remaining == 0
                        and imp_unchanged
                        and not converged_written
                    )
                    # Round-level "already-done" escape hatch.  When
                    # THIS attempt's subprocess exited cleanly and
                    # EARLIER attempts in the same round already
                    # landed real work (HEAD moved or imp changed
                    # since round-start), AND this attempt itself
                    # did NOT produce a new fallback commit AND is
                    # NOT a zero-improvements / no-commit-msg bug,
                    # treat the round as succeeded — the agent is
                    # just wrapping up.  This avoids false-positive
                    # retries where attempt 3 is idle because
                    # attempt 2 already finished the work.
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
                    # The escape hatch fires when THIS attempt did not
                    # introduce any NEW problems — a new fallback
                    # commit, a memory wipe, a backlog-discipline
                    # violation.  ``imp_unchanged`` is deliberately
                    # excluded from this "new problem" set: that's
                    # precisely the signal this hatch is meant to
                    # neutralise.  When an earlier attempt committed
                    # real work, THIS attempt finding nothing to add
                    # to improvements.md is the round wrapping up,
                    # not a failure.
                    _attempt_had_no_new_issues = not (
                        no_commit_msg or memory_wiped or backlog_violated
                    )
                    if (_round_head_moved or _round_imp_changed) and _attempt_had_no_new_issues:
                        made_progress = True
                        round_succeeded = True
                        break

                    if backlog_drained_no_converged:
                        # Override the default "no progress" framing so the
                        # diagnostic is actionable (agent goes to Phase 4)
                        # rather than punitive (agent must edit something).
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
                        _attempt_sig = _failure_signature(
                            "no-progress:BACKLOG DRAINED",
                            returncode,
                            f"unchecked={unchecked_remaining}",
                        )
                        # Fall through to the retry-registration path at
                        # the end of the attempt loop.  The retry's prompt
                        # will surface this as "BACKLOG DRAINED" and steer
                        # the agent toward Phase 4 on the next attempt.
                        # No need to evaluate the other no-progress signals —
                        # they'd all fire as tautological consequences of
                        # an empty queue + no edits.
                    # Any condition alone triggers zero-progress / memory-wipe / backlog retry
                    elif scope_creep:
                        # Rebuild + implement in one round → dedicated
                        # diagnostic that routes the retry to split the
                        # work.  Prefix recognised by build_prompt to
                        # emit a tailored "CRITICAL — Scope creep"
                        # section on the next attempt.
                        creep_summary = ", ".join(
                            scope_creep_other_files[:5]
                        )
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
                        _attempt_sig = _failure_signature(
                            "no-progress:SCOPE CREEP",
                            returncode,
                            f"new={len(backlog_new_items)}|other={len(scope_creep_other_files)}",
                        )
                    elif no_commit_msg or effective_imp_unchanged or memory_wiped or backlog_violated:
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
                        # Diagnostic prefix priority: memory-wipe > backlog >
                        # subtype-based > no-progress, so agent.py's prompt
                        # builder picks the most specific dedicated header.
                        #
                        # Subtype-based branching per SPEC § "Authoritative
                        # termination signal from the SDK":
                        # - error_max_turns → MAX_TURNS prefix (fix-only retry)
                        # - error_during_execution → SDK ERROR prefix
                        # - success + imp_unchanged → genuine no-work path
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
                        _attempt_sig = _failure_signature(
                            f"no-progress:{prefix}", returncode, reason_str
                        )
                    else:
                        made_progress = (
                            checked != prev_checked
                            or unchecked != prev_unchecked
                            or (convo.is_file() and convo.stat().st_size > convo_size_before)
                        )
                        if made_progress:
                            round_succeeded = True
                            break

                        # Subtype-aware "silent no progress" path.
                        # Per SPEC § "Authoritative termination signal from
                        # the SDK": success + imp_unchanged is a genuine
                        # "agent decided no work needed" signal (e.g.
                        # backlog drained), NOT a turn-budget exhaustion.
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
                            _attempt_sig = _failure_signature(
                                "no-progress:MAX_TURNS", returncode, output
                            )
                        elif _agent_subtype == "error_during_execution":
                            _save_subprocess_diagnostic(
                                run_dir, round_num, cmd, output,
                                reason=(
                                    "SDK ERROR: agent stopped with "
                                    "error_during_execution — check SDK "
                                    "error in output"
                                ),
                                attempt=attempt,
                            )
                            _attempt_sig = _failure_signature(
                                "no-progress:SDK ERROR", returncode, output
                            )
                        else:
                            # No progress — save diagnostic for retry
                            _save_subprocess_diagnostic(
                                run_dir, round_num, cmd, output,
                                reason="no progress (agent ran but changed nothing)",
                                attempt=attempt,
                            )
                            _attempt_sig = _failure_signature(
                                "no-progress:silent", returncode, output
                            )

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

            # Round succeeded — reset the circuit breaker so that a single
            # recovery clears the deterministic-failure counter.
            _failure_signatures.clear()

            # Fire on_round_end hook for successful round
            fire_hook(hooks, "on_round_end", session=session_name, round_num=round_num, status="success")

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
                run_dir, round_num
            )
            _usage_state = build_usage_state(
                _usage_total, _usage_cost, _usage_rounds
            )

            # Update state.json after every round
            _write_state_json(
                run_dir=run_dir,
                project_dir=project_dir,
                round_num=round_num,
                max_rounds=max_rounds,
                phase="improvement",
                status="running",
                improvements_path=improvements_path,
                check_passed=_check_passed,
                check_tests=_check_tests,
                check_duration_s=_check_duration,
                started_at=_started_at,
                usage=_usage_state,
            )

            # Budget enforcement — pause session if cost exceeds --max-cost
            if max_cost is not None and _usage_cost is not None:
                if _usage_cost >= max_cost:
                    _probe_warn(
                        f"budget reached: "
                        f"{format_cost(_usage_cost)} / {format_cost(max_cost)}"
                    )
                    ui.budget_reached(
                        round_num, max_cost, _usage_cost
                    )
                    # Update state with budget_reached status
                    _write_state_json(
                        run_dir=run_dir,
                        project_dir=project_dir,
                        round_num=round_num,
                        max_rounds=max_rounds,
                        phase="improvement",
                        status="budget_reached",
                        improvements_path=improvements_path,
                        check_passed=_check_passed,
                        check_tests=_check_tests,
                        check_duration_s=_check_duration,
                        started_at=_started_at,
                        usage=_usage_state,
                    )
                    fire_hook(
                        hooks, "on_error",
                        session=session_name,
                        round_num=round_num,
                        status="budget_reached",
                    )
                    sys.exit(1)

            # Clean up diagnostic file on success (no longer relevant)
            error_log = run_dir / f"subprocess_error_round_{round_num}.txt"
            if error_log.is_file():
                error_log.unlink()

            # --- FILE TOO LARGE detection (SPEC § "Hard rule: source files
            # MUST NOT exceed 500 lines").  Advisory only — writes a
            # diagnostic for the NEXT round so the agent auto-targets
            # the oversized file for splitting.  Same prefix-chain
            # pattern as BACKLOG VIOLATION / SCOPE CREEP.
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

            # --- Memory curation (Mira) — SPEC § "Dedicated memory
            # curation (Mira)".  Between rounds, after post-check,
            # before the next round's pre-check.  Runs only when
            # memory.md > 300 lines OR round is a multiple of 10.
            _run_curation_pass(
                project_dir, run_dir, round_num,
                improvements_path, spec, ui,
            )

            # --- SPEC archival (Sid) — SPEC § "SPEC archival (Sid)".
            # Between rounds, after post-check + memory curation,
            # before the next round's pre-check.  Runs only when
            # SPEC.md > 2000 lines OR round is a multiple of 20.
            _run_spec_archival_pass(
                project_dir, run_dir, round_num, spec, ui,
            )

            # --- Structural change detection (SPEC § "Structural change
            # self-detection" — orchestrator-side protocol).  The agent
            # writes RESTART_REQUIRED when it detects a structural commit.
            # We check AFTER state.json + budget, BEFORE convergence.
            # --forever does NOT bypass — structural changes always pause.
            #
            # Scope: RESTART_REQUIRED is a *self-evolution* concept — it
            # only matters when evolve is evolving its own source tree
            # (the running orchestrator's imports become stale on rename
            # / __init__.py edits / entry-point moves).  When evolve is
            # evolving a third-party project, the target's structural
            # changes don't touch the orchestrator's module layout, so
            # restarting the orchestrator would be theatre.  Ignore the
            # marker in that case — the marker stays on disk as audit
            # trail, but we do not exit.
            restart_marker = _parse_restart_required(run_dir)
            if restart_marker is not None and not _is_self_evolving(project_dir):
                _probe(
                    f"RESTART_REQUIRED marker present but project is not "
                    f"evolve itself — ignoring (target's structural change "
                    f"does not affect the orchestrator)"
                )
                restart_marker = None
            if restart_marker is not None:
                _probe_warn(f"RESTART_REQUIRED detected: {restart_marker.get('reason', '?')}")

                # Fire on_structural_change hook with marker fields as env vars
                structural_env = {
                    "EVOLVE_STRUCTURAL_REASON": restart_marker.get("reason", ""),
                    "EVOLVE_STRUCTURAL_VERIFY": restart_marker.get("verify", ""),
                    "EVOLVE_STRUCTURAL_RESUME": restart_marker.get("resume", ""),
                    "EVOLVE_STRUCTURAL_ROUND": restart_marker.get("round", ""),
                    "EVOLVE_STRUCTURAL_TIMESTAMP": restart_marker.get("timestamp", ""),
                }
                fire_hook(
                    hooks, "on_structural_change",
                    session=session_name,
                    round_num=round_num,
                    status="structural_change",
                    extra_env=structural_env,
                )

                # Render blocking red panel
                ui.structural_change_required(restart_marker)

                # Exit with code 3 — structural change, manual restart required
                sys.exit(3)

            # Check convergence — requires spec freshness gate
            # (mtime(improvements.md) >= mtime(spec)) AND CONVERGED file
            converged_path = run_dir / "CONVERGED"
            if converged_path.is_file():
                # Verify spec freshness gate — don't mark stale again,
                # just check the mtime relationship
                spec_file = spec or "README.md"
                spec_path = project_dir / spec_file
                if converged_path.is_file() and spec_path.is_file() and improvements_path.is_file():
                    if spec_path.stat().st_mtime > improvements_path.stat().st_mtime:
                        _probe("convergence rejected: spec is newer than improvements.md — removing CONVERGED marker")
                        converged_path.unlink()

                # Convergence-gate orchestrator backstop — re-verify the
                # two documented gates (SPEC.md § "Convergence")
                # independently of the agent's judgment.  Closes the trust
                # gap where Phase 4 criteria are 100% agent-judged today.
                # If either gate fails, CONVERGED is unlinked, a
                # diagnostic is saved with a ``PREMATURE CONVERGED``
                # prefix, and the next round picks it up via
                # ``build_prompt`` → dedicated CRITICAL header.
                _enforce_convergence_backstop(
                    converged_path,
                    improvements_path,
                    spec_path,
                    run_dir,
                    round_num,
                    cmd,
                    output,
                    attempt,
                    ui,
                )
            if converged_path.is_file():
                reason = converged_path.read_text().strip()
                _probe_ok(f"CONVERGED at round {round_num}: {reason[:80]}")
                ui.converged(round_num, reason)

                # Capture convergence frame
                ui.capture_frame("converged")

                # Fire on_converged hook
                fire_hook(hooks, "on_converged", session=session_name, round_num=round_num, status="converged")

                # Update state.json to converged
                _write_state_json(
                    run_dir=run_dir,
                    project_dir=project_dir,
                    round_num=round_num,
                    max_rounds=max_rounds,
                    phase="convergence",
                    status="converged",
                    improvements_path=improvements_path,
                    check_passed=_check_passed,
                    check_tests=_check_tests,
                    check_duration_s=_check_duration,
                    started_at=_started_at,
                    usage=_usage_state,
                )

                # Generate evolution report
                _generate_evolution_report(project_dir, run_dir, max_rounds, round_num, converged=True, capture_frames=capture_frames)

                # Display completion summary panel
                duration_s = time.monotonic() - _rounds_start_time
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

                # Launch party mode — ONLY in --forever mode.
                #
                # Party mode's sole purpose is to draft the next
                # spec proposal (`<spec>_proposal.md`) so the forever
                # loop has something to converge toward in the next
                # cycle.  Without ``--forever``, the session ends on
                # convergence (exit 0); there is no next cycle, so
                # running party mode would be wasted work —
                # potentially costly work (Opus call + multi-turn
                # brainstorming).  Convergence in non-forever mode
                # = stop, cleanly.
                if forever:
                    _run_party_mode(project_dir, run_dir, ui, spec=spec)
                else:
                    _probe(
                        "convergence reached — skipping party mode "
                        "(only runs in --forever mode; without forever, "
                        "convergence = stop)"
                    )

                if forever:
                    # Auto-merge the spec proposal into the spec file, then
                    # restart. README.md is never written by the evolution
                    # loop — see SPEC.md § "README as a user-level summary".
                    adoption_result = _forever_restart(
                        project_dir, run_dir, improvements_path, ui, spec=spec
                    )
                    # Backwards-compat: historical _forever_restart returned
                    # None; current signature returns (spec_adopted, _).
                    if isinstance(adoption_result, tuple):
                        spec_adopted, _ = adoption_result
                    else:
                        spec_adopted = False

                    # Create a new session directory for the next cycle
                    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                    run_dir = _runs_base(project_dir) / timestamp
                    run_dir.mkdir(parents=True, exist_ok=True)
                    # Update TUI with new run_dir for frame capture
                    if capture_frames:
                        ui = get_tui(run_dir=run_dir, capture_frames=capture_frames)
                    ui.run_dir_info(str(run_dir))

                    # Git commit the proposal adoption + reset. When --spec
                    # differs from README.md and the spec proposal was
                    # adopted, use a focused feat(spec) commit; otherwise
                    # (no --spec flag, or nothing adopted) fall back to the
                    # legacy chore message.
                    spec_file_for_msg = spec or "README.md"
                    if spec_file_for_msg != "README.md" and spec_adopted:
                        spec_stem_msg = Path(spec_file_for_msg).stem
                        spec_suffix_msg = Path(spec_file_for_msg).suffix or ".md"
                        proposal_name_msg = f"{spec_stem_msg}_proposal{spec_suffix_msg}"
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
                    _git_commit(project_dir, commit_msg, ui)

                    # Restart from round 1 via the outer while loop
                    start_round = 1
                    break  # break out of for loop, continue while loop

                sys.exit(0)
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


def run_single_round(
    project_dir: Path,
    round_num: int,
    check_cmd: str | None = None,
    allow_installs: bool = False,
    timeout: int = 20,
    run_dir: Path | None = None,
    model: str = "claude-opus-4-6",
    spec: str | None = None,
    yolo: bool | None = None,
    effort: str | None = "medium",
) -> None:
    """Execute a single evolution round (called as subprocess).

    Runs the check command, invokes the agent, commits changes, and
    re-runs the check to verify fixes.  This function is the entry
    point for each subprocess spawned by ``_run_rounds``.

    Args:
        project_dir: Root directory of the project.
        round_num: Current evolution round number.
        check_cmd: Shell command to verify the project.
        allow_installs: If True, allow improvements requiring new packages.
        timeout: Timeout for the check command in seconds.
        run_dir: Session directory for round artifacts.
        model: Claude model identifier to use.
        spec: Path to the spec file relative to project_dir (default: README.md).
        yolo: Deprecated alias for *allow_installs*. Will be removed in a future version.
    """
    if yolo is not None:
        allow_installs = yolo
    from evolve.agent import analyze_and_fix
    import evolve.agent as _agent_mod
    _agent_mod.MODEL = model
    _agent_mod.EFFORT = effort

    rdir = run_dir or _runs_base(project_dir)
    rdir.mkdir(parents=True, exist_ok=True)
    improvements_path = _runs_base(project_dir) / "improvements.md"
    ui = get_tui()

    from evolve.agent import MAX_TURNS as _MAX_TURNS
    _probe(
        f"round {round_num} starting — project={project_dir.name}, "
        f"model={model}, effort={effort}, max_turns={_MAX_TURNS}"
    )

    # Round-wide heartbeat.  The parent orchestrator watches this
    # subprocess's stdout with a silence-based watchdog
    # (``_run_monitored_subprocess``, ``WATCHDOG_TIMEOUT``=120s).  Any
    # part of the round that buffers output — pre-check running pytest
    # silently, agent tool calls using ``| tail`` or ``> /dev/null``,
    # long agent thinking between streaming messages, git operations
    # on large repos — would trigger SIGKILL before the round can
    # finish.  A background thread printing an alive-line every 30s
    # keeps the watchdog satisfied while real work proceeds.  Total
    # round duration is still bounded by budget/round/cost limits at
    # the orchestrator level, not by this watchdog.
    _round_heartbeat_stop = threading.Event()
    _round_start_time = time.monotonic()

    def _round_heartbeat():
        while not _round_heartbeat_stop.wait(30):
            elapsed = int(time.monotonic() - _round_start_time)
            # Only the elapsed wall clock — the watchdog's silence
            # threshold is a different axis (it measures *stdout
            # silence*, and this heartbeat line is precisely what
            # keeps it quiet).  Mixing the two in one message
            # implies a relationship that doesn't exist.  The
            # watchdog config is announced once at orchestrator
            # startup instead.
            _probe(f"round {round_num} alive — {elapsed}s elapsed")

    _round_hb_thread = threading.Thread(target=_round_heartbeat, daemon=True)
    _round_hb_thread.start()
    try:
        _run_single_round_body(
            project_dir=project_dir,
            round_num=round_num,
            check_cmd=check_cmd,
            allow_installs=allow_installs,
            timeout=timeout,
            rdir=rdir,
            improvements_path=improvements_path,
            ui=ui,
            spec=spec,
        )
    finally:
        _round_heartbeat_stop.set()


def _run_single_round_body(
    *,
    project_dir: Path,
    round_num: int,
    check_cmd: str | None,
    allow_installs: bool,
    timeout: int,
    rdir: Path,
    improvements_path: Path,
    ui: TUIProtocol,
    spec: str | None,
) -> None:
    """Body of ``run_single_round`` — extracted so the caller can wrap
    the whole thing in a try/finally around the round-wide heartbeat
    without indenting 100+ lines.
    """
    from evolve.agent import analyze_and_fix  # local import mirrors caller

    # 1. Run check command if provided.  The round-wide heartbeat in
    # ``run_single_round`` keeps the parent watchdog satisfied during
    # silent pre-check runs; the pre-check's own ``timeout`` still
    # bounds the wait.
    check_output = ""
    pre_check_failed = False
    if check_cmd:
        _probe(f"running pre-check: {check_cmd} (max {timeout}s)")
        ui.check_result("check", check_cmd, passed=None)
        try:
            result = subprocess.run(
                check_cmd, shell=True, cwd=str(project_dir),
                capture_output=True, text=True, timeout=timeout,
            )
            check_output = f"Exit code: {result.returncode}\n"
            if result.stdout:
                check_output += f"stdout:\n{result.stdout[-2000:]}\n"
            if result.stderr:
                check_output += f"stderr:\n{result.stderr[-2000:]}\n"
            ok = result.returncode == 0
            pre_check_failed = not ok
            ui.check_result("check", check_cmd, passed=ok)
            if ok:
                _probe_ok(f"pre-check PASSED (exit {result.returncode})")
            else:
                _probe_warn(f"pre-check FAILED (exit {result.returncode})")
        except subprocess.TimeoutExpired:
            check_output = f"TIMEOUT after {timeout}s"
            pre_check_failed = True
            ui.check_result("check", check_cmd, timeout=True)
            _probe_warn(f"pre-check TIMEOUT after {timeout}s (hit ceiling)")
    else:
        ui.no_check()
        _probe("no check command configured")

    # 2. Pick the right call for this round's state.
    #
    # Multi-call round architecture (SPEC § "Multi-call round
    # architecture"):
    #
    # - Pre-check FAILED → ``implement`` call (Phase 1 fixes the check
    #   regardless of backlog state — drafting new US items on top of a
    #   broken test suite is non-sensical and defeats the whole point of
    #   Phase 1.  Even when the backlog is drained, fixing the failing
    #   check is the round's first job).
    # - Backlog has ≥1 unchecked ``[ ]`` item → ``implement`` call
    #   (Amelia — Opus, full ``analyze_and_fix``).
    # - Backlog drained AND pre-check passing → ``draft`` call
    #   (Winston + John — Opus, narrow scope, writes ONE new US).
    #
    # The orchestrator picks; the agent doesn't have to decide.
    current = _get_current_improvement(improvements_path, allow_installs=allow_installs)
    agent_subtype: str | None = None
    round_kind = "implement" if (current or pre_check_failed) else "draft"
    if round_kind == "implement":
        if not current and pre_check_failed:
            _probe(
                "pre-check failed with drained backlog — routing to implement "
                "(Phase 1 fixes the check before any drafting)"
            )
        _probe(f"invoking implement agent — target: {current}")
        ui.agent_working()
        from evolve.agent import analyze_and_fix as _analyze_and_fix
        agent_subtype = _analyze_and_fix(
            project_dir=project_dir,
            check_output=check_output,
            check_cmd=check_cmd,
            allow_installs=allow_installs,
            round_num=round_num,
            run_dir=rdir,
            spec=spec,
            check_timeout=timeout,
        )
        _probe(f"implement agent finished (subtype={agent_subtype})")

        # Persist the subtype so the parent orchestrator (_run_rounds) can
        # branch retry logic on the authoritative SDK signal rather than
        # relying solely on indirect tells (missing COMMIT_MSG, imp_unchanged).
        # See SPEC § "Authoritative termination signal from the SDK".
        if agent_subtype:
            subtype_path = rdir / f"agent_subtype_round_{round_num}.txt"
            subtype_path.write_text(agent_subtype)
    else:
        _probe("backlog drained — invoking draft agent (Winston + John, Opus low)")
        ui.agent_working()
        from evolve.agent import run_draft_agent as _run_draft_agent
        _run_draft_agent(
            project_dir=project_dir,
            run_dir=rdir,
            spec=spec,
        )
        _probe("draft agent finished")

    # 3. Git commit + push
    commit_msg_path = rdir / "COMMIT_MSG"
    if commit_msg_path.is_file():
        msg = commit_msg_path.read_text().strip()
        commit_msg_path.unlink()
    else:
        new_current = _get_current_improvement(improvements_path, allow_installs=allow_installs)
        if current and new_current != current:
            msg = f"feat(evolve): ✓ {current}"
        else:
            msg = f"chore(evolve): round {round_num}"
    _probe(f"git commit: {msg[:80]}")
    _git_commit(project_dir, msg, ui)

    # 4. Re-run check after fixes
    if check_cmd:
        _probe(f"running post-check: {check_cmd} (max {timeout}s)")
        ui.check_result("verify", check_cmd, passed=None)
        try:
            result = subprocess.run(
                check_cmd, shell=True, cwd=str(project_dir),
                capture_output=True, text=True, timeout=timeout,
            )
            ok = result.returncode == 0
            ui.check_result("verify", check_cmd, passed=ok)
            if ok:
                _probe_ok(f"post-check PASSED (exit {result.returncode})")
            else:
                _probe_warn(f"post-check FAILED (exit {result.returncode})")

            probe_path = rdir / f"check_round_{round_num}.txt"
            with open(probe_path, "w") as f:
                f.write(f"Round {round_num} post-fix check: {'PASS' if ok else 'FAIL'}\n")
                f.write(f"Command: {check_cmd}\n")
                f.write(f"Exit code: {result.returncode}\n")
                if result.stdout:
                    f.write(f"\nstdout:\n{result.stdout[-2000:]}\n")
                if result.stderr:
                    f.write(f"\nstderr:\n{result.stderr[-2000:]}\n")
        except subprocess.TimeoutExpired:
            ui.check_result("verify", check_cmd, timeout=True)
            _probe_warn(f"post-check TIMEOUT after {timeout}s (hit ceiling)")

    # 5. Run the dedicated review agent (Zara — Opus low effort).
    #
    # Multi-call round architecture: review is a separate SDK call
    # after the implement commit + post-check.  Writes
    # ``review_round_N.md`` which the parent orchestrator's
    # ``_check_review_verdict`` parses and routes to retry / proceed.
    #
    # SKIPPED on draft rounds.  Draft rounds produce only an
    # ``improvements.md`` text edit (a new ``[ ]`` US item) and a
    # ``COMMIT_MSG`` — there is no code/test surface for adversarial
    # code review, and the draft agent (Winston + John) already runs
    # an internal architect + PM dual-pass on the US before writing
    # it.  Running Zara on top of a draft round routinely flagged
    # wording-quality "HIGH findings" that fed the auto-retry loop
    # with non-actionable churn.
    # See SPEC § "Adversarial round review (Phase 3.6)".
    if round_kind == "implement":
        try:
            from evolve.agent import run_review_agent as _run_review_agent
            _probe("invoking review agent (Zara, Opus low)")
            _run_review_agent(
                project_dir=project_dir,
                run_dir=rdir,
                round_num=round_num,
                spec=spec,
            )
            _probe("review agent finished")
        except Exception as exc:
            # Review failures should not sink the round — log and continue.
            # The verdict parser treats a missing/malformed file as
            # ``verdict=None``, which falls through the normal flow.
            _probe_warn(f"review agent error: {exc}")
    else:
        _probe("draft round — skipping review (Zara reviews implement rounds only)")

    _probe_ok(f"round {round_num} complete")


def run_dry_run(
    project_dir: Path,
    check_cmd: str | None = None,
    timeout: int = 20,
    model: str = "claude-opus-4-6",
    spec: str | None = None,
    effort: str | None = "medium",
) -> None:
    """Run a read-only analysis of the project without modifying files.

    Runs the check command (if provided) to see the current state, then
    launches the agent with write-related tools disabled (Edit, Write, Bash
    are disallowed).  The agent analyzes the project using only Read, Grep,
    and Glob, and produces a ``dry_run_report.md`` in the session directory.

    No files in the project are modified and no git commits are created.

    Args:
        project_dir: Root directory of the project being analyzed.
        check_cmd: Shell command to verify the project (run read-only).
        timeout: Timeout in seconds for the check command.
        model: Claude model identifier to use.
        spec: Path to the spec file relative to project_dir (default: README.md).
    """
    ui = get_tui()

    _probe(f"dry-run starting — project={project_dir.name}")

    # Startup-time stale-README advisory (SPEC § "Stale-README pre-flight
    # check") — pure observability, runs once at startup.
    _emit_stale_readme_advisory(project_dir, spec, ui)

    # Auto-detect check command if not provided
    if check_cmd is None:
        detected = _auto_detect_check(project_dir)
        if detected:
            ui.info(f"  Auto-detected check command: {detected}")
            check_cmd = detected

    # Create timestamped run directory
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = _runs_base(project_dir) / timestamp
    run_dir.mkdir(parents=True, exist_ok=True)
    ui.run_dir_info(str(run_dir))
    ui.info("  Mode: DRY RUN (read-only analysis, no file changes)")
    _probe(f"dry-run session: {run_dir}")

    # 1. Run check command if provided
    check_output = ""
    if check_cmd:
        ui.check_result("check", check_cmd, passed=None)
        try:
            result = subprocess.run(
                check_cmd, shell=True, cwd=str(project_dir),
                capture_output=True, text=True, timeout=timeout,
            )
            check_output = f"Exit code: {result.returncode}\n"
            if result.stdout:
                check_output += f"stdout:\n{result.stdout[-2000:]}\n"
            if result.stderr:
                check_output += f"stderr:\n{result.stderr[-2000:]}\n"
            ok = result.returncode == 0
            ui.check_result("check", check_cmd, passed=ok)
        except subprocess.TimeoutExpired:
            check_output = f"TIMEOUT after {timeout}s"
            ui.check_result("check", check_cmd, timeout=True)
    else:
        ui.no_check()

    # 2. Launch agent in dry-run mode (restricted tools)
    from evolve.agent import run_dry_run_agent
    import evolve.agent as _agent_mod
    _agent_mod.MODEL = model
    _agent_mod.EFFORT = effort

    ui.agent_working()
    run_dry_run_agent(
        project_dir=project_dir,
        check_output=check_output,
        check_cmd=check_cmd,
        run_dir=run_dir,
        spec=spec,
    )

    # 3. Report location
    report_path = run_dir / "dry_run_report.md"
    if report_path.is_file():
        ui.info(f"  Dry-run report: {report_path}")
    else:
        ui.warn("No dry_run_report.md produced by the agent")


def run_validate(
    project_dir: Path,
    check_cmd: str | None = None,
    timeout: int = 20,
    model: str = "claude-opus-4-6",
    spec: str | None = None,
    effort: str | None = "medium",
) -> int:
    """Run spec compliance validation without modifying project files.

    Launches the agent in read-only mode with a validation-focused prompt.
    The agent checks every README claim against the code and produces a
    ``validate_report.md`` with pass/fail per claim.

    Args:
        project_dir: Root directory of the project being validated.
        check_cmd: Shell command to verify the project (run read-only).
        timeout: Timeout in seconds for the check command.
        model: Claude model identifier to use.
        spec: Path to the spec file relative to project_dir (default: README.md).

    Returns:
        Exit code: 0 if all claims pass, 1 if any fail, 2 on error.
    """
    ui = get_tui()

    _probe(f"validate starting — project={project_dir.name}")

    # Startup-time stale-README advisory (SPEC § "Stale-README pre-flight
    # check") — pure observability, runs once at startup.
    _emit_stale_readme_advisory(project_dir, spec, ui)

    # Auto-detect check command if not provided
    if check_cmd is None:
        detected = _auto_detect_check(project_dir)
        if detected:
            ui.info(f"  Auto-detected check command: {detected}")
            check_cmd = detected

    # Create timestamped run directory
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = _runs_base(project_dir) / timestamp
    run_dir.mkdir(parents=True, exist_ok=True)
    ui.run_dir_info(str(run_dir))
    ui.info("  Mode: VALIDATE (spec compliance check, no file changes)")
    _probe(f"validate session: {run_dir}")

    # 1. Run check command if provided
    check_output = ""
    if check_cmd:
        ui.check_result("check", check_cmd, passed=None)
        try:
            result = subprocess.run(
                check_cmd, shell=True, cwd=str(project_dir),
                capture_output=True, text=True, timeout=timeout,
            )
            check_output = f"Exit code: {result.returncode}\n"
            if result.stdout:
                check_output += f"stdout:\n{result.stdout[-2000:]}\n"
            if result.stderr:
                check_output += f"stderr:\n{result.stderr[-2000:]}\n"
            ok = result.returncode == 0
            ui.check_result("check", check_cmd, passed=ok)
        except subprocess.TimeoutExpired:
            check_output = f"TIMEOUT after {timeout}s"
            ui.check_result("check", check_cmd, timeout=True)
    else:
        ui.no_check()

    # 2. Launch agent in validate mode (restricted tools)
    from evolve.agent import run_validate_agent
    import evolve.agent as _agent_mod
    _agent_mod.MODEL = model
    _agent_mod.EFFORT = effort

    ui.agent_working()
    run_validate_agent(
        project_dir=project_dir,
        check_output=check_output,
        check_cmd=check_cmd,
        run_dir=run_dir,
        spec=spec,
    )

    # 3. Parse the validate report for pass/fail determination
    report_path = run_dir / "validate_report.md"
    if not report_path.is_file():
        ui.warn("No validate_report.md produced by the agent")
        return 2

    ui.info(f"  Validation report: {report_path}")

    report_text = report_path.read_text(errors="replace")
    # Count ✅ and ❌ markers
    passed = len(re.findall(r"✅", report_text))
    failed = len(re.findall(r"❌", report_text))

    if failed > 0:
        ui.info(f"  Result: FAIL — {passed} passed, {failed} failed")
        _probe(f"validate result: FAIL ({passed} passed, {failed} failed)")
        return 1
    elif passed > 0:
        ui.info(f"  Result: PASS — {passed} claims validated")
        _probe(f"validate result: PASS ({passed} claims validated)")
        return 0
    else:
        # No markers found — likely an error in report generation
        ui.warn("Could not determine pass/fail from validate_report.md")
        return 2


def run_diff(
    project_dir: Path,
    spec: str | None = None,
    model: str = "claude-opus-4-6",
    effort: str | None = "low",
) -> int:
    """Run the ``evolve diff`` one-shot subcommand.

    Launches the agent in read-only mode with ``--effort low`` and a
    gap-detection prompt.  Does NOT run the check command.  Produces
    ``diff_report.md`` with per-section compliance and overall percentage.

    Args:
        project_dir: Root directory of the project.
        spec: Path to the spec file relative to project_dir (default: README.md).
        model: Claude model identifier to use.
        effort: Reasoning effort level (default: ``"low"``).

    Returns:
        Exit code: 0 if all major sections present, 1 if gaps found, 2 on error.
    """
    ui = get_tui()

    _probe(f"diff starting — project={project_dir.name}")

    # Validate spec file exists if --spec is set
    if spec:
        spec_path = project_dir / spec
        if not spec_path.is_file():
            ui.warn(f"Spec file not found: {spec_path}")
            return 2

    # Create timestamped run directory
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = _runs_base(project_dir) / timestamp
    run_dir.mkdir(parents=True, exist_ok=True)
    ui.run_dir_info(str(run_dir))
    ui.info("  Mode: DIFF (lightweight gap detection, no file changes)")
    _probe(f"diff session: {run_dir}")

    # Launch agent in diff mode (restricted tools, effort low)
    from evolve.agent import run_diff_agent
    import evolve.agent as _agent_mod
    _agent_mod.MODEL = model
    _agent_mod.EFFORT = effort

    ui.agent_working()
    run_diff_agent(
        project_dir=project_dir,
        run_dir=run_dir,
        spec=spec,
    )

    # Parse the diff report for pass/fail determination
    report_path = run_dir / "diff_report.md"
    if not report_path.is_file():
        ui.warn("No diff_report.md produced by the agent")
        return 2

    ui.info(f"  Diff report: {report_path}")

    report_text = report_path.read_text(errors="replace")
    # Count ✅ and ❌ markers
    passed = len(re.findall(r"✅", report_text))
    failed = len(re.findall(r"❌", report_text))

    if failed > 0:
        ui.info(f"  Result: GAPS FOUND — {passed} present, {failed} missing")
        _probe(f"diff result: GAPS ({passed} present, {failed} missing)")
        return 1
    elif passed > 0:
        ui.info(f"  Result: COMPLIANT — {passed} sections present")
        _probe(f"diff result: COMPLIANT ({passed} sections present)")
        return 0
    else:
        ui.warn("Could not determine compliance from diff_report.md")
        return 2


def run_sync_readme(
    project_dir: Path,
    spec: str | None = None,
    apply: bool = False,
    model: str = "claude-opus-4-6",
    effort: str | None = "medium",
) -> int:
    """Run the ``evolve sync-readme`` one-shot subcommand.

    Refreshes README.md so it reflects the current spec, preserving the
    README's tutorial voice (brevity, examples, links to the spec for
    internals).  Per SPEC.md § "evolve sync-readme":

    - Default mode writes ``<project>/README_proposal.md`` for human
      review and does NOT touch ``README.md``.
    - ``apply=True`` writes directly to ``README.md`` and creates a
      ``docs(readme): sync to spec`` git commit.

    Refuses to run when the spec IS the README — i.e. ``spec`` is
    ``None`` or equals ``"README.md"`` — because there is nothing to
    sync against.  In that case the function emits a ``ui.info``
    explaining the no-op and returns exit code 1.

    Args:
        project_dir: Root directory of the project.
        spec: Path to the spec file relative to ``project_dir``.
        apply: When True, write directly to README.md and commit.
        model: Claude model identifier to use.

    Returns:
        Exit code: 0 (proposal written / applied), 1 (already in sync
        OR spec IS README), 2 (error — spec missing, agent failure,
        etc.).
    """
    ui = get_tui()

    _probe(f"sync-readme starting — project={project_dir.name}")

    # Refuse when the spec IS the README — no sync to perform.
    if spec is None or spec == "README.md":
        ui.info(
            "  sync-readme is a no-op when --spec is unset or equals "
            "README.md (README is the spec)"
        )
        _probe("sync-readme: no-op (README is the spec)")
        return 1

    # Validate spec exists.
    spec_path = project_dir / spec
    if not spec_path.is_file():
        ui.error(f"ERROR: spec file not found: {spec_path}")
        _probe(f"sync-readme: ERROR — spec missing")
        return 2

    # Create timestamped run directory for the conversation log + sentinel.
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = _runs_base(project_dir) / timestamp
    run_dir.mkdir(parents=True, exist_ok=True)
    ui.run_dir_info(str(run_dir))
    mode_label = "APPLY (will commit README.md)" if apply else "PROPOSAL (writes README_proposal.md)"
    ui.info(f"  Mode: SYNC-README — {mode_label}")
    _probe(f"sync-readme session: {run_dir} (apply={apply})")

    # Snapshot README.md mtime before agent runs (used to detect whether
    # apply mode actually overwrote the file).
    readme_path = project_dir / "README.md"
    readme_mtime_before = readme_path.stat().st_mtime if readme_path.is_file() else None

    # Launch agent.
    from evolve.agent import run_sync_readme_agent, SYNC_README_NO_CHANGES_SENTINEL
    import evolve.agent as _agent_mod
    _agent_mod.MODEL = model
    _agent_mod.EFFORT = effort

    ui.agent_working()
    try:
        run_sync_readme_agent(
            project_dir=project_dir,
            run_dir=run_dir,
            spec=spec,
            apply=apply,
        )
    except Exception as e:
        ui.error(f"sync-readme agent failed: {e}")
        _probe(f"sync-readme: ERROR — agent exception {e}")
        return 2

    # Inspect filesystem outputs to compute exit code.
    sentinel = run_dir / SYNC_README_NO_CHANGES_SENTINEL
    proposal = project_dir / "README_proposal.md"

    if sentinel.is_file():
        ui.info("  README already in sync — no proposal written")
        _probe("sync-readme: no changes needed (exit 1)")
        return 1

    if apply:
        # Agent should have overwritten README.md.  Verify the mtime moved
        # forward (or the file appeared) — if not, treat as error.
        if not readme_path.is_file():
            ui.error("sync-readme apply mode: README.md missing after agent run")
            return 2
        readme_mtime_after = readme_path.stat().st_mtime
        if readme_mtime_before is not None and readme_mtime_after == readme_mtime_before:
            ui.warn("sync-readme apply mode: README.md was not modified")
            return 2
        # Commit the updated README.
        _ensure_git(project_dir, ui=ui)
        _git_commit(project_dir, "docs(readme): sync to spec", ui=ui)
        ui.info(f"  README.md updated and committed")
        _probe("sync-readme: applied + committed (exit 0)")
        return 0

    # Default mode: agent should have written README_proposal.md.
    if proposal.is_file():
        ui.info(f"  README proposal written: {proposal}")
        _probe("sync-readme: proposal written (exit 0)")
        return 0

    ui.warn("sync-readme: agent produced no README_proposal.md and no NO_SYNC_NEEDED sentinel")
    _probe("sync-readme: ERROR — no agent output (exit 2)")
    return 2



# _run_party_mode and _forever_restart extracted to evolve/party.py
# (SPEC.md § "Architecture", migration step 5).
# Re-exported at module level via ``from evolve.party import …`` above
# for backward compatibility with existing callers and test patches.


