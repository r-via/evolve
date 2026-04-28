"""Session startup — ``evolve_loop`` entry point.

Extracted from ``evolve/orchestrator.py`` per US-042 to keep the
orchestrator under the SPEC § "Hard rule: source files MUST NOT
exceed 500 lines" cap.

``evolve_loop`` is the public entry point invoked by the CLI
(``evolve.cli.main``).  It performs session-level setup — runs-layout
migration, scaffolding of shared runtime files, hook loading, stale-
README advisory, check-command auto-detection, ``--resume`` /
``--forever`` branching — and then delegates the per-round loop to
``_run_rounds``.

Heavy dependencies on orchestrator internals (``_run_rounds``,
``_scaffold_shared_runtime_files``, ``_probe``, ``_ensure_runs_layout``,
``_RunsLayoutError``, ``_runs_base``, ``_ensure_git``,
``_setup_forever_branch``, ``_emit_stale_readme_advisory``,
``_auto_detect_check``, ``load_hooks``, ``get_tui``,
``WATCHDOG_TIMEOUT``) are lazy-imported via ``from evolve.orchestrator
import ...`` inside the function body to preserve
``patch("evolve.orchestrator.X", ...)`` test surfaces — same lesson
as US-036 / US-037 / US-038 / US-040 / US-041.

Leaf-module invariant: zero top-level imports from
``evolve.(agent|orchestrator|cli)`` — verified by
``tests/test_orchestrator_startup_module.py``.
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path


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
    # Lazy-import every orchestrator-resident symbol via
    # ``evolve.orchestrator`` so ``patch("evolve.orchestrator.X", ...)``
    # test patches continue to intercept after the US-042 split
    # (US-036 / US-037 / US-038 lesson).
    from evolve.orchestrator import (
        WATCHDOG_TIMEOUT,
        _RunsLayoutError,
        _auto_detect_check,
        _emit_stale_readme_advisory,
        _ensure_git,
        _ensure_runs_layout,
        _probe,
        _run_rounds,
        _runs_base,
        _scaffold_shared_runtime_files,
        _setup_forever_branch,
        get_tui,
        load_hooks,
    )

    if yolo is not None:
        allow_installs = yolo

    # SPEC § "Migration from legacy runs/" — ensure .evolve/runs/ layout
    # and migrate legacy runs/ if needed, before any path resolution.
    try:
        _ensure_runs_layout(project_dir)
    except _RunsLayoutError as exc:
        ui_early = get_tui()
        ui_early.error(f"Runs layout error: {exc}")
        sys.exit(2)

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
