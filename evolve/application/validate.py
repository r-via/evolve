"""Use case: spec compliance validation.

One-shot use case — orchestration bounded context.
"""

from __future__ import annotations

import re
import subprocess
from datetime import datetime
from pathlib import Path


def validate(
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

    Returns:
        Exit code: 0 if all claims pass, 1 if any fail, 2 on error.
    """
    # Lazy imports — preserve ``patch("evolve.orchestrator.X")`` surfaces.
    __mod = __import__("evolve.orchestrator", fromlist=["_auto_detect_check", "_emit_stale_readme_advisory", "_probe", "_runs_base", "get_tui"])
    _auto_detect_check = __mod._auto_detect_check
    _emit_stale_readme_advisory = __mod._emit_stale_readme_advisory
    _probe = __mod._probe
    _runs_base = __mod._runs_base
    get_tui = __mod.get_tui

    ui = get_tui()

    _probe(f"validate starting — project={project_dir.name}")

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
    __mod = __import__("evolve.agent", fromlist=["run_validate_agent"])
    run_validate_agent = __mod.run_validate_agent
    __mod = __import__("evolve.infrastructure.claude_sdk", fromlist=["runtime"])
    _runtime = __mod.runtime
    _runtime.MODEL = model
    _runtime.EFFORT = effort

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
