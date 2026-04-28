"""Improvement-parsing helpers — extracted from ``evolve.state``.

US-044: pure parsing helpers split out of ``evolve/state.py`` to keep
that module under the SPEC.md § "Hard rule: source files MUST NOT
exceed 500 lines" cap.

This is a **leaf module** — it imports only stdlib (``pathlib`` /
``re``).  No imports from ``evolve.agent`` / ``evolve.orchestrator``
/ ``evolve.cli`` / ``evolve.state``.  ``evolve.state`` re-exports
every name defined here so existing
``from evolve.state import _count_unchecked`` (and friends) callers
keep working unchanged.
"""

from __future__ import annotations

import re
from pathlib import Path


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
# Backlog — unchecked set/lines, violation detection
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
