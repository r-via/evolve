"""One-shot orchestrator entry points — dry-run, validate, diff, sync-readme.

SPEC § "The --dry-run flag", "The --validate flag", "evolve diff",
"evolve sync-readme" — top-level orchestrator entry points for the
four read-only / single-shot subcommands.  Extracted from
``evolve/orchestrator.py`` (US-036) to satisfy the SPEC § "Hard rule:
source files MUST NOT exceed 500 lines" cap.  Mirrors the extraction
pattern of US-027 (diagnostics from orchestrator), US-030
(agent_runtime), US-031 (memory_curation), the round-6 spec_archival
split, US-032 (draft_review), US-033 (oneshot_agents), US-034
(sync_readme), and US-035 (prompt_builder).

Public symbols (``run_dry_run``, ``run_validate``, ``run_diff``,
``run_sync_readme``) are re-exported from ``evolve.orchestrator`` for
backward compatibility with the existing test suite (``patch(
"evolve.orchestrator.run_dry_run", ...)``, ``from evolve.orchestrator
import run_validate``, etc.) and with ``evolve/cli.py``'s late-binding
imports inside ``main`` dispatch branches.

Leaf-module invariant: this file imports ONLY from stdlib at module
top.  ``grep -E "^from evolve\\.(agent|orchestrator|cli)( |$|\\.)"
evolve/orchestrator_oneshots.py`` returns zero matches.  The
orchestrator-resident dependencies (``_probe``, ``_auto_detect_check``,
``_emit_stale_readme_advisory``, ``_runs_base``, ``_ensure_git``,
``_git_commit``, ``get_tui``) are imported lazily inside function
bodies so that:

1. tests that ``patch("evolve.orchestrator.X")`` continue to intercept
   (memory.md round-7 lesson + round-1-of-20260427_114957 entry:
   "Re-export ≠ patch surface when call site uses indirection — the
   extracted function must look X up via ``evolve.orchestrator``,
   NOT the original source module");
2. module load order remains acyclic — orchestrator.py re-exports
   the four entry points at the top of its own module body, but
   this file's lazy imports run only when each entry point is
   actually invoked, well after orchestrator.py finishes loading;
3. indented imports do NOT trip the leaf-invariant regex
   ``^from evolve\\.`` (memory.md round-7 entry).

``subprocess.run`` is intentionally left as a top-level
``import subprocess`` — tests patch ``evolve.orchestrator.subprocess.
run`` which mutates the actual ``subprocess`` module's ``run``
attribute (modules are singletons), so the patch propagates
automatically without any lazy-import dance.
"""

from __future__ import annotations

import re
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

    Args:
        project_dir: Root directory of the project being analyzed.
        check_cmd: Shell command to verify the project (run read-only).
        timeout: Timeout in seconds for the check command.
        model: Claude model identifier to use.
        spec: Path to the spec file relative to project_dir (default: README.md).
    """
    # Lazy imports — preserve ``patch("evolve.orchestrator.X")`` surfaces.
    from evolve.orchestrator import (
        _auto_detect_check,
        _emit_stale_readme_advisory,
        _probe,
        _runs_base,
        get_tui,
    )

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
    # Lazy imports — preserve ``patch("evolve.orchestrator.X")`` surfaces.
    from evolve.orchestrator import (
        _auto_detect_check,
        _emit_stale_readme_advisory,
        _probe,
        _runs_base,
        get_tui,
    )

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
    # Lazy imports — preserve ``patch("evolve.orchestrator.X")`` surfaces.
    from evolve.orchestrator import (
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
    # Lazy imports — preserve ``patch("evolve.orchestrator.X")`` surfaces.
    from evolve.orchestrator import (
        _ensure_git,
        _git_commit,
        _probe,
        _runs_base,
        get_tui,
    )

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
