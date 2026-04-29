"""Use case: sync README.md to spec.

One-shot use case — orchestration bounded context.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path


def sync_readme(
    project_dir: Path,
    spec: str | None = None,
    apply: bool = False,
    model: str = "claude-opus-4-6",
    effort: str | None = "medium",
) -> int:
    """Refresh README.md to reflect the current spec.

    Args:
        project_dir: Root directory of the project.
        spec: Path to spec file relative to ``project_dir``.
        apply: When True, write directly to README.md and commit.
        model: Claude model identifier to use.
        effort: Reasoning effort level.

    Returns:
        Exit code: 0 (proposal written / applied), 1 (already in sync), 2 (error).
    """
    # Lazy imports — preserve ``patch("evolve.orchestrator.X")`` surfaces.
    __mod = __import__("evolve.orchestrator", fromlist=["_ensure_git", "_git_commit", "_probe", "_runs_base", "get_tui"])
    _ensure_git = __mod._ensure_git
    _git_commit = __mod._git_commit
    _probe = __mod._probe
    _runs_base = __mod._runs_base
    get_tui = __mod.get_tui

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

    # Create timestamped run directory
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = _runs_base(project_dir) / timestamp
    run_dir.mkdir(parents=True, exist_ok=True)
    ui.run_dir_info(str(run_dir))
    mode_label = "APPLY (will commit README.md)" if apply else "PROPOSAL (writes README_proposal.md)"
    ui.info(f"  Mode: SYNC-README — {mode_label}")
    _probe(f"sync-readme session: {run_dir} (apply={apply})")

    # Snapshot README.md mtime
    readme_path = project_dir / "README.md"
    readme_mtime_before = readme_path.stat().st_mtime if readme_path.is_file() else None

    # Launch agent.
    __mod = __import__("evolve.agent", fromlist=["run_sync_readme_agent", "SYNC_README_NO_CHANGES_SENTINEL"])
    run_sync_readme_agent = __mod.run_sync_readme_agent
    SYNC_README_NO_CHANGES_SENTINEL = __mod.SYNC_README_NO_CHANGES_SENTINEL
    __mod = __import__("evolve.infrastructure.claude_sdk", fromlist=["runtime"])
    _runtime = __mod.runtime
    _runtime.MODEL = model
    _runtime.EFFORT = effort

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

    # Inspect filesystem outputs
    sentinel = run_dir / SYNC_README_NO_CHANGES_SENTINEL
    proposal = project_dir / "README_proposal.md"

    if sentinel.is_file():
        ui.info("  README already in sync — no proposal written")
        _probe("sync-readme: no changes needed (exit 1)")
        return 1

    if apply:
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

    if proposal.is_file():
        ui.info(f"  README proposal written: {proposal}")
        _probe("sync-readme: proposal written (exit 0)")
        return 0

    ui.warn("sync-readme: agent produced no README_proposal.md and no NO_SYNC_NEEDED sentinel")
    return 2
