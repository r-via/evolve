"""Git operations for evolve — commit, push, branch management, ensure-git.

Extracted from ``loop.py`` as part of the package restructuring
(SPEC.md § "Architecture", migration step 3).  All callers in
``loop.py`` import from here; ``loop._ensure_git`` et al. remain
importable for backward compatibility because ``loop.py`` re-exports
the names.
"""

from __future__ import annotations

import subprocess
import sys
from datetime import datetime
from pathlib import Path

from evolve.tui import TUIProtocol, get_tui


def _git_show_at(project_dir: Path, ref: str, rel_path: str) -> str | None:
    """Return the contents of ``rel_path`` at git ref ``ref``, or None on failure.

    Used by ``_compute_backlog_stats`` to read prior commits of
    improvements.md so backlog growth can be computed. Returns None on
    any git failure (no repo, missing ref, missing file at ref, timeout)
    so callers can degrade gracefully rather than crash.
    """
    try:
        result = subprocess.run(
            ["git", "show", f"{ref}:{rel_path}"],
            cwd=project_dir,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (subprocess.SubprocessError, FileNotFoundError, OSError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout


def _setup_forever_branch(project_dir: Path) -> None:
    """Create and switch to a dedicated branch for forever mode.

    Creates a branch named ``evolve/<timestamp>`` from the current HEAD
    so that forever-mode changes are isolated from the main branch.

    Args:
        project_dir: Root directory of the project (must be a git repo).
    """
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    branch_name = f"evolve/{timestamp}"
    ui = get_tui()

    result = subprocess.run(
        ["git", "checkout", "-b", branch_name],
        cwd=str(project_dir),
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        ui.error(f"Failed to create branch {branch_name}: {result.stderr.strip()}")
        sys.exit(2)

    ui.info(f"  Forever mode: created branch {branch_name}")


def _ensure_git(project_dir: Path, ui: TUIProtocol | None = None) -> None:
    """Verify *project_dir* is a git repository and snapshot uncommitted changes.

    Checks that ``git rev-parse --git-dir`` succeeds; if not, prints an error
    via *ui* and exits with code 2.  If the working tree has uncommitted
    changes, they are auto-committed with a snapshot message so the evolution
    loop starts from a clean state.

    Args:
        project_dir: Path to the target project.
        ui: Optional TUI instance (defaults to ``get_tui()``).
    """
    if ui is None:
        ui = get_tui()
    result = subprocess.run(
        ["git", "rev-parse", "--git-dir"],
        cwd=project_dir, capture_output=True, text=True,
    )
    if result.returncode != 0:
        ui.error(f"ERROR: {project_dir} is not a git repository.")
        sys.exit(2)

    status = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=project_dir, capture_output=True, text=True,
    )
    if status.stdout.strip():
        ui.uncommitted()
        subprocess.run(["git", "add", "-A"], cwd=project_dir)
        subprocess.run(
            ["git", "commit", "-m", "evolve: snapshot before evolution"],
            cwd=project_dir, capture_output=True,
        )


def _git_commit(project_dir: Path, message: str, ui: TUIProtocol | None = None) -> None:
    """Stage all changes, commit with *message*, and push to the remote.

    Runs ``git add -A`` then checks whether the index differs from HEAD.
    If there is nothing to commit the function returns early.  Otherwise it
    commits and pushes.  On the first push of a new branch (no upstream), it
    automatically sets the upstream with ``git push -u origin <branch>``.

    Args:
        project_dir: Path to the target project repository.
        message: Conventional-commit message written by the agent.
        ui: Optional TUI instance (defaults to ``get_tui()``).
    """
    if ui is None:
        ui = get_tui()
    print(f"[probe] git: staging changes")
    subprocess.run(["git", "add", "-A"], cwd=project_dir)
    status = subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=project_dir)
    if status.returncode == 0:
        print("[probe] git: nothing to commit")
        ui.git_status(message, pushed=None)
        return
    subprocess.run(["git", "commit", "-m", message], cwd=project_dir, capture_output=True)
    result = subprocess.run(["git", "push"], cwd=project_dir, capture_output=True, text=True)
    if result.returncode != 0 and "has no upstream branch" in (result.stderr or ""):
        # First push on a new branch — set upstream and retry
        branch = subprocess.run(
            ["git", "branch", "--show-current"], cwd=project_dir,
            capture_output=True, text=True,
        ).stdout.strip()
        result = subprocess.run(
            ["git", "push", "-u", "origin", branch], cwd=project_dir,
            capture_output=True, text=True,
        )
    if result.returncode == 0:
        ui.git_status(message, pushed=True)
    else:
        ui.git_status(message, pushed=False, error=result.stderr.strip()[:100])
