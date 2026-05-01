"""Session startup — ``evolve_loop`` entry point.

Application layer — orchestration bounded context.
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
    """Orchestrate evolution by launching each round as a subprocess."""
    from evolve.application.run_loop import (
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

    try:
        _ensure_runs_layout(project_dir)
    except _RunsLayoutError as exc:
        ui_early = get_tui()
        ui_early.error(f"Runs layout error: {exc}")
        sys.exit(2)

    improvements_path = _runs_base(project_dir) / "improvements.md"
    _scaffold_shared_runtime_files(project_dir, spec)

    from evolve.infrastructure.claude_sdk.runtime import MAX_TURNS as _MAX_TURNS
    _probe(
        f"evolve_loop starting — project={project_dir.name}, "
        f"max_rounds={max_rounds}, check={check_cmd or '(auto-detect)'}, "
        f"model={model}, effort={effort}, max_turns={_MAX_TURNS}"
    )
    _probe(
        f"timing axes — check_timeout: {timeout}s (pre/post), "
        f"watchdog: {WATCHDOG_TIMEOUT}s silence, heartbeat: every 30s"
    )

    hooks = load_hooks(project_dir)
    if hooks:
        _probe(f"loaded {len(hooks)} hook(s): {', '.join(hooks.keys())}")

    _emit_stale_readme_advisory(project_dir, spec, get_tui())

    if check_cmd is None:
        detected = _auto_detect_check(project_dir)
        if detected:
            ui_early = get_tui()
            ui_early.info(f"  Auto-detected check command: {detected}")
            check_cmd = detected
            _probe(f"auto-detected check command: {detected}")

    start_round = 1

    if forever:
        _probe("forever mode enabled — creating dedicated branch")
        _setup_forever_branch(project_dir)
        max_rounds = 999999

    if resume:
        runs_dir = _runs_base(project_dir)
        if runs_dir.is_dir():
            sessions = sorted(
                [d for d in runs_dir.iterdir() if d.is_dir() and d.name[0].isdigit()],
                reverse=True,
            )
            if sessions:
                run_dir = sessions[0]
                def _convo_sort_key(p: Path) -> int:
                    try:
                        return int(p.stem.rsplit("_", 1)[1])
                    except (ValueError, IndexError):
                        return -1

                convos = sorted(run_dir.glob("conversation_loop_*.md"), key=_convo_sort_key)
                if convos:
                    last = convos[-1].stem
                    try:
                        last_round = int(last.rsplit("_", 1)[1])
                        start_round = last_round + 1
                    except (ValueError, IndexError):
                        pass
                ui = get_tui(run_dir=run_dir, capture_frames=capture_frames)
                ui.run_dir_info(f"{run_dir} (resumed from round {start_round})")
                _ensure_git(project_dir, ui)

                return _run_rounds(
                    project_dir, run_dir, improvements_path, ui,
                    start_round, max_rounds, check_cmd, allow_installs, timeout, model,
                    forever=forever, hooks=hooks, spec=spec,
                    capture_frames=capture_frames,
                    effort=effort,
                    max_cost=max_cost,
                )

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = _runs_base(project_dir) / timestamp
    run_dir.mkdir(parents=True, exist_ok=True)
    ui = get_tui(run_dir=run_dir, capture_frames=capture_frames)
    ui.run_dir_info(str(run_dir))

    _ensure_git(project_dir, ui)

    _run_rounds(
        project_dir, run_dir, improvements_path, ui,
        1, max_rounds, check_cmd, allow_installs, timeout, model,
        forever=forever, hooks=hooks, spec=spec,
        capture_frames=capture_frames,
        effort=effort,
        max_cost=max_cost,
    )
