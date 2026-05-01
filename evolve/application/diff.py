"""Use case: spec-vs-implementation diff.

One-shot use case — orchestration bounded context.
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path


def run_diff(
    project_dir: Path,
    spec: str | None = None,
    model: str = "claude-opus-4-6",
    effort: str | None = "low",
) -> int:
    """Show delta between spec and implementation.

    Launches the agent in read-only mode with ``--effort low`` and a
    gap-detection prompt.  Does NOT run the check command.

    Returns:
        Exit code: 0 if all major sections present, 1 if gaps found, 2 on error.
    """
    # Lazy imports — preserve ``patch("evolve.orchestrator.X")`` surfaces.
    from evolve.application.run_loop import (
        _probe,
        _runs_base,
        get_tui,
    )

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
    from evolve.infrastructure.claude_sdk.oneshot_agents import run_diff_agent
    from evolve.infrastructure.claude_sdk import runtime as _runtime
    _runtime.MODEL = model
    _runtime.EFFORT = effort

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
