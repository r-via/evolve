"""evolve CLI utility subcommands — ``clean``, ``history``, ``status``.

Extracted from ``evolve/cli.py`` per SPEC.md § "Hard rule: source files
MUST NOT exceed 500 lines".  ``evolve/cli.py`` re-exports each symbol so
existing ``from evolve.cli import _clean_sessions`` (and ``from evolve
import _clean_sessions`` via the package shim) keep working unchanged.

Leaf module: imports only stdlib + ``evolve.state`` + ``evolve.tui`` at
module top — never ``evolve.agent`` / ``evolve.orchestrator`` /
``evolve.cli`` — so it can be imported by ``evolve.cli`` without cycles.
"""

import re as _re
import shutil
from pathlib import Path

from evolve.state import _count_blocked, _runs_base
from evolve.tui import get_tui


def _clean_sessions(project_dir: Path, keep: int = 5) -> None:
    """Remove old session directories, keeping the N most recent.

    Args:
        project_dir: Root directory of the project.
        keep: Number of most-recent sessions to retain.
    """
    runs_dir = _runs_base(project_dir)
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
    """Show evolution timeline across all sessions.

    Parses evolution_report.md and CONVERGED markers from each session
    directory to build a table of sessions with round counts, status,
    and improvement statistics.

    Args:
        project_dir: Root directory of the project.
    """
    ui = get_tui()

    runs_dir = _runs_base(project_dir)
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

        # Parse evolution_report.md for round info
        report_path = session_dir / "evolution_report.md"
        rounds_str = "?"
        checked = 0
        unchecked = 0

        if report_path.is_file():
            report_text = report_path.read_text(errors="replace")
            # Extract "Rounds: N/M"
            m = _re.search(r"\*\*Rounds:\*\*\s*(\d+)/(\d+)", report_text)
            if m:
                rounds_str = f"{m.group(1)}/{m.group(2)}"
                total_rounds += int(m.group(1))
            # Extract "N improvements completed"
            m = _re.search(r"(\d+)\s+improvements completed", report_text)
            if m:
                checked = int(m.group(1))
                total_improvements += checked
            # Extract "N improvements remaining"
            m = _re.search(r"(\d+)\s+improvements remaining", report_text)
            if m:
                unchecked = int(m.group(1))
            # Extract status from report
            m = _re.search(r"\*\*Status:\*\*\s*(\w+)", report_text)
            if m:
                status = m.group(1)
        else:
            # Fall back: count conversation logs for round info
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
    """Display the current evolution status of *project_dir*.

    Reads ``runs/improvements.md`` and ``runs/memory.md`` to summarise
    checked / unchecked / blocked improvements and accumulated errors.
    Also identifies the latest session directory and reports its round
    count, check count, and convergence status.

    Args:
        project_dir: Path to the target project.
    """
    ui = get_tui()

    runs_dir = _runs_base(project_dir)
    improvements_path = runs_dir / "improvements.md"
    memory_path = runs_dir / "memory.md"

    ui.status_header(str(project_dir), (project_dir / "README.md").is_file())

    if improvements_path.is_file():
        content = improvements_path.read_text()
        checked = len(_re.findall(r"^- \[x\]", content, _re.MULTILINE))
        unchecked = len(_re.findall(r"^- \[ \]", content, _re.MULTILINE))
        blocked = _count_blocked(improvements_path)
        ui.status_improvements(checked, unchecked, blocked)
    else:
        ui.status_no_improvements()

    if memory_path.is_file():
        lines = [l for l in memory_path.read_text().splitlines() if l.startswith("## Error:")]
        ui.status_memory(len(lines))
    else:
        ui.status_memory(0)

    # Show latest session
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
