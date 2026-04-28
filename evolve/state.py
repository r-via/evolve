"""State management — state.json, improvements parsing, convergence gates, backlog discipline.

Extracted from ``loop.py`` as part of the package restructuring (SPEC.md §
"Architecture", migration step 4).
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

import subprocess as _subprocess

from evolve.git import _git_show_at

# Re-export improvement-parsing helpers from the leaf module
# (US-044: keeps state.py under SPEC.md § "Hard rule" 500-line cap).
# Existing ``from evolve.state import _count_unchecked`` (and friends)
# call sites — orchestrator, diagnostics, agent, tests — continue to
# work via this re-export chain.
from evolve.state_improvements import (
    _count_blocked,
    _count_checked,
    _count_unchecked,
    _detect_backlog_violation,
    _extract_unchecked_lines,
    _extract_unchecked_set,
    _get_current_improvement,
    _is_needs_package,
    _parse_check_output,
)


# ---------------------------------------------------------------------------
# Runs directory helpers — SPEC.md § "The .evolve/ directory"
# ---------------------------------------------------------------------------


def _runs_base(project_dir: Path) -> Path:
    """Return the runs base directory for *project_dir*.

    Canonical location is ``<project>/.evolve/runs/`` per SPEC.md §
    "The .evolve/ directory".  For backward compatibility during the
    migration window — and for isolated test fixtures that haven't
    adopted ``.evolve/`` — legacy ``<project>/runs/`` is accepted
    as a fallback **only when it already exists on disk and the
    canonical path does not**.  New creations always go to the
    canonical location.

    Resolution order:

    1. If ``<project>/.evolve/runs/`` exists → canonical (primary).
    2. Else if ``<project>/runs/`` exists → legacy (transition).
    3. Else → canonical (pre-create target).

    The ambiguous "both exist" case is handled by
    ``_ensure_runs_layout`` which raises ``_RunsLayoutError`` and
    asks the operator to reconcile — this helper simply returns the
    canonical path in that case to avoid silent split-brain.

    Args:
        project_dir: Root directory of the project being evolved.

    Returns:
        ``project_dir / ".evolve" / "runs"`` (canonical) or, in the
        transition case, ``project_dir / "runs"`` (legacy).
    """
    canonical = project_dir / ".evolve" / "runs"
    legacy = project_dir / "runs"
    if canonical.is_dir():
        return canonical
    if legacy.is_dir():
        return legacy
    return canonical


class _RunsLayoutError(Exception):
    """Raised when both legacy ``runs/`` and ``.evolve/runs/`` exist."""


def _ensure_runs_layout(project_dir: Path) -> Path:
    """Ensure the runs directory is in the canonical ``.evolve/runs/`` location.

    Handles three cases per SPEC.md § "Migration from legacy runs/":

    1. **Only ``.evolve/runs/`` exists** (or neither) — return it, create if needed.
    2. **Only legacy ``runs/`` exists** — migrate in-place via ``git mv`` (preserves
       git history), emit a ``[migrate]`` notice, return the new path.
    3. **Both exist** — raise ``_RunsLayoutError`` with instructions.

    Args:
        project_dir: Root directory of the project being evolved.

    Returns:
        The canonical ``project_dir / ".evolve" / "runs"`` path (created if needed).

    Raises:
        _RunsLayoutError: When both ``runs/`` and ``.evolve/runs/`` exist and
            the operator must resolve the ambiguity manually.
    """
    canonical = project_dir / ".evolve" / "runs"
    legacy = project_dir / "runs"

    legacy_exists = legacy.is_dir()
    canonical_exists = canonical.is_dir()

    # Case 3 — ambiguous
    if legacy_exists and canonical_exists:
        raise _RunsLayoutError(
            f"Both '{legacy}' and '{canonical}' exist.\n"
            f"Resolve before restarting:\n"
            f"  mv runs/* .evolve/runs/ && rmdir runs   # merge legacy into new\n"
            f"  rm -rf runs                              # discard legacy\n"
        )

    # Case 2 — legacy migration
    if legacy_exists and not canonical_exists:
        # Ensure .evolve/ parent exists
        canonical.parent.mkdir(parents=True, exist_ok=True)
        # Try git mv first (preserves history)
        try:
            _subprocess.run(
                ["git", "mv", "runs", str(canonical.relative_to(project_dir))],
                cwd=str(project_dir),
                capture_output=True,
                check=True,
                timeout=30,
            )
        except (FileNotFoundError, _subprocess.CalledProcessError, _subprocess.TimeoutExpired):
            # Fallback: plain rename (no git history, but works outside git repos)
            import shutil
            shutil.move(str(legacy), str(canonical))
        # Yellow ``[migrate]`` prefix — ANSI is parsed by the parent
        # Rich TUI (``Text.from_ansi``) and rendered as styled text;
        # plain terminals see the raw escapes which are still readable.
        print(f"\x1b[33m[migrate]\x1b[0m moved runs/ → .evolve/runs/", flush=True)
        return canonical

    # Case 1 — canonical (or fresh start)
    canonical.mkdir(parents=True, exist_ok=True)
    return canonical


# ---------------------------------------------------------------------------
# Spec freshness gate
# ---------------------------------------------------------------------------


def _check_spec_freshness(
    project_dir: Path,
    improvements_path: Path,
    spec: str | None = None,
) -> bool:
    """Check if the spec file is newer than improvements.md (spec freshness gate).

    Compares ``mtime(spec_file)`` vs ``mtime(improvements.md)``.  If the spec
    is newer, the backlog is stale — all unchecked items are marked with
    ``[stale: spec changed]`` so the agent knows to rebuild ``improvements.md``
    from the updated spec.

    Args:
        project_dir: Root directory of the project.
        improvements_path: Path to the improvements.md file.
        spec: Path to the spec file relative to project_dir (default: README.md).

    Returns:
        True if improvements.md is fresh (its mtime >= spec mtime), False if
        the spec is newer and the backlog was marked stale.
    """
    spec_file = spec or "README.md"
    spec_path = project_dir / spec_file

    if not spec_path.is_file():
        return True  # No spec file — nothing to gate on

    if not improvements_path.is_file():
        return True  # No backlog yet — agent will create it fresh

    spec_mtime = spec_path.stat().st_mtime
    imp_mtime = improvements_path.stat().st_mtime

    if imp_mtime >= spec_mtime:
        return True  # Backlog is aligned with spec

    # Spec is newer — mark all unchecked items as stale
    text = improvements_path.read_text()
    lines = text.splitlines()
    new_lines = []
    for line in lines:
        stripped = line.strip()
        if re.match(r"^- \[ \]", stripped) and "[stale: spec changed]" not in stripped:
            # Insert [stale: spec changed] after the checkbox
            line = line.replace("- [ ] ", "- [ ] [stale: spec changed] ", 1)
        new_lines.append(line)

    improvements_path.write_text("\n".join(new_lines))
    return False


# ---------------------------------------------------------------------------
# Convergence gates
# ---------------------------------------------------------------------------


def _detect_premature_converged(
    improvements_path: Path,
    spec_path: Path,
) -> tuple[bool, str]:
    """Re-verify the two convergence gates after the agent wrote ``CONVERGED``.

    This is the **orchestrator-side backstop** for SPEC.md § "Convergence":
    Phase 4 criteria are 100% agent-judged today, so we independently
    re-verify the two documented gates before firing ``on_converged`` and
    party mode.

    The gates are:

    1. **Spec freshness gate** —
       ``mtime(improvements.md) >= mtime(spec_file)`` AND no
       ``[stale: spec changed]`` items remain in ``improvements.md``.
    2. **Backlog gate** — every ``- [ ]`` line in ``improvements.md`` is
       tagged with ``[needs-package]`` or ``[blocked:`` (any other
       unchecked item is an unresolved blocker).

    Args:
        improvements_path: Path to ``improvements.md``.
        spec_path: Path to the spec file (``README.md`` or ``--spec`` target).

    Returns:
        Tuple ``(is_premature, reason)`` — ``is_premature`` is True when
        at least one gate failed; ``reason`` is a human-readable
        concatenation of every failing gate (joined by `` AND ``), or
        empty when no gate failed.
    """
    reasons: list[str] = []

    # Gate 1 — spec freshness
    if spec_path.is_file() and improvements_path.is_file():
        if spec_path.stat().st_mtime > improvements_path.stat().st_mtime:
            reasons.append(
                f"spec freshness gate: mtime({spec_path.name}) > "
                f"mtime(improvements.md) — backlog must be rebuilt"
            )
    if improvements_path.is_file():
        imp_text = improvements_path.read_text()
        # Only check *unchecked* lines for stale markers — the phrase may
        # appear inside the description text of completed [x] items (e.g.
        # "Implement Phase 2 spec freshness gate ... mark items as
        # `[stale: spec changed]`") and those must not trigger the gate.
        has_stale = any(
            "[stale: spec changed]" in line
            for line in imp_text.splitlines()
            if line.strip().startswith("- [ ]")
        )
        if has_stale:
            reasons.append(
                "spec freshness gate: [stale: spec changed] items still "
                "present in improvements.md — backlog must be rebuilt"
            )

    # Gate 2 — backlog: every `- [ ]` must carry an allowed blocker tag
    if improvements_path.is_file():
        unresolved: list[str] = []
        for line in improvements_path.read_text().splitlines():
            stripped = line.strip()
            if not stripped.startswith("- [ ]"):
                continue
            if (
                "[needs-package]" in stripped
                or "[blocked:" in stripped
            ):
                continue
            unresolved.append(stripped[:120])
        if unresolved:
            sample = "; ".join(unresolved[:3])
            reasons.append(
                f"backlog gate: {len(unresolved)} unresolved `- [ ]` "
                f"item(s) without [needs-package]/[blocked:] tags "
                f"(sample: {sample})"
            )

    if reasons:
        return True, " AND ".join(reasons)
    return False, ""


# ---------------------------------------------------------------------------
# Improvements parsing — extracted to evolve/state_improvements.py (US-044)
# Re-exports at module top: _count_checked, _count_unchecked, _is_needs_package,
# _count_blocked, _get_current_improvement, _parse_check_output,
# _extract_unchecked_set, _extract_unchecked_lines, _detect_backlog_violation.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# RESTART_REQUIRED marker parsing
# ---------------------------------------------------------------------------


def _parse_restart_required(run_dir: Path) -> dict | None:
    """Parse a RESTART_REQUIRED marker file if present.

    The marker is written by the agent when a structural change is detected
    (SPEC.md § "Structural change self-detection").  Format::

        # RESTART_REQUIRED
        reason: <one-line why the process must restart>
        verify: <shell command(s) the operator should run>
        resume: <shell command to continue evolution>
        round: <current round number>
        timestamp: <ISO-8601 UTC>

    Args:
        run_dir: Session directory to check for the marker.

    Returns:
        Dict with keys reason/verify/resume/round/timestamp, or None if no
        marker is present.
    """
    marker_path = run_dir / "RESTART_REQUIRED"
    if not marker_path.is_file():
        return None

    marker: dict[str, str] = {}
    try:
        text = marker_path.read_text(errors="replace")
        for line in text.splitlines():
            line = line.strip()
            if line.startswith("#") or not line:
                continue
            if ":" in line:
                key, _, value = line.partition(":")
                marker[key.strip()] = value.strip()
    except OSError:
        return None

    # Must have at least the reason field to be valid
    if "reason" not in marker:
        return None

    return marker


# ---------------------------------------------------------------------------
# Backlog — stats (line-set extraction + violation detection extracted to
# evolve/state_improvements.py per US-044, re-exported at module top).
# ---------------------------------------------------------------------------


def _compute_backlog_stats(
    project_dir: Path,
    improvements_path: Path,
    pending: int,
    done: int,
    blocked: int,
) -> dict:
    """Compute the ``backlog`` block exposed in state.json.

    Implements SPEC.md § "Growth monitoring": the five fields are
    ``pending``, ``done``, ``blocked`` (taken from the caller, which has
    already counted them for the ``improvements`` block) plus
    ``added_this_round`` and ``growth_rate_last_5_rounds`` derived from
    git history of ``improvements.md``.

    - ``added_this_round`` = ``len(current_unchecked − prior_unchecked)``
      where the prior is improvements.md at ``HEAD`` (the previous
      committed state). Uses a line-set diff so that checking off one
      item and adding another does not falsely register as +1 — only
      genuinely new ``- [ ]`` lines count.
    - ``growth_rate_last_5_rounds`` = ``(pending − pending_at_HEAD~5) / 5``,
      rounded to 2 decimals. Negative values mean the queue is draining
      (the healthy direction); positive means it is growing.

    All git lookups degrade gracefully — a missing repo, missing ref, or
    missing file at the ref returns 0 / 0.0 instead of crashing. This
    keeps the helper safe for fresh projects (no commits yet), test
    fixtures (tmp_path with no git), and corrupted repos.
    """
    added_this_round = 0
    growth_rate = 0.0

    if improvements_path.is_file():
        try:
            rel_path = improvements_path.relative_to(project_dir).as_posix()
        except ValueError:
            rel_path = None

        if rel_path:
            current_text = improvements_path.read_text(errors="replace")
            current_unchecked = _extract_unchecked_set(current_text)

            prev_text = _git_show_at(project_dir, "HEAD", rel_path)
            if prev_text is not None:
                prev_unchecked = _extract_unchecked_set(prev_text)
                added_this_round = len(current_unchecked - prev_unchecked)

            five_back_text = _git_show_at(project_dir, "HEAD~5", rel_path)
            if five_back_text is not None:
                five_back_pending = len(_extract_unchecked_set(five_back_text))
                growth_rate = round((pending - five_back_pending) / 5.0, 2)

    return {
        "pending": pending,
        "done": done,
        "blocked": blocked,
        "added_this_round": added_this_round,
        "growth_rate_last_5_rounds": growth_rate,
    }


# ---------------------------------------------------------------------------
# state.json writer
# ---------------------------------------------------------------------------


def _write_state_json(
    run_dir: Path,
    project_dir: Path,
    round_num: int,
    max_rounds: int,
    phase: str,
    status: str,
    improvements_path: Path,
    check_passed: bool | None = None,
    check_tests: int | None = None,
    check_duration_s: float | None = None,
    started_at: str | None = None,
    usage: dict | None = None,
) -> None:
    """Write or update the real-time state.json file in the session directory.

    The state file provides structured status queryable by external tools
    (CI systems, dashboards, monitoring). It is updated after every round.

    Args:
        run_dir: Session directory where state.json is written.
        project_dir: Root directory of the project.
        round_num: Current round number.
        max_rounds: Maximum rounds configured for the session.
        phase: Current phase (``"error"``, ``"improvement"``, ``"convergence"``).
        status: Current status (``"running"``, ``"converged"``, ``"max_rounds"``,
                ``"error"``, ``"party_mode"``, ``"budget_reached"``).
        improvements_path: Path to the improvements.md file.
        check_passed: Whether the last check command passed (None if not run).
        check_tests: Number of tests passed in the last check (None if unknown).
        check_duration_s: Duration of the last check in seconds (None if unknown).
        started_at: ISO timestamp when the session started. If None, read from
                    existing state.json or use current time.
        usage: Token usage and cost aggregation dict (from ``build_usage_state``).
            Included in state.json when provided.
    """
    done = _count_checked(improvements_path)
    remaining = _count_unchecked(improvements_path)
    blocked = _count_blocked(improvements_path)

    # Determine started_at: use provided value, or read from existing state, or now
    if started_at is None:
        existing = run_dir / "state.json"
        if existing.is_file():
            try:
                prev = json.loads(existing.read_text())
                started_at = prev.get("started_at")
            except (json.JSONDecodeError, OSError):
                pass
        if started_at is None:
            started_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    last_check: dict[str, bool | int | float | None] = {}
    if check_passed is not None:
        last_check["passed"] = check_passed
    if check_tests is not None:
        last_check["tests"] = check_tests
    if check_duration_s is not None:
        last_check["duration_s"] = round(check_duration_s, 1)

    backlog = _compute_backlog_stats(
        project_dir, improvements_path, remaining, done, blocked
    )

    state: dict = {
        "version": 2,
        "session": run_dir.name,
        "project": project_dir.name,
        "round": round_num,
        "max_rounds": max_rounds,
        "phase": phase,
        "status": status,
        "improvements": {"done": done, "remaining": remaining, "blocked": blocked},
        "backlog": backlog,
        "last_check": last_check if last_check else {},
        "started_at": started_at,
        "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    if usage is not None:
        state["usage"] = usage
    state_path = run_dir / "state.json"
    state_path.write_text(json.dumps(state, indent=2) + "\n")
