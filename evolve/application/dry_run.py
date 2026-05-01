"""Use case: read-only dry-run analysis.

One-shot use case — orchestration bounded context.
"""

from __future__ import annotations

import subprocess
from datetime import datetime
from pathlib import Path


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
    """
    # Lazy imports — preserve ``patch("evolve.orchestrator.X")`` surfaces.
    from evolve.application.run_loop import (
        _auto_detect_check,
        _emit_stale_readme_advisory,
        _probe,
        _runs_base,
        get_tui,
    )

    ui = get_tui()

    _probe(f"dry-run starting — project={project_dir.name}")

    # Startup-time stale-README advisory
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
    from evolve.infrastructure.claude_sdk.oneshot_agents import run_dry_run_agent
    from evolve.infrastructure.claude_sdk import runtime as _runtime
    _runtime.MODEL = model
    _runtime.EFFORT = effort

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
