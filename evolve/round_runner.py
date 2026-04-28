"""Round runner — subprocess entry point for a single evolution round.

Extracted from ``evolve/orchestrator.py`` per US-038 to keep the
orchestrator under the SPEC § "Hard rule: source files MUST NOT
exceed 500 lines" cap.

The two public entry points:

- ``run_single_round`` — called by ``_run_monitored_subprocess`` via
  the CLI ``_round`` subcommand.  Sets up the round-wide heartbeat
  thread, then delegates to ``_run_single_round_body``.
- ``_run_single_round_body`` — runs the pre-check, picks
  implement/draft, commits, runs the post-check, then runs the
  review agent on implement rounds.

Heavy dependencies on orchestrator internals (``_probe``,
``_runs_base``, ``_get_current_improvement``, ``_git_commit``,
``get_tui``) are lazy-imported via ``from evolve.orchestrator import
...`` inside each function body to preserve
``patch("evolve.orchestrator.X", ...)`` test surfaces (US-036 /
US-037 lesson).  Agent symbols (``analyze_and_fix``,
``run_draft_agent``, ``run_review_agent``, ``MAX_TURNS``, ``MODEL``,
``EFFORT``) are likewise lazy-imported via ``evolve.agent``.

Leaf-module invariant: zero top-level imports from
``evolve.(agent|orchestrator|cli)`` — verified by
``tests/test_round_runner_module.py``.
"""

from __future__ import annotations

import subprocess
import threading
import time
from pathlib import Path

from evolve.tui import TUIProtocol


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
    from evolve.agent import analyze_and_fix  # noqa: F401  (mirrors original; module import side-effect)
    import evolve.agent as _agent_mod
    _agent_mod.MODEL = model
    _agent_mod.EFFORT = effort

    # Lazy-imports preserve `patch("evolve.orchestrator.X", ...)` interception
    # — see memory.md round-1-of-20260428_081633 (US-036 lesson).
    from evolve.orchestrator import _runs_base, get_tui, _probe

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

    # Lazy-imports of orchestrator-resident deps preserve
    # `patch("evolve.orchestrator.X", ...)` test surfaces — see
    # memory.md round-1-of-20260428_081633 (US-036 lesson).
    from evolve.orchestrator import (
        _get_current_improvement,
        _git_commit,
        _probe,
        _probe_ok,
        _probe_warn,
    )

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
