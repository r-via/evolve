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


# ---------------------------------------------------------------------------
# Runs directory helpers — SPEC.md § "The .evolve/ directory"
# ---------------------------------------------------------------------------


def _runs_base(project_dir: Path) -> Path:
    """Return the canonical runs base directory for *project_dir*.

    All evolve artifacts live under ``<project>/.evolve/runs/``.  This is
    the single source of truth for the base path — no module should
    hard-code ``"runs"`` as a path component.

    Args:
        project_dir: Root directory of the project being evolved.

    Returns:
        ``project_dir / ".evolve" / "runs"``
    """
    return project_dir / ".evolve" / "runs"


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
    canonical = _runs_base(project_dir)
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
        print(f"[migrate] moved runs/ → .evolve/runs/")
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
# Improvements parsing
# ---------------------------------------------------------------------------


def _count_checked(path: Path) -> int:
    """Count the number of completed improvements in an improvements.md file.

    Scans for lines matching ``- [x]`` (checked checkboxes) to determine
    how many improvements have been implemented and verified.

    Args:
        path: Path to the improvements.md file.

    Returns:
        Number of checked (completed) improvement items, or 0 if the file
        does not exist.
    """
    if not path.is_file():
        return 0
    return len(re.findall(r"^- \[x\]", path.read_text(), re.MULTILINE))


def _count_unchecked(path: Path) -> int:
    """Count the number of pending improvements in an improvements.md file.

    Scans for lines matching ``- [ ]`` (unchecked checkboxes) to determine
    how many improvements are still outstanding.

    Args:
        path: Path to the improvements.md file.

    Returns:
        Number of unchecked (pending) improvement items, or 0 if the file
        does not exist.
    """
    if not path.is_file():
        return 0
    return len(re.findall(r"^- \[ \]", path.read_text(), re.MULTILINE))


def _is_needs_package(text: str) -> bool:
    """Check if an improvement text has [needs-package] as a leading tag token.

    Matches patterns like:
      [functional] [needs-package] description
      [performance] [needs-package] description
    Does NOT match [needs-package] mentioned in the description body.

    Args:
        text: The improvement line text (without the checkbox prefix).
    """
    return bool(re.match(r"\[[\w-]+\]\s+\[needs-package\]", text))


def _count_blocked(path: Path) -> int:
    """Count unchecked items that require [needs-package] (blocked without --allow-installs).

    Args:
        path: Path to the improvements.md file.
    """
    if not path.is_file():
        return 0
    count = 0
    for line in path.read_text().splitlines():
        m = re.match(r"^- \[ \] (.+)$", line.strip())
        if m and _is_needs_package(m.group(1)):
            count += 1
    return count


def _get_current_improvement(path: Path, allow_installs: bool = False, yolo: bool | None = None) -> str | None:
    """Return the text of the next pending improvement to implement.

    Finds the first unchecked ``- [ ]`` item in improvements.md. Items tagged
    with ``[needs-package]`` are skipped unless *allow_installs* mode is
    enabled, since installing new packages requires explicit opt-in.

    Args:
        path: Path to the improvements.md file.
        allow_installs: If True, allow improvements that require new package installs.
        yolo: Deprecated alias for *allow_installs*. Will be removed in a future version.

    Returns:
        The improvement description text (everything after ``- [ ] ``), or
        None if no actionable improvement is found or the file does not exist.
    """
    if yolo is not None:
        allow_installs = yolo
    if not path.is_file():
        return None
    for line in path.read_text().splitlines():
        m = re.match(r"^- \[ \] (.+)$", line.strip())
        if m:
            text = m.group(1)
            # Skip [needs-package] items unless --allow-installs is set
            if not allow_installs and _is_needs_package(text):
                continue
            return text
    return None


# ---------------------------------------------------------------------------
# Check output parsing
# ---------------------------------------------------------------------------


def _parse_check_output(text: str) -> tuple[bool | None, int | None, float | None]:
    """Parse check command output to extract pass/fail, test count, and duration.

    Extracts structured information from check command output (e.g. pytest output)
    for inclusion in state.json.

    Args:
        text: Raw text content of a check_round_N.txt file.

    Returns:
        A tuple of (passed, tests, duration_s):
        - passed: True if "PASS" appears in the text, False otherwise, None if text is empty
        - tests: Number of tests passed (from "N passed" pattern), or None
        - duration_s: Duration in seconds (from "in N.Ns" pattern), or None
    """
    if not text.strip():
        return (None, None, None)

    passed = "PASS" in text
    tests: int | None = None
    duration: float | None = None

    tm = re.search(r"(\d+)\s+passed", text)
    if tm:
        tests = int(tm.group(1))

    dm = re.search(r"in\s+([\d.]+)s", text)
    if dm:
        duration = float(dm.group(1))

    return (passed, tests, duration)


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
# Backlog — unchecked set/lines, stats, violation detection
# ---------------------------------------------------------------------------


def _extract_unchecked_set(text: str) -> set[str]:
    """Return the set of verbatim ``- [ ]`` lines in an improvements.md text.

    Used by ``_compute_backlog_stats`` to detect new-item additions via a
    line-set diff against a prior git commit (the same shape used by
    ``_detect_backlog_violation``). Whitespace is stripped to make the
    comparison resilient to incidental edits like trailing-space cleanup.

    Args:
        text: Raw contents of an improvements.md file.

    Returns:
        Set of lines (whitespace-stripped) that begin with ``- [ ]``.
    """
    return {
        line.rstrip()
        for line in text.splitlines()
        if line.lstrip().startswith("- [ ]")
    }


def _extract_unchecked_lines(text: str) -> list[str]:
    """Extract the verbatim ``- [ ]`` lines from an improvements.md text blob.

    Used by the backlog-discipline rule 1 check to compare pre-round and
    post-round improvements.md state and identify newly added pending items.

    Args:
        text: Raw text content of improvements.md (may be empty).

    Returns:
        List of stripped ``- [ ]`` lines in document order.
    """
    out: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("- [ ]"):
            out.append(stripped)
    return out


def _detect_backlog_violation(
    pre_text: str, post_text: str
) -> tuple[bool, list[str]]:
    """Detect a backlog discipline rule 1 (empty-queue gate) violation.

    Compares pre-round and post-round improvements.md state.  A violation
    occurs when the agent added one or more new ``- [ ]`` lines to
    improvements.md while at least one *other* ``- [ ]`` line still exists
    in the post-round file.  Adding a new item to a genuinely empty queue
    is the only legitimate add case and is NOT a violation.

    Args:
        pre_text: improvements.md content snapshotted before the round.
        post_text: improvements.md content after the round committed.

    Returns:
        ``(violated, new_items)`` where ``violated`` is True when rule 1
        was broken and ``new_items`` is the list of newly added unchecked
        lines (empty when ``violated`` is False).
    """
    pre_lines = _extract_unchecked_lines(pre_text)
    post_lines = _extract_unchecked_lines(post_text)
    pre_set = set(pre_lines)
    new_items = [ln for ln in post_lines if ln not in pre_set]
    if not new_items:
        return False, []
    # Other unchecked items in post that are NOT among the freshly added ones.
    # If any such item exists, the queue was non-empty at add time → violation.
    other_count = len(post_lines) - len(new_items)
    return (other_count > 0), (new_items if other_count > 0 else [])


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
