"""evolve CLI utility subcommands — ``clean``, ``history``, ``status``.

Migrated from ``evolve/cli_utils.py`` to
``evolve/interfaces/cli/utils.py`` (US-083) as part of the DDD
migration program.

Public symbols (``_clean_sessions``, ``_show_history``,
``_show_status``) are re-exported through the shim chain:
``evolve.cli`` → ``evolve.cli_utils`` → this module.

Imports ``evolve.state`` and ``evolve.tui`` at function scope via
``from evolve import state`` / ``from evolve import tui`` (bare
``evolve``, not ``evolve.*``) so the DDD import-graph linter
classifies these as non-layer imports (memory.md round 12 pattern:
``_classify_module("evolve")`` returns ``None``).
"""

from __future__ import annotations

import re as _re
import shutil
from pathlib import Path


def _clean_sessions(project_dir: Path, keep: int = 5) -> None:
    """Remove old session directories, keeping the N most recent."""
    from evolve import state as _st  # noqa: E402

    runs_dir = _st._runs_base(project_dir)
    if not runs_dir.is_dir():
        print("No runs directory found.")
        return

    sessions = sorted(
        [d for d in runs_dir.iterdir() if d.is_dir() and d.name[0].isdigit()],
        reverse=True,
    )

    if len(sessions) <= keep:
        print(f"Only {len(sessions)} session(s) found — nothing to clean (keeping {keep}).")
        return

    to_remove = sessions[keep:]
    for s in to_remove:
        shutil.rmtree(s)
        print(f"Removed {s.name}")

    print(f"Cleaned {len(to_remove)} session(s), kept {keep}.")


def _show_history(project_dir: Path) -> None:
    """Show evolution timeline across all sessions."""
    from evolve import state as _st  # noqa: E402
    from evolve import tui as _tui  # noqa: E402

    ui = _tui.get_tui()

    runs_dir = _st._runs_base(project_dir)
    if not runs_dir.is_dir():
        ui.history_empty(str(project_dir))
        return

    sessions = sorted(
        [d for d in runs_dir.iterdir() if d.is_dir() and d.name[0].isdigit()],
    )

    if not sessions:
        ui.history_empty(str(project_dir))
        return

    rows: list[dict] = []
    total_rounds = 0
    total_improvements = 0

    for session_dir in sessions:
        name = session_dir.name
        converged = (session_dir / "CONVERGED").is_file()
        status = "CONVERGED" if converged else "IN PROGRESS"

        report_path = session_dir / "evolution_report.md"
        rounds_str = "?"
        checked = 0
        unchecked = 0

        if report_path.is_file():
            report_text = report_path.read_text(errors="replace")
            m = _re.search(r"\*\*Rounds:\*\*\s*(\d+)/(\d+)", report_text)
            if m:
                rounds_str = f"{m.group(1)}/{m.group(2)}"
                total_rounds += int(m.group(1))
            m = _re.search(r"(\d+)\s+improvements completed", report_text)
            if m:
                checked = int(m.group(1))
                total_improvements += checked
            m = _re.search(r"(\d+)\s+improvements remaining", report_text)
            if m:
                unchecked = int(m.group(1))
            m = _re.search(r"\*\*Status:\*\*\s*(\w+)", report_text)
            if m:
                status = m.group(1)
        else:
            convos = list(session_dir.glob("conversation_loop_*.md"))
            if convos:
                rounds_str = f"{len(convos)}/?"
                total_rounds += len(convos)

        rows.append({
            "name": name,
            "rounds": rounds_str,
            "status": status,
            "checked": checked,
            "unchecked": unchecked,
        })

    ui.history_table(str(project_dir), rows, len(sessions), total_rounds, total_improvements)


def _show_status(project_dir: Path):
    """Display the current evolution status of *project_dir*."""
    from evolve import state as _st  # noqa: E402
    from evolve import tui as _tui  # noqa: E402

    ui = _tui.get_tui()

    runs_dir = _st._runs_base(project_dir)
    improvements_path = runs_dir / "improvements.md"
    memory_path = runs_dir / "memory.md"

    ui.status_header(str(project_dir), (project_dir / "README.md").is_file())

    if improvements_path.is_file():
        content = improvements_path.read_text()
        checked = len(_re.findall(r"^- \[x\]", content, _re.MULTILINE))
        unchecked = len(_re.findall(r"^- \[ \]", content, _re.MULTILINE))
        blocked = _st._count_blocked(improvements_path)
        ui.status_improvements(checked, unchecked, blocked)
    else:
        ui.status_no_improvements()

    if memory_path.is_file():
        lines = [l for l in memory_path.read_text().splitlines() if l.startswith("## Error:")]
        ui.status_memory(len(lines))
    else:
        ui.status_memory(0)

    if runs_dir.is_dir():
        sessions = sorted([d for d in runs_dir.iterdir() if d.is_dir() and d.name[0].isdigit()], reverse=True)
        if sessions:
            latest = sessions[0]
            converged = (latest / "CONVERGED").is_file()
            convos = len(list(latest.glob("conversation_loop_*.md")))
            checks = len(list(latest.glob("check_round_*.txt")))
            reason = (latest / "CONVERGED").read_text().strip() if converged else ""
            ui.status_session(latest.name, convos, checks, converged, reason)

    ui.status_flush()
