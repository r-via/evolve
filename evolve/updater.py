"""evolve update — pull latest evolve commit from upstream.

One-shot subcommand. Detects editable vs non-editable install, refuses
to run on dirty trees, active sessions, or non-fast-forward merges.

Exit codes:
  - 0: updated successfully (or already up-to-date)
  - 1: blocked by a safety check (dirty tree, non-FF, active session)
  - 2: error (network, pip failure, no install detected, etc.)

This module is a leaf — it imports ONLY from stdlib so it cannot
introduce import cycles into the package.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

# Status values that indicate an evolve session is still in flight.
# Anything outside this set (CONVERGED, ERROR, ABORTED, max_rounds, etc.)
# is treated as terminal and does NOT block an update.
_ACTIVE_STATUSES = {"running", "in_progress", "paused", "started"}


def _run(
    cmd: list[str],
    cwd: Path | None = None,
) -> subprocess.CompletedProcess:
    """Wrap ``subprocess.run`` with ``capture_output=True`` + ``text=True``.

    Centralised so tests can ``patch("evolve.updater._run", ...)`` once
    and intercept every subprocess call.
    """
    return subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
        check=False,
    )


def _detect_install_location() -> tuple[Path | None, bool]:
    """Return ``(install_dir, is_editable)`` parsed from ``pip show evolve``.

    ``install_dir`` is ``None`` when ``pip show`` fails or evolve is not
    installed at all.  ``is_editable`` is ``True`` when the output
    contains an ``Editable project location:`` line.
    """
    out = _run([sys.executable, "-m", "pip", "show", "evolve"])
    if out.returncode != 0:
        return None, False
    editable_loc: str | None = None
    install_loc: str | None = None
    for line in out.stdout.splitlines():
        if line.startswith("Editable project location:"):
            editable_loc = line.split(":", 1)[1].strip()
        elif line.startswith("Location:"):
            install_loc = line.split(":", 1)[1].strip()
    if editable_loc:
        return Path(editable_loc), True
    if install_loc:
        return Path(install_loc), False
    return None, False


def _detect_active_session(install_dir: Path) -> Path | None:
    """Return path to an active session's ``state.json``, or ``None``.

    Scans ``<install_dir>/.evolve/runs/*/state.json`` for ``status``
    fields in :data:`_ACTIVE_STATUSES`.  Best-effort: malformed JSON or
    missing files are silently skipped.
    """
    runs = install_dir / ".evolve" / "runs"
    if not runs.is_dir():
        return None
    for state_path in sorted(runs.glob("*/state.json")):
        try:
            data = json.loads(state_path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        status = str(data.get("status", "")).lower()
        if status in _ACTIVE_STATUSES:
            return state_path
    return None


def _git_dirty(repo_dir: Path) -> bool:
    """True iff ``git status --porcelain`` reports any non-``.evolve/`` path.

    Per SPEC archive 019, dirty paths under ``.evolve/`` (run artifacts)
    do NOT block an update — only operator-tracked source/config edits do.
    """
    out = _run(["git", "status", "--porcelain"], cwd=repo_dir)
    if out.returncode != 0:
        # Treat git-failure as "not dirty" — the caller's later git ops
        # will surface the real error.  We don't want to spuriously refuse.
        return False
    for line in out.stdout.splitlines():
        # `git status --porcelain` emits a 2-char status followed by a space
        # then the path.  Slice safely; tolerate odd whitespace.
        path = line[3:].strip() if len(line) > 3 else ""
        if not path:
            continue
        if path.startswith(".evolve/") or path == ".evolve":
            continue
        return True
    return False


def _default_ref(repo_dir: Path) -> str:
    """Resolve ``origin/HEAD`` symbolic ref, falling back to ``main``."""
    out = _run(
        ["git", "symbolic-ref", "refs/remotes/origin/HEAD"],
        cwd=repo_dir,
    )
    if out.returncode == 0 and out.stdout.strip():
        return out.stdout.strip().rsplit("/", 1)[-1]
    return "main"


def _git_can_fast_forward(
    repo_dir: Path,
    ref: str,
) -> tuple[bool, str]:
    """Fetch ``origin/<ref>`` and report whether HEAD can fast-forward.

    Returns ``(True, target_sha)`` for a non-trivial FF advance,
    ``(True, "already up-to-date")`` when HEAD already matches origin,
    or ``(False, reason)`` for any block (fetch failure, missing ref,
    diverged history).
    """
    fetch = _run(["git", "fetch", "origin", ref], cwd=repo_dir)
    if fetch.returncode != 0:
        msg = (fetch.stderr or fetch.stdout).strip()
        return False, f"fetch failed: {msg or 'unknown error'}"
    target = f"origin/{ref}"
    head_sha = _run(["git", "rev-parse", "HEAD"], cwd=repo_dir).stdout.strip()
    tgt_sha = _run(["git", "rev-parse", target], cwd=repo_dir).stdout.strip()
    if not tgt_sha:
        return False, f"unknown ref: {target}"
    if head_sha and head_sha == tgt_sha:
        return True, "already up-to-date"
    anc = _run(
        ["git", "merge-base", "--is-ancestor", "HEAD", target],
        cwd=repo_dir,
    )
    if anc.returncode != 0:
        return False, (
            f"non-fast-forward — local commits diverge from {target}; "
            "rebase or reset manually before re-running `evolve update`"
        )
    return True, tgt_sha


def _update_editable(
    repo_dir: Path,
    ref: str | None,
    dry_run: bool,
) -> int:
    """Pull-and-fast-forward an editable install located at *repo_dir*."""
    if _git_dirty(repo_dir):
        print(
            f"BLOCKED: git working tree at {repo_dir} is dirty.\n"
            "Stash, commit, or discard your changes before running "
            "`evolve update` (paths under .evolve/ are ignored).",
            file=sys.stderr,
        )
        return 1
    target_ref = ref or _default_ref(repo_dir)

    if dry_run:
        print(f"[dry-run] would: git -C {repo_dir} fetch origin {target_ref}")
        print(
            f"[dry-run] would: git -C {repo_dir} merge --ff-only "
            f"origin/{target_ref}"
        )
        return 0

    can_ff, info = _git_can_fast_forward(repo_dir, target_ref)
    if not can_ff:
        print(f"BLOCKED: {info}", file=sys.stderr)
        return 1
    if info == "already up-to-date":
        print(f"already up-to-date with origin/{target_ref}")
        return 0

    merge = _run(
        ["git", "merge", "--ff-only", f"origin/{target_ref}"],
        cwd=repo_dir,
    )
    if merge.returncode != 0:
        print(
            f"ERROR: git merge --ff-only failed: "
            f"{(merge.stderr or merge.stdout).strip()}",
            file=sys.stderr,
        )
        return 2
    new_sha = _run(
        ["git", "rev-parse", "HEAD"], cwd=repo_dir
    ).stdout.strip()
    print(f"updated to {new_sha[:12]} on origin/{target_ref}")
    return 0


def _update_non_editable(ref: str | None, dry_run: bool) -> int:
    """Refresh a non-editable install via ``pip install --upgrade evolve``.

    ``--ref`` is honored only for editable installs (the source tree is
    a real git checkout); for pip-managed snapshots we emit a warning
    and proceed with the published-package upgrade.
    """
    if ref is not None:
        print(
            "NOTE: --ref is honored only for editable installs; "
            "ignoring for pip upgrade of the published package.",
            file=sys.stderr,
        )
    cmd = [sys.executable, "-m", "pip", "install", "--upgrade", "evolve"]
    if dry_run:
        print(f"[dry-run] would: {' '.join(cmd)}")
        return 0
    pip = _run(cmd)
    if pip.returncode != 0:
        print("ERROR: pip upgrade failed:", file=sys.stderr)
        print((pip.stderr or pip.stdout).rstrip(), file=sys.stderr)
        return 2
    print("pip upgrade complete")
    return 0


def run_update(dry_run: bool = False, ref: str | None = None) -> int:
    """Entry point — see module docstring for exit-code contract."""
    install_dir, editable = _detect_install_location()
    if install_dir is None:
        print(
            "ERROR: could not detect evolve install location via "
            "`pip show evolve`.  Is evolve installed?",
            file=sys.stderr,
        )
        return 2

    mode = "editable" if editable else "non-editable"
    print(f"evolve install: {install_dir} ({mode})")

    active = _detect_active_session(install_dir)
    if active is not None:
        print(
            f"BLOCKED: active evolve session detected at {active}.\n"
            "Wait for the session to converge or abort it before "
            "running `evolve update`.",
            file=sys.stderr,
        )
        return 1

    if editable:
        return _update_editable(install_dir, ref, dry_run)
    return _update_non_editable(ref, dry_run)
