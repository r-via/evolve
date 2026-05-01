"""Scaffolding helpers and between-round agent passes for the orchestrator.

Application layer — orchestration bounded context.
"""

from __future__ import annotations

import re
from pathlib import Path

from evolve.infrastructure.diagnostics.detector import _save_subprocess_diagnostic
from evolve.infrastructure.git.adapter import _git_commit
from evolve.infrastructure.filesystem.state_manager import (
    _detect_premature_converged,
    _runs_base,
)
from evolve.interfaces.tui import TUIProtocol

_PROBE_PREFIX = "\x1b[2;36m[probe]\x1b[0m"
_PROBE_WARN_PREFIX = "\x1b[33m[probe]\x1b[0m"
_PROBE_OK_PREFIX = "\x1b[32m[probe]\x1b[0m"


def _probe(msg: str) -> None:
    """Emit a styled orchestrator probe line to stdout."""
    print(f"{_PROBE_PREFIX} {msg}", flush=True)


def _probe_warn(msg: str) -> None:
    """Probe line flagged as a warning (yellow prefix)."""
    print(f"{_PROBE_WARN_PREFIX} {msg}", flush=True)


def _probe_ok(msg: str) -> None:
    """Probe line flagged as a success (green prefix)."""
    print(f"{_PROBE_OK_PREFIX} {msg}", flush=True)


def _scaffold_shared_runtime_files(project_dir: Path, spec: str | None) -> None:
    """Pre-create shared cross-round runtime files at ``{runs_base}``."""
    runs_base = _runs_base(project_dir)
    runs_base.mkdir(parents=True, exist_ok=True)

    imp_path = runs_base / "improvements.md"
    if not imp_path.is_file():
        imp_path.write_text(
            "# Improvements\n\n"
            "Backlog of user-story items driving evolution — "
            "see SPEC.md § \"Item format — user story with "
            "acceptance criteria\".\n"
        )

    mem_path = runs_base / "memory.md"
    if not mem_path.is_file():
        try:
            from evolve.interfaces.cli.main import _render_default_memory_md
            mem_path.write_text(_render_default_memory_md(spec))
        except ImportError:
            mem_path.write_text(
                "# Agent Memory\n\n"
                "## Errors\n\n## Decisions\n\n## Patterns\n\n## Insights\n"
            )


def _is_self_evolving(project_dir: Path) -> bool:
    """Return True when evolve is evolving its own source tree."""
    try:
        evolve_package_dir = Path(__file__).resolve().parent.parent  # .../evolve
        evolve_project_root = evolve_package_dir.parent
        return project_dir.resolve() == evolve_project_root
    except (OSError, RuntimeError):
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
    """Independently re-verify convergence gates."""
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
    """Parse evolution_report.md to extract completion summary stats."""
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


def _run_curation_pass(
    project_dir: Path,
    run_dir: Path,
    round_num: int,
    improvements_path: Path,
    spec: str | None,
    ui: TUIProtocol,
) -> None:
    """Run memory curation (Mira) between rounds if triggered."""
    from evolve.infrastructure.claude_sdk.memory_curation import run_memory_curation
    from evolve.application.run_loop import (
        _git_commit,
        _runs_base,
    )

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
        return
    elif verdict == "CURATED":
        _probe(f"memory curation: CURATED at round {round_num}")
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
    """Return True when SPEC archival should run this round."""
    from evolve.infrastructure.claude_sdk.spec_archival import _should_run_spec_archival

    spec_path = project_dir / (spec or "README.md")
    return _agent_check(spec_path, round_num)


def _run_spec_archival_pass(
    project_dir: Path,
    run_dir: Path,
    round_num: int,
    spec: str | None,
    ui: TUIProtocol,
) -> None:
    """Run SPEC archival (Sid) between rounds if triggered."""
    from evolve.infrastructure.claude_sdk.spec_archival import run_spec_archival
    from evolve.application.run_loop import _git_commit

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
        return
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
