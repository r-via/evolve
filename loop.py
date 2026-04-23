"""Evolution loop orchestrator.

Each round runs as a separate subprocess so code changes are picked up immediately.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from hooks import fire_hook, load_hooks
from tui import TUIProtocol, get_tui


def _auto_detect_check(project_dir: Path) -> str | None:
    """Auto-detect the test framework for a project.

    Looks for common project files and checks whether the corresponding
    test runner is available on PATH.  Returns the first match or None.

    Detection order:
      1. pytest      — pyproject.toml, setup.py, setup.cfg, or test_*.py files
      2. npm test    — package.json
      3. cargo test  — Cargo.toml
      4. go test ./...  — go.mod
      5. make test   — Makefile with a 'test' target

    Args:
        project_dir: Root directory of the project to inspect.

    Returns:
        A shell command string (e.g. ``"pytest"``) or None if nothing found.
    """
    # pytest: Python project indicators
    py_markers = ["pyproject.toml", "setup.py", "setup.cfg", "tox.ini", "pytest.ini"]
    has_python = any((project_dir / m).is_file() for m in py_markers)
    if not has_python:
        # Also check for test_*.py files at top level or in tests/
        has_python = bool(list(project_dir.glob("test_*.py")))
        if not has_python and (project_dir / "tests").is_dir():
            has_python = bool(list((project_dir / "tests").glob("test_*.py")))
    if has_python and shutil.which("pytest"):
        return "pytest"

    # npm test: Node.js project
    if (project_dir / "package.json").is_file() and shutil.which("npm"):
        return "npm test"

    # cargo test: Rust project
    if (project_dir / "Cargo.toml").is_file() and shutil.which("cargo"):
        return "cargo test"

    # go test: Go project
    if (project_dir / "go.mod").is_file() and shutil.which("go"):
        return "go test ./..."

    # make test: Makefile with test target
    makefile = project_dir / "Makefile"
    if makefile.is_file() and shutil.which("make"):
        try:
            content = makefile.read_text(errors="replace")
            if re.search(r"^test\s*:", content, re.MULTILINE):
                return "make test"
        except OSError:
            pass

    return None


# Stale-README advisory constants — keep the runtime advisory aligned with
# SPEC.md § "Stale-README pre-flight check".  The advisory is emitted once
# at the start of `evolve start` when --spec points at a file other than
# README.md and the spec file was modified more than the configured
# threshold days ago relative to README.md.  Pure observability: never
# blocks anything, never modifies any file, never runs during rounds.
_README_STALE_ADVISORY_FMT = (
    "\u2139\ufe0f  README has not been updated in {days} days \u2014 "
    "consider `evolve sync-readme`"
)
_DEFAULT_README_STALE_THRESHOLD_DAYS = 30


def _emit_stale_readme_advisory(
    project_dir: Path,
    spec: str | None,
    ui: TUIProtocol,
) -> None:
    """Emit the startup-time stale-README advisory (SPEC § "Stale-README pre-flight check").

    When ``--spec`` points at a file other than ``README.md``, compares
    ``mtime(spec_file) - mtime(README.md)``.  If the spec is newer by more
    than the configured threshold (days), emits a single-line
    ``ui.info`` advisory.  Threshold resolution order (first wins):

    1. ``EVOLVE_README_STALE_THRESHOLD_DAYS`` environment variable
    2. ``[tool.evolve] readme_stale_threshold_days`` in evolve.toml /
       ``pyproject.toml``
    3. Built-in default (30)

    A threshold of ``0`` disables the advisory entirely.  The advisory is
    pure observability: it never blocks the run, never modifies any file,
    and is never emitted during rounds.  When ``spec`` is ``None`` or
    equals ``"README.md"``, README IS the spec and the advisory is a
    no-op.

    Args:
        project_dir: Root directory of the project.
        spec: Path to the spec file relative to ``project_dir``, or
            ``None`` when README.md is the spec.
        ui: The TUI to emit the advisory through.
    """
    # No-op when README IS the spec.
    if not spec or spec == "README.md":
        return

    spec_path = project_dir / spec
    readme_path = project_dir / "README.md"
    if not spec_path.is_file() or not readme_path.is_file():
        return

    # Resolve threshold: env > config > default.  Invalid values are
    # silently ignored so a typo never breaks the evolution loop.
    import os as _os

    threshold_days: int | None = None
    env_val = _os.environ.get("EVOLVE_README_STALE_THRESHOLD_DAYS", "").strip()
    if env_val:
        try:
            threshold_days = int(env_val)
        except ValueError:
            threshold_days = None
    if threshold_days is None:
        try:
            from evolve import _load_config as _load_cfg
            cfg = _load_cfg(project_dir)
            if "readme_stale_threshold_days" in cfg:
                threshold_days = int(cfg["readme_stale_threshold_days"])
        except Exception:
            threshold_days = None
    if threshold_days is None:
        threshold_days = _DEFAULT_README_STALE_THRESHOLD_DAYS

    # 0 (or negative) disables the advisory entirely per SPEC.
    if threshold_days <= 0:
        return

    drift_seconds = spec_path.stat().st_mtime - readme_path.stat().st_mtime
    if drift_seconds <= 0:
        return  # README is newer than spec — nothing to warn about
    drift_days = int(drift_seconds // 86400)
    if drift_days > threshold_days:
        ui.info(_README_STALE_ADVISORY_FMT.format(days=drift_days))


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
        if "[stale: spec changed]" in imp_text:
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
    """Independently re-verify convergence gates after the agent wrote ``CONVERGED``.

    When either documented gate in SPEC.md § "Convergence" is violated,
    this function unlinks the ``CONVERGED`` marker, saves a
    ``subprocess_error_round_N.txt`` diagnostic with a ``PREMATURE
    CONVERGED: <reason>`` prefix, and emits ``ui.error``. The next round
    will pick up the diagnostic via ``agent.py``'s ``build_prompt`` and
    surface a dedicated ``CRITICAL — Premature CONVERGED`` header so the
    agent addresses the violated gate before attempting convergence
    again.

    This is the orchestrator-side trust boundary — without it, Phase 4
    criteria remain 100% agent-judged.

    Args:
        converged_path: Path to the ``CONVERGED`` marker file.
        improvements_path: Path to ``improvements.md``.
        spec_path: Path to the spec file.
        run_dir: Session directory (used to write the diagnostic).
        round_num: Current round number.
        cmd: Original subprocess command (echoed into the diagnostic).
        output: Subprocess output (echoed into the diagnostic).
        attempt: Current attempt number (echoed into the diagnostic).
        ui: TUI instance for ``ui.error`` emission.

    Returns:
        True iff the backstop rejected convergence (marker unlinked,
        diagnostic saved); False when both gates pass.
    """
    if not converged_path.is_file():
        return False
    is_premature, reason = _detect_premature_converged(
        improvements_path, spec_path
    )
    if not is_premature:
        return False
    ui.error(f"Premature CONVERGED rejected: {reason}")
    print(f"[probe] convergence-gate backstop rejected: {reason}")
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
                ``"error"``, ``"party_mode"``).
        improvements_path: Path to the improvements.md file.
        check_passed: Whether the last check command passed (None if not run).
        check_tests: Number of tests passed in the last check (None if unknown).
        check_duration_s: Duration of the last check in seconds (None if unknown).
        started_at: ISO timestamp when the session started. If None, read from
                    existing state.json or use current time.
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
        "version": 1,
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
    state_path = run_dir / "state.json"
    state_path.write_text(json.dumps(state, indent=2) + "\n")


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


def _parse_report_summary(run_dir: Path) -> dict:
    """Parse evolution_report.md to extract completion summary stats.

    Returns a dict with keys: improvements, bugs_fixed, tests_passing.
    """
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

    # Get latest test count from the most recent check_round_N.txt
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


def _generate_evolution_report(
    project_dir: Path,
    run_dir: Path,
    max_rounds: int,
    final_round: int,
    converged: bool,
    capture_frames: bool = False,
) -> None:
    """Generate evolution_report.md summarizing the session.

    Parses conversation logs, commit messages (from git log), and check results
    to produce a timeline table and summary stats.

    Args:
        project_dir: Root directory of the project.
        run_dir: Session directory where the report will be written.
        max_rounds: Maximum rounds configured for the session.
        final_round: Last round that was actually executed.
        converged: Whether the session converged successfully.
    """
    session_name = run_dir.name
    improvements_path = project_dir / "runs" / "improvements.md"
    checked = _count_checked(improvements_path)
    unchecked = _count_unchecked(improvements_path)
    status = "CONVERGED" if converged else "MAX_ROUNDS"

    # Build timeline by scanning each round's data
    timeline_rows: list[str] = []
    files_modified: set[str] = set()
    bugs_fixed = 0
    improvements_done = 0
    prev_passed: int | None = None  # track test counts for arrow format

    for r in range(1, final_round + 1):
        # Try to get the commit message for this round from git log
        action = ""
        commit_msg_line = ""
        from_git_log = False
        try:
            git_result = subprocess.run(
                ["git", "log", "--oneline", f"--grep=round {r}", "--grep=evolve", "--all-match", "-1"],
                cwd=str(project_dir), capture_output=True, text=True, timeout=10,
            )
            if git_result.stdout.strip():
                commit_msg_line = git_result.stdout.strip()
                from_git_log = True
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass

        # Fall back: parse conversation log for COMMIT_MSG content
        if not commit_msg_line:
            convo_path = run_dir / f"conversation_loop_{r}.md"
            if convo_path.is_file():
                convo_text = convo_path.read_text(errors="replace")
                # Look for conventional commit patterns in the conversation
                for line in convo_text.splitlines():
                    m = re.match(r"^(fix|feat|refactor|perf|docs|test|chore)\(.+?\):\s+(.+)", line.strip())
                    if m:
                        commit_msg_line = line.strip()
                        break

        if commit_msg_line:
            # Strip the git hash prefix from 'git log --oneline' output (<hash> <msg>)
            if from_git_log:
                commit_msg_line = commit_msg_line.split(" ", 1)[-1]
            action = commit_msg_line[:70]
        else:
            action = f"round {r}"

        # Count fix vs feat
        if action.startswith("fix"):
            bugs_fixed += 1
        elif action.startswith("feat"):
            improvements_done += 1

        # Parse check results — show arrow format (prev→current) when possible
        tests_info = ""
        check_path = run_dir / f"check_round_{r}.txt"
        cur_passed: int | None = None
        if check_path.is_file():
            check_text = check_path.read_text(errors="replace")
            pass_fail = "PASS" if "PASS" in check_text else "FAIL"
            # Try to extract test counts (pytest format: "N passed")
            m = re.search(r"(\d+)\s+passed", check_text)
            if m:
                cur_passed = int(m.group(1))
                if prev_passed is not None and cur_passed != prev_passed:
                    tests_info = f"{prev_passed}\u2192{cur_passed}"
                else:
                    tests_info = f"{cur_passed} passed"
                m2 = re.search(r"(\d+)\s+failed", check_text)
                if m2:
                    tests_info += f", {m2.group(1)} failed"
            else:
                tests_info = pass_fail
        prev_passed = cur_passed if cur_passed is not None else prev_passed

        # Parse files changed from conversation log (deduplicated)
        round_files: list[str] = []
        seen_files: set[str] = set()
        convo_path = run_dir / f"conversation_loop_{r}.md"
        if convo_path.is_file():
            convo_text = convo_path.read_text(errors="replace")
            # Look for file edit patterns: Edit → filename, Write → filename
            for fm in re.finditer(r"(?:Edit|Write)\s*→?\s*[`]?([^\s`\n]+\.\w+)", convo_text):
                fname = fm.group(1)
                if fname not in seen_files:
                    seen_files.add(fname)
                    round_files.append(fname)
                files_modified.add(fname)

        files_str = ", ".join(round_files[:3]) if round_files else ""
        if len(round_files) > 3:
            files_str += f" (+{len(round_files) - 3})"

        timeline_rows.append(f"| {r} | {action} | {files_str} | {tests_info} |")

    # Build report
    report_lines = [
        "# Evolution Report",
        f"**Project:** {project_dir.name}",
        f"**Session:** {session_name}",
        f"**Rounds:** {final_round}/{max_rounds}",
        f"**Status:** {status}",
        "",
        "## Timeline",
        "| Round | Action | Files Changed | Tests |",
        "|-------|--------|---------------|-------|",
    ]
    report_lines.extend(timeline_rows)
    report_lines.append("")
    report_lines.append("## Summary")
    report_lines.append(f"- {checked} improvements completed")
    report_lines.append(f"- {bugs_fixed} bugs fixed")
    report_lines.append(f"- {len(files_modified)} files modified")
    if unchecked > 0:
        report_lines.append(f"- {unchecked} improvements remaining")
    report_lines.append("")

    # Add visual timeline section if frame capture is enabled
    if capture_frames:
        frames_dir = run_dir / "frames"
        if frames_dir.is_dir():
            frame_files = sorted(frames_dir.glob("*.png"))
            if frame_files:
                report_lines.append("## Visual timeline")
                report_lines.append("")
                for frame_file in frame_files:
                    # Use relative path from report location into frames/
                    label = frame_file.stem.replace("_", " ").title()
                    report_lines.append(f"### {label}")
                    report_lines.append(f"![{label}](frames/{frame_file.name})")
                    report_lines.append("")

    report_path = run_dir / "evolution_report.md"
    report_path.write_text("\n".join(report_lines))


def evolve_loop(
    project_dir: Path,
    max_rounds: int = 10,
    check_cmd: str | None = None,
    allow_installs: bool = False,
    timeout: int = 300,
    model: str = "claude-opus-4-6",
    resume: bool = False,
    forever: bool = False,
    spec: str | None = None,
    yolo: bool | None = None,
    capture_frames: bool = False,
    effort: str | None = "max",
) -> None:
    """Orchestrate evolution by launching each round as a subprocess.

    Creates a timestamped session directory, then delegates to ``_run_rounds``
    for the main loop.  Supports ``--resume`` to continue an interrupted
    session and ``--forever`` for autonomous indefinite evolution.

    Args:
        project_dir: Root directory of the project being evolved.
        max_rounds: Maximum number of evolution rounds.
        check_cmd: Shell command to verify the project after each round.
        allow_installs: If True, allow improvements requiring new packages.
        timeout: Timeout in seconds for the check command.
        model: Claude model identifier to use.
        resume: If True, resume the most recent interrupted session.
        forever: If True, run indefinitely on a dedicated branch.
        spec: Path to the spec file relative to project_dir (default: README.md).
        yolo: Deprecated alias for *allow_installs*. Will be removed in a future version.
        capture_frames: If True, capture TUI frames as PNG at key moments.
    """
    if yolo is not None:
        allow_installs = yolo
    improvements_path = project_dir / "runs" / "improvements.md"

    print(f"[probe] evolve_loop starting — project={project_dir.name}, max_rounds={max_rounds}, check={check_cmd or '(auto-detect)'}")

    # Load event hooks from project config
    hooks = load_hooks(project_dir)
    if hooks:
        print(f"[probe] loaded {len(hooks)} hook(s): {', '.join(hooks.keys())}")

    # Startup-time stale-README advisory (SPEC § "Stale-README pre-flight
    # check") — pure observability, runs once before the first round.
    _emit_stale_readme_advisory(project_dir, spec, get_tui())

    # Auto-detect check command if not provided
    if check_cmd is None:
        detected = _auto_detect_check(project_dir)
        if detected:
            ui_early = get_tui()
            ui_early.info(f"  Auto-detected check command: {detected}")
            check_cmd = detected
            print(f"[probe] auto-detected check command: {detected}")

    start_round = 1

    # In forever mode, create a separate branch and run indefinitely
    if forever:
        print("[probe] forever mode enabled — creating dedicated branch")
        _setup_forever_branch(project_dir)
        # Use a very large max_rounds so the loop runs until convergence
        max_rounds = 999999

    if resume:
        # Find the most recent session and detect last completed round
        runs_dir = project_dir / "runs"
        if runs_dir.is_dir():
            sessions = sorted(
                [d for d in runs_dir.iterdir() if d.is_dir() and d.name[0].isdigit()],
                reverse=True,
            )
            if sessions:
                run_dir = sessions[0]
                # Detect last completed round from conversation logs
                def _convo_sort_key(p: Path) -> int:
                    try:
                        return int(p.stem.rsplit("_", 1)[1])
                    except (ValueError, IndexError):
                        return -1

                convos = sorted(run_dir.glob("conversation_loop_*.md"), key=_convo_sort_key)
                if convos:
                    last = convos[-1].stem  # conversation_loop_N
                    try:
                        last_round = int(last.rsplit("_", 1)[1])
                        start_round = last_round + 1
                    except (ValueError, IndexError):
                        pass
                ui = get_tui(run_dir=run_dir, capture_frames=capture_frames)
                ui.run_dir_info(f"{run_dir} (resumed from round {start_round})")

                # Ensure git
                _ensure_git(project_dir, ui)

                # Jump to loop body
                return _run_rounds(
                    project_dir, run_dir, improvements_path, ui,
                    start_round, max_rounds, check_cmd, allow_installs, timeout, model,
                    forever=forever, hooks=hooks, spec=spec,
                    capture_frames=capture_frames,
                    effort=effort,
                )

    # Create timestamped run directory
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = project_dir / "runs" / timestamp
    run_dir.mkdir(parents=True, exist_ok=True)
    ui = get_tui(run_dir=run_dir, capture_frames=capture_frames)
    ui.run_dir_info(str(run_dir))

    # Ensure git
    _ensure_git(project_dir, ui)

    _run_rounds(
        project_dir, run_dir, improvements_path, ui,
        1, max_rounds, check_cmd, allow_installs, timeout, model,
        forever=forever, hooks=hooks, spec=spec,
        capture_frames=capture_frames,
        effort=effort,
    )


# Maximum number of debug retries when a round fails, stalls, or makes no progress.
MAX_DEBUG_RETRIES = 2
# Seconds of silence before the watchdog considers a subprocess stalled.
WATCHDOG_TIMEOUT = 120

# Memory-wipe sanity gate constants — keep the runtime check aligned with
# SPEC.md § "memory.md" — "Byte-size sanity gate".  Changing either value
# here is the single source of truth for both the detection logic in
# _run_rounds and the tests that exercise it.
#
#   _MEMORY_COMPACTION_MARKER — the literal string the agent must include
#       in its commit message (on its own line, per SPEC) to legitimise a
#       large memory.md shrink.  Absence of the marker on a >threshold
#       shrink triggers a debug retry with the "silently wiped memory.md"
#       diagnostic header.
#   _MEMORY_WIPE_THRESHOLD   — fractional shrink floor below which memory.md
#       is considered wiped.  0.5 means "memory.md after the round is
#       smaller than half of its pre-round size" → retry.
_MEMORY_COMPACTION_MARKER = "memory: compaction"
_MEMORY_WIPE_THRESHOLD = 0.5

# Backlog discipline rule 1 (empty-queue gate) constants — keep the runtime
# check aligned with SPEC.md § "Backlog discipline".  The agent is forbidden
# from adding a new `- [ ]` item while any other `- [ ]` item already exists
# in improvements.md.  When detected, the orchestrator triggers a debug retry
# whose diagnostic prefix carries the documented header so agent.py's prompt
# builder can render the dedicated section.
_BACKLOG_VIOLATION_PREFIX = "BACKLOG VIOLATION"
_BACKLOG_VIOLATION_HEADER = (
    "CRITICAL \u2014 Backlog discipline violation: "
    "new item added while queue non-empty"
)


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


def _run_monitored_subprocess(
    cmd: list[str],
    cwd: str,
    ui: TUIProtocol,
    round_num: int,
    watchdog_timeout: int = WATCHDOG_TIMEOUT,
) -> tuple[int, str, bool]:
    """Run a subprocess with real-time output streaming and stall detection.

    Spawns the command, streams stdout in real-time, and monitors for
    inactivity.  If no output is produced for ``watchdog_timeout`` seconds
    the process is killed.

    Args:
        cmd: Command list to execute.
        cwd: Working directory for the subprocess.
        ui: TUI instance for status messages.
        round_num: Current round number (for diagnostic messages).
        watchdog_timeout: Seconds of silence before killing the process.

    Returns:
        A tuple ``(returncode, output, stalled)`` where *stalled* is True
        when the watchdog killed the process due to inactivity.
    """
    # -u ensures Python doesn't buffer stdout/stderr in the child process.
    if cmd[0] == sys.executable and "-u" not in cmd:
        cmd = [cmd[0], "-u"] + cmd[1:]

    proc = subprocess.Popen(
        cmd, cwd=cwd,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, bufsize=1,
    )

    output_lines: list[str] = []
    last_activity = time.monotonic()
    lock = threading.Lock()

    def _reader():
        """Read subprocess stdout line-by-line, updating the watchdog timer.

        Runs in a daemon thread.  Each line is appended to *output_lines*
        (under *lock*) and echoed to ``sys.stdout`` so the orchestrator's
        watchdog sees continuous activity.  Updates *last_activity* on every
        line to prevent the watchdog from killing an active process.
        """
        nonlocal last_activity
        assert proc.stdout is not None
        for line in proc.stdout:
            with lock:
                output_lines.append(line)
                last_activity = time.monotonic()
            sys.stdout.write(line)
            sys.stdout.flush()

    reader_thread = threading.Thread(target=_reader, daemon=True)
    reader_thread.start()

    stalled = False
    while proc.poll() is None:
        time.sleep(1)
        with lock:
            idle = time.monotonic() - last_activity
        if idle > watchdog_timeout:
            stalled = True
            ui.warn(
                f"Round {round_num} stalled ({int(idle)}s without output) "
                "— killing subprocess"
            )
            proc.kill()
            break

    reader_thread.join(timeout=5)
    output = "".join(output_lines)
    rc = proc.returncode if proc.returncode is not None else -9
    return rc, output, stalled


def _save_subprocess_diagnostic(
    run_dir: Path,
    round_num: int,
    cmd: list[str],
    output: str,
    reason: str,
    attempt: int,
) -> None:
    """Write a diagnostic file for a failed/stalled subprocess round.

    Args:
        run_dir: Session directory to write the diagnostic into.
        round_num: The round number that failed.
        cmd: The command that was executed.
        output: Captured subprocess output (may be truncated).
        reason: Human-readable description of the failure.
        attempt: Which retry attempt produced this failure.
    """
    error_log = run_dir / f"subprocess_error_round_{round_num}.txt"
    # When this diagnostic is about to be read by the FINAL retry (attempt 3),
    # prepend an explicit Phase 1 escape hatch banner so the agent's prompt
    # builder can pick it up and surface it prominently. attempt=K means the
    # Kth attempt just failed, so the *next* attempt will be K+1.
    next_attempt = attempt + 1
    escape_hatch_banner = ""
    if next_attempt >= 3:
        escape_hatch_banner = (
            "### Phase 1 escape hatch notice\n"
            f"The next run of round {round_num} will be attempt "
            f"{next_attempt} of 3 — the FINAL retry. If Phase 1 check "
            "failures are still unresolved AND the failing output references "
            "NO files named in the current improvement target, the Phase 1 "
            "escape hatch is PERMITTED (see prompts/system.md § 'Phase 1 "
            "escape hatch'). Log blocked errors to memory.md, append a "
            "'Phase 1 bypass' item to improvements.md, proceed with the "
            "target, and include a 'Phase 1 bypass: <summary>' line in "
            "COMMIT_MSG.\n\n"
        )
    # Retry continuity: surface the path of the per-attempt log so the next
    # attempt can read it and continue the investigation from where this one
    # stopped.  The agent.py prompt builder also injects a dedicated
    # "## Previous attempt log" section based on this same convention; the
    # path here is for the diagnostic reader and as a single source of truth.
    prev_attempt_log = run_dir / f"conversation_loop_{round_num}_attempt_{attempt}.md"
    prev_attempt_section = (
        "### Previous attempt log\n"
        f"Full conversation log of attempt {attempt}: {prev_attempt_log}\n"
        "Read this file FIRST in the next attempt — do not redo the "
        "investigation, continue from where it stopped.\n\n"
    )
    error_log.write_text(
        f"Round {round_num} — {reason} (attempt {attempt})\n"
        f"Command: {' '.join(str(c) for c in cmd)}\n\n"
        f"{escape_hatch_banner}"
        f"{prev_attempt_section}"
        f"Output (last 3000 chars):\n{(output or '')[-3000:]}\n"
    )


def _run_rounds(
    project_dir: Path,
    run_dir: Path,
    improvements_path: Path,
    ui: TUIProtocol,
    start_round: int,
    max_rounds: int,
    check_cmd: str | None,
    allow_installs: bool,
    timeout: int,
    model: str,
    forever: bool = False,
    hooks: dict[str, str] | None = None,
    spec: str | None = None,
    capture_frames: bool = False,
    effort: str | None = "max",
) -> None:
    """Run evolution rounds from start_round to max_rounds.

    Each round is launched as a subprocess via ``_run_monitored_subprocess``.
    Failed rounds are retried up to ``MAX_DEBUG_RETRIES`` times.  On
    convergence the session exits (or restarts in forever mode).

    Args:
        project_dir: Root directory of the project.
        run_dir: Session directory for round artifacts.
        improvements_path: Path to the improvements.md file.
        ui: TUI instance for status output.
        start_round: First round number to execute.
        max_rounds: Maximum round number (inclusive).
        check_cmd: Shell command to verify the project.
        allow_installs: If True, allow improvements requiring new packages.
        timeout: Timeout for the check command in seconds.
        model: Claude model identifier.
        forever: If True, restart after convergence instead of exiting.
        hooks: Event hook configuration dict (from ``load_hooks``).
        spec: Path to the spec file relative to project_dir (default: README.md).
        capture_frames: If True, capture TUI frames as PNG at key moments.
    """
    if hooks is None:
        hooks = {}
    _rounds_start_time = time.monotonic()
    _started_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"[probe] _run_rounds starting from round {start_round} to {max_rounds}")
    while True:
        for round_num in range(start_round, max_rounds + 1):
            # Phase 2 — Spec freshness gate: if spec is newer than
            # improvements.md, mark unchecked items as stale so the agent
            # rebuilds the backlog from the updated spec.
            spec_fresh = _check_spec_freshness(project_dir, improvements_path, spec=spec)
            if not spec_fresh:
                print(f"[probe] spec freshness gate: spec is newer than improvements.md — backlog marked stale")

            current = _get_current_improvement(improvements_path, allow_installs=allow_installs)
            checked = _count_checked(improvements_path)
            unchecked = _count_unchecked(improvements_path)
            print(f"[probe] round {round_num}/{max_rounds} — checked={checked}, unchecked={unchecked}, target={current or '(none)'}")

            if current:
                ui.round_header(round_num, max_rounds, target=current,
                                checked=checked, total=checked + unchecked)
            elif unchecked > 0:
                # All remaining unchecked items are blocked (needs-package without --allow-installs)
                blocked = _count_blocked(improvements_path)
                if blocked == unchecked:
                    ui.round_header(round_num, max_rounds)
                    ui.blocked_message(blocked)
                    sys.exit(1)
                ui.round_header(round_num, max_rounds, target="(initial analysis)")
            else:
                ui.round_header(round_num, max_rounds, target="(initial analysis)")

            # Fire on_round_start hook
            session_name = run_dir.name
            fire_hook(hooks, "on_round_start", session=session_name, round_num=round_num, status="running")

            # Launch round as subprocess — picks up code changes from previous round
            evolve_script = Path(__file__).parent / "evolve.py"
            cmd = [
                sys.executable, str(evolve_script),
                "_round",
                str(project_dir),
                "--round-num", str(round_num),
                "--timeout", str(timeout),
                "--run-dir", str(run_dir),
                "--model", model,
            ]
            if check_cmd:
                cmd += ["--check", check_cmd]
            if allow_installs:
                cmd += ["--allow-installs"]
            if spec:
                cmd += ["--spec", spec]
            if effort:
                cmd += ["--effort", effort]

            # --- Debug retry loop: run the round, diagnose failures, retry ---
            print(f"[probe] launching subprocess for round {round_num}")
            round_succeeded = False
            for attempt in range(1, MAX_DEBUG_RETRIES + 2):  # 1..MAX_DEBUG_RETRIES+1
                # Snapshot conversation log size before subprocess so we can detect new output
                convo = run_dir / f"conversation_loop_{round_num}.md"
                convo_size_before = convo.stat().st_size if convo.is_file() else 0

                # Snapshot improvements.md bytes before subprocess for zero-progress detection
                imp_snapshot_before = improvements_path.read_bytes() if improvements_path.is_file() else b""

                # Snapshot memory.md byte size before subprocess for the
                # memory-wipe sanity gate.  If the agent shrinks memory.md
                # by more than 50% in a single round without explicitly
                # declaring `memory: compaction` in its commit message, we
                # treat it as a silent wipe and trigger a debug retry
                # (same family as zero-progress detection).  See
                # SPEC.md § "memory.md" — "Byte-size sanity gate".
                memory_path = project_dir / "runs" / "memory.md"
                mem_size_before = memory_path.stat().st_size if memory_path.is_file() else 0

                returncode, output, stalled = _run_monitored_subprocess(
                    cmd, str(project_dir), ui, round_num,
                )

                # --- Diagnose subprocess outcome ---
                if stalled:
                    ui.round_failed(round_num, returncode)
                    _save_subprocess_diagnostic(
                        run_dir, round_num, cmd, output,
                        reason=f"stalled ({WATCHDOG_TIMEOUT}s without output, killed)",
                        attempt=attempt,
                    )
                elif returncode != 0:
                    ui.round_failed(round_num, returncode)
                    _save_subprocess_diagnostic(
                        run_dir, round_num, cmd, output,
                        reason=f"crashed (exit code {returncode})",
                        attempt=attempt,
                    )
                else:
                    # Subprocess exited OK — check for actual progress
                    prev_checked = checked
                    prev_unchecked = unchecked
                    unchecked = _count_unchecked(improvements_path)
                    checked = _count_checked(improvements_path)
                    ui.progress_summary(checked, unchecked)

                    # --- Zero-progress detection ---
                    # 1. Check if improvements.md is byte-identical to pre-round snapshot
                    imp_after = improvements_path.read_bytes() if improvements_path.is_file() else b""
                    imp_unchanged = (imp_after == imp_snapshot_before)

                    # 2. Check if agent committed without COMMIT_MSG (fallback commit)
                    no_commit_msg = False
                    try:
                        git_log_result = subprocess.run(
                            ["git", "log", "-1", "--format=%s"],
                            cwd=str(project_dir), capture_output=True, text=True, timeout=10,
                        )
                        if git_log_result.returncode == 0:
                            last_commit_msg = git_log_result.stdout.strip()
                            if last_commit_msg == f"chore(evolve): round {round_num}":
                                no_commit_msg = True
                    except (subprocess.TimeoutExpired, FileNotFoundError):
                        pass

                    # 3. Memory-wipe sanity gate: detect silent wipes of
                    #    memory.md.  A >50% shrink without an explicit
                    #    "memory: compaction" line in the commit message
                    #    is treated as a wipe and triggers a retry.  This
                    #    catches agents that "compact" memory.md by
                    #    emptying sections they couldn't read.
                    mem_size_after = memory_path.stat().st_size if memory_path.is_file() else 0
                    memory_wiped = False
                    if (
                        mem_size_before > 0
                        and mem_size_after < mem_size_before * _MEMORY_WIPE_THRESHOLD
                    ):
                        commit_body = ""
                        try:
                            git_body_result = subprocess.run(
                                ["git", "log", "-1", "--format=%B"],
                                cwd=str(project_dir), capture_output=True, text=True, timeout=10,
                            )
                            if git_body_result.returncode == 0:
                                commit_body = git_body_result.stdout
                        except (subprocess.TimeoutExpired, FileNotFoundError):
                            # Can't read commit body — treat the shrink as
                            # a wipe to stay on the safe side.
                            commit_body = ""
                        if _MEMORY_COMPACTION_MARKER not in commit_body:
                            memory_wiped = True

                    # 4. Backlog discipline rule 1: detect "new [ ] item added
                    #    while queue non-empty".  See SPEC.md § "Backlog
                    #    discipline" rule 1.  We only check when improvements.md
                    #    actually changed (otherwise imp_unchanged path takes
                    #    over) and run the comparison on the snapshotted
                    #    pre-round bytes vs the current file.
                    backlog_violated = False
                    backlog_new_items: list[str] = []
                    if not imp_unchanged:
                        try:
                            pre_text = imp_snapshot_before.decode(
                                "utf-8", errors="replace"
                            )
                            post_text = imp_after.decode(
                                "utf-8", errors="replace"
                            )
                            backlog_violated, backlog_new_items = (
                                _detect_backlog_violation(pre_text, post_text)
                            )
                        except Exception as e:  # pragma: no cover — defensive
                            print(
                                f"[probe] backlog-violation check skipped: {e}"
                            )

                    # Any condition alone triggers zero-progress / memory-wipe / backlog retry
                    if no_commit_msg or imp_unchanged or memory_wiped or backlog_violated:
                        no_progress_reasons: list[str] = []
                        if no_commit_msg:
                            no_progress_reasons.append(
                                "no COMMIT_MSG written (fallback commit message)"
                            )
                        if imp_unchanged:
                            no_progress_reasons.append(
                                "improvements.md byte-identical to pre-round state"
                            )
                        if memory_wiped:
                            threshold_pct = int(_MEMORY_WIPE_THRESHOLD * 100)
                            no_progress_reasons.append(
                                f"memory.md shrunk by >{threshold_pct}% "
                                f"({mem_size_before}\u2192{mem_size_after} bytes) "
                                f"without '{_MEMORY_COMPACTION_MARKER}' in commit message"
                            )
                        if backlog_violated:
                            new_summary = "; ".join(
                                ln[:160] for ln in backlog_new_items[:3]
                            )
                            no_progress_reasons.append(
                                f"backlog discipline rule 1 violated: "
                                f"{len(backlog_new_items)} new `- [ ]` item(s) "
                                f"added while queue non-empty "
                                f"(new: {new_summary})"
                            )
                        reason_str = " AND ".join(no_progress_reasons)
                        # Diagnostic prefix priority: memory-wipe > backlog >
                        # no-progress, so agent.py's prompt builder picks the
                        # most specific dedicated header.
                        if memory_wiped:
                            prefix = "MEMORY WIPED"
                        elif backlog_violated:
                            prefix = _BACKLOG_VIOLATION_PREFIX
                        else:
                            prefix = "NO PROGRESS"
                        _save_subprocess_diagnostic(
                            run_dir, round_num, cmd, output,
                            reason=f"{prefix}: {reason_str}",
                            attempt=attempt,
                        )
                    else:
                        made_progress = (
                            checked != prev_checked
                            or unchecked != prev_unchecked
                            or (convo.is_file() and convo.stat().st_size > convo_size_before)
                        )
                        if made_progress:
                            round_succeeded = True
                            break

                        # No progress — save diagnostic for retry
                        _save_subprocess_diagnostic(
                            run_dir, round_num, cmd, output,
                            reason="no progress (agent ran but changed nothing)",
                            attempt=attempt,
                        )

                # Capture error frame before retry
                ui.capture_frame(f"error_round_{round_num}")

                # Fire on_error hook for failed round
                fire_hook(hooks, "on_error", session=session_name, round_num=round_num, status="error")

                # If retries remain, inform and loop
                if attempt <= MAX_DEBUG_RETRIES:
                    ui.warn(
                        f"Debug retry {attempt}/{MAX_DEBUG_RETRIES} for round {round_num} "
                        "— re-running with diagnostic context"
                    )
                else:
                    # All retries exhausted
                    ui.no_progress()
                    if not forever:
                        sys.exit(2)
                    # In forever mode, don't exit — move on to next round
                    ui.warn(
                        f"Round {round_num} failed after {MAX_DEBUG_RETRIES + 1} attempts "
                        "— skipping to next round"
                    )
                    break

            if not round_succeeded:
                continue  # skip convergence check, move to next round

            # Fire on_round_end hook for successful round
            fire_hook(hooks, "on_round_end", session=session_name, round_num=round_num, status="success")

            # Capture round-end frame
            ui.capture_frame(f"round_{round_num}_end")

            # Parse last check results for state.json
            _check_passed: bool | None = None
            _check_tests: int | None = None
            _check_duration: float | None = None
            check_file = run_dir / f"check_round_{round_num}.txt"
            if check_file.is_file():
                _ct = check_file.read_text(errors="replace")
                _check_passed, _check_tests, _check_duration = _parse_check_output(_ct)

            # Update state.json after every round
            _write_state_json(
                run_dir=run_dir,
                project_dir=project_dir,
                round_num=round_num,
                max_rounds=max_rounds,
                phase="improvement",
                status="running",
                improvements_path=improvements_path,
                check_passed=_check_passed,
                check_tests=_check_tests,
                check_duration_s=_check_duration,
                started_at=_started_at,
            )

            # Clean up diagnostic file on success (no longer relevant)
            error_log = run_dir / f"subprocess_error_round_{round_num}.txt"
            if error_log.is_file():
                error_log.unlink()

            # Check convergence — requires spec freshness gate
            # (mtime(improvements.md) >= mtime(spec)) AND CONVERGED file
            converged_path = run_dir / "CONVERGED"
            if converged_path.is_file():
                # Verify spec freshness gate — don't mark stale again,
                # just check the mtime relationship
                spec_file = spec or "README.md"
                spec_path = project_dir / spec_file
                if converged_path.is_file() and spec_path.is_file() and improvements_path.is_file():
                    if spec_path.stat().st_mtime > improvements_path.stat().st_mtime:
                        print("[probe] convergence rejected: spec is newer than improvements.md — removing CONVERGED marker")
                        converged_path.unlink()

                # Convergence-gate orchestrator backstop — re-verify the
                # two documented gates (SPEC.md § "Convergence")
                # independently of the agent's judgment.  Closes the trust
                # gap where Phase 4 criteria are 100% agent-judged today.
                # If either gate fails, CONVERGED is unlinked, a
                # diagnostic is saved with a ``PREMATURE CONVERGED``
                # prefix, and the next round picks it up via
                # ``build_prompt`` → dedicated CRITICAL header.
                _enforce_convergence_backstop(
                    converged_path,
                    improvements_path,
                    spec_path,
                    run_dir,
                    round_num,
                    cmd,
                    output,
                    attempt,
                    ui,
                )
            if converged_path.is_file():
                reason = converged_path.read_text().strip()
                print(f"[probe] CONVERGED at round {round_num}: {reason[:80]}")
                ui.converged(round_num, reason)

                # Capture convergence frame
                ui.capture_frame("converged")

                # Fire on_converged hook
                fire_hook(hooks, "on_converged", session=session_name, round_num=round_num, status="converged")

                # Update state.json to converged
                _write_state_json(
                    run_dir=run_dir,
                    project_dir=project_dir,
                    round_num=round_num,
                    max_rounds=max_rounds,
                    phase="convergence",
                    status="converged",
                    improvements_path=improvements_path,
                    check_passed=_check_passed,
                    check_tests=_check_tests,
                    check_duration_s=_check_duration,
                    started_at=_started_at,
                )

                # Generate evolution report
                _generate_evolution_report(project_dir, run_dir, max_rounds, round_num, converged=True, capture_frames=capture_frames)

                # Display completion summary panel
                duration_s = time.monotonic() - _rounds_start_time
                summary_stats = _parse_report_summary(run_dir)
                ui.completion_summary(
                    status="CONVERGED",
                    round_num=round_num,
                    duration_s=duration_s,
                    improvements=summary_stats["improvements"],
                    bugs_fixed=summary_stats["bugs_fixed"],
                    tests_passing=summary_stats["tests_passing"],
                    report_path=str(run_dir / "evolution_report.md"),
                )

                # Launch party mode
                _run_party_mode(project_dir, run_dir, ui, spec=spec)

                if forever:
                    # Auto-merge the spec proposal into the spec file, then
                    # restart. README.md is never written by the evolution
                    # loop — see SPEC.md § "README as a user-level summary".
                    adoption_result = _forever_restart(
                        project_dir, run_dir, improvements_path, ui, spec=spec
                    )
                    # Backwards-compat: historical _forever_restart returned
                    # None; current signature returns (spec_adopted, _).
                    if isinstance(adoption_result, tuple):
                        spec_adopted, _ = adoption_result
                    else:
                        spec_adopted = False

                    # Create a new session directory for the next cycle
                    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                    run_dir = project_dir / "runs" / timestamp
                    run_dir.mkdir(parents=True, exist_ok=True)
                    # Update TUI with new run_dir for frame capture
                    if capture_frames:
                        ui = get_tui(run_dir=run_dir, capture_frames=capture_frames)
                    ui.run_dir_info(str(run_dir))

                    # Git commit the proposal adoption + reset. When --spec
                    # differs from README.md and the spec proposal was
                    # adopted, use a focused feat(spec) commit; otherwise
                    # (no --spec flag, or nothing adopted) fall back to the
                    # legacy chore message.
                    spec_file_for_msg = spec or "README.md"
                    if spec_file_for_msg != "README.md" and spec_adopted:
                        spec_stem_msg = Path(spec_file_for_msg).stem
                        spec_suffix_msg = Path(spec_file_for_msg).suffix or ".md"
                        proposal_name_msg = f"{spec_stem_msg}_proposal{spec_suffix_msg}"
                        commit_msg = (
                            f"feat(spec): adopt {proposal_name_msg}\n"
                            "\n"
                            f"- {spec_file_for_msg} updated from {proposal_name_msg}\n"
                            "- improvements.md reset"
                        )
                    else:
                        commit_msg = (
                            "chore(evolve): forever mode — adopt proposal, "
                            "reset improvements"
                        )
                    _git_commit(project_dir, commit_msg, ui)

                    # Restart from round 1 via the outer while loop
                    start_round = 1
                    break  # break out of for loop, continue while loop

                sys.exit(0)
        else:
            # for loop completed without break — max rounds reached
            unchecked = _count_unchecked(improvements_path)
            checked = _count_checked(improvements_path)
            print(f"[probe] max rounds reached ({max_rounds}) — checked={checked}, unchecked={unchecked}")

            # Update state.json to max_rounds
            _write_state_json(
                run_dir=run_dir,
                project_dir=project_dir,
                round_num=max_rounds,
                max_rounds=max_rounds,
                phase="improvement",
                status="max_rounds",
                improvements_path=improvements_path,
                started_at=_started_at,
            )

            # Generate evolution report
            _generate_evolution_report(project_dir, run_dir, max_rounds, max_rounds, converged=False, capture_frames=capture_frames)

            # Display completion summary panel
            duration_s = time.monotonic() - _rounds_start_time
            summary_stats = _parse_report_summary(run_dir)
            ui.completion_summary(
                status="MAX_ROUNDS",
                round_num=max_rounds,
                duration_s=duration_s,
                improvements=summary_stats["improvements"],
                bugs_fixed=summary_stats["bugs_fixed"],
                tests_passing=summary_stats["tests_passing"],
                report_path=str(run_dir / "evolution_report.md"),
            )

            ui.max_rounds(max_rounds, checked, unchecked)
            sys.exit(1)


def run_single_round(
    project_dir: Path,
    round_num: int,
    check_cmd: str | None = None,
    allow_installs: bool = False,
    timeout: int = 300,
    run_dir: Path | None = None,
    model: str = "claude-opus-4-6",
    spec: str | None = None,
    yolo: bool | None = None,
    effort: str | None = "max",
) -> None:
    """Execute a single evolution round (called as subprocess).

    Runs the check command, invokes the agent, commits changes, and
    re-runs the check to verify fixes.  This function is the entry
    point for each subprocess spawned by ``_run_rounds``.

    Args:
        project_dir: Root directory of the project.
        round_num: Current evolution round number.
        check_cmd: Shell command to verify the project.
        allow_installs: If True, allow improvements requiring new packages.
        timeout: Timeout for the check command in seconds.
        run_dir: Session directory for round artifacts.
        model: Claude model identifier to use.
        spec: Path to the spec file relative to project_dir (default: README.md).
        yolo: Deprecated alias for *allow_installs*. Will be removed in a future version.
    """
    if yolo is not None:
        allow_installs = yolo
    from agent import analyze_and_fix
    import agent as _agent_mod
    _agent_mod.MODEL = model
    _agent_mod.EFFORT = effort

    rdir = run_dir or (project_dir / "runs")
    rdir.mkdir(parents=True, exist_ok=True)
    improvements_path = project_dir / "runs" / "improvements.md"
    ui = get_tui()

    print(f"[probe] round {round_num} starting — project={project_dir.name}, model={model}")

    # 1. Run check command if provided
    check_output = ""
    if check_cmd:
        print(f"[probe] running pre-check: {check_cmd}")
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
            print(f"[probe] pre-check {'PASSED' if ok else 'FAILED'} (exit {result.returncode})")
        except subprocess.TimeoutExpired:
            check_output = f"TIMEOUT after {timeout}s"
            ui.check_result("check", check_cmd, timeout=True)
            print(f"[probe] pre-check TIMEOUT after {timeout}s")
    else:
        ui.no_check()
        print("[probe] no check command configured")

    # 2. Let opus agent analyze and fix
    current = _get_current_improvement(improvements_path, allow_installs=allow_installs)
    print(f"[probe] invoking agent — target: {current or '(initial analysis)'}")
    ui.agent_working()
    analyze_and_fix(
        project_dir=project_dir,
        check_output=check_output,
        check_cmd=check_cmd,
        allow_installs=allow_installs,
        round_num=round_num,
        run_dir=rdir,
        spec=spec,
    )
    print("[probe] agent finished")

    # 3. Git commit + push
    commit_msg_path = rdir / "COMMIT_MSG"
    if commit_msg_path.is_file():
        msg = commit_msg_path.read_text().strip()
        commit_msg_path.unlink()
    else:
        new_current = _get_current_improvement(improvements_path, allow_installs=allow_installs)
        if current and new_current != current:
            msg = f"feat(evolve): ✓ {current}"
        else:
            msg = f"chore(evolve): round {round_num}"
    print(f"[probe] git commit: {msg[:80]}")
    _git_commit(project_dir, msg, ui)

    # 4. Re-run check after fixes
    if check_cmd:
        print(f"[probe] running post-check: {check_cmd}")
        ui.check_result("verify", check_cmd, passed=None)
        try:
            result = subprocess.run(
                check_cmd, shell=True, cwd=str(project_dir),
                capture_output=True, text=True, timeout=timeout,
            )
            ok = result.returncode == 0
            ui.check_result("verify", check_cmd, passed=ok)
            print(f"[probe] post-check {'PASSED' if ok else 'FAILED'} (exit {result.returncode})")

            probe_path = rdir / f"check_round_{round_num}.txt"
            with open(probe_path, "w") as f:
                f.write(f"Round {round_num} post-fix check: {'PASS' if ok else 'FAIL'}\n")
                f.write(f"Command: {check_cmd}\n")
                f.write(f"Exit code: {result.returncode}\n")
                if result.stdout:
                    f.write(f"\nstdout:\n{result.stdout[-2000:]}\n")
                if result.stderr:
                    f.write(f"\nstderr:\n{result.stderr[-2000:]}\n")
        except subprocess.TimeoutExpired:
            ui.check_result("verify", check_cmd, timeout=True)
            print(f"[probe] post-check TIMEOUT after {timeout}s")

    print(f"[probe] round {round_num} complete")


def run_dry_run(
    project_dir: Path,
    check_cmd: str | None = None,
    timeout: int = 300,
    model: str = "claude-opus-4-6",
    spec: str | None = None,
    effort: str | None = "max",
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
    ui = get_tui()

    print(f"[probe] dry-run starting — project={project_dir.name}")

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
    run_dir = project_dir / "runs" / timestamp
    run_dir.mkdir(parents=True, exist_ok=True)
    ui.run_dir_info(str(run_dir))
    ui.info("  Mode: DRY RUN (read-only analysis, no file changes)")
    print(f"[probe] dry-run session: {run_dir}")

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
    from agent import run_dry_run_agent
    import agent as _agent_mod
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
    timeout: int = 300,
    model: str = "claude-opus-4-6",
    spec: str | None = None,
    effort: str | None = "max",
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
    ui = get_tui()

    print(f"[probe] validate starting — project={project_dir.name}")

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
    run_dir = project_dir / "runs" / timestamp
    run_dir.mkdir(parents=True, exist_ok=True)
    ui.run_dir_info(str(run_dir))
    ui.info("  Mode: VALIDATE (spec compliance check, no file changes)")
    print(f"[probe] validate session: {run_dir}")

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
    from agent import run_validate_agent
    import agent as _agent_mod
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
        print(f"[probe] validate result: FAIL ({passed} passed, {failed} failed)")
        return 1
    elif passed > 0:
        ui.info(f"  Result: PASS — {passed} claims validated")
        print(f"[probe] validate result: PASS ({passed} claims validated)")
        return 0
    else:
        # No markers found — likely an error in report generation
        ui.warn("Could not determine pass/fail from validate_report.md")
        return 2


def run_sync_readme(
    project_dir: Path,
    spec: str | None = None,
    apply: bool = False,
    model: str = "claude-opus-4-6",
    effort: str | None = "max",
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
    ui = get_tui()

    print(f"[probe] sync-readme starting — project={project_dir.name}")

    # Refuse when the spec IS the README — no sync to perform.
    if spec is None or spec == "README.md":
        ui.info(
            "  sync-readme is a no-op when --spec is unset or equals "
            "README.md (README is the spec)"
        )
        print("[probe] sync-readme: no-op (README is the spec)")
        return 1

    # Validate spec exists.
    spec_path = project_dir / spec
    if not spec_path.is_file():
        ui.error(f"ERROR: spec file not found: {spec_path}")
        print(f"[probe] sync-readme: ERROR — spec missing")
        return 2

    # Create timestamped run directory for the conversation log + sentinel.
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = project_dir / "runs" / timestamp
    run_dir.mkdir(parents=True, exist_ok=True)
    ui.run_dir_info(str(run_dir))
    mode_label = "APPLY (will commit README.md)" if apply else "PROPOSAL (writes README_proposal.md)"
    ui.info(f"  Mode: SYNC-README — {mode_label}")
    print(f"[probe] sync-readme session: {run_dir} (apply={apply})")

    # Snapshot README.md mtime before agent runs (used to detect whether
    # apply mode actually overwrote the file).
    readme_path = project_dir / "README.md"
    readme_mtime_before = readme_path.stat().st_mtime if readme_path.is_file() else None

    # Launch agent.
    from agent import run_sync_readme_agent, SYNC_README_NO_CHANGES_SENTINEL
    import agent as _agent_mod
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
        print(f"[probe] sync-readme: ERROR — agent exception {e}")
        return 2

    # Inspect filesystem outputs to compute exit code.
    sentinel = run_dir / SYNC_README_NO_CHANGES_SENTINEL
    proposal = project_dir / "README_proposal.md"

    if sentinel.is_file():
        ui.info("  README already in sync — no proposal written")
        print("[probe] sync-readme: no changes needed (exit 1)")
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
        print("[probe] sync-readme: applied + committed (exit 0)")
        return 0

    # Default mode: agent should have written README_proposal.md.
    if proposal.is_file():
        ui.info(f"  README proposal written: {proposal}")
        print("[probe] sync-readme: proposal written (exit 0)")
        return 0

    ui.warn("sync-readme: agent produced no README_proposal.md and no NO_SYNC_NEEDED sentinel")
    print("[probe] sync-readme: ERROR — no agent output (exit 2)")
    return 2


def _run_party_mode(project_dir: Path, run_dir: Path, ui: TUIProtocol | None = None, spec: str | None = None) -> None:
    """Launch party mode: multi-agent brainstorming post-convergence.

    Loads agent personas and workflow definitions, then runs a Claude
    session that simulates a multi-agent discussion and produces a
    party report and README proposal.

    Args:
        project_dir: Root directory of the project.
        run_dir: Session directory for party mode artifacts.
        ui: TUI instance for status output (auto-created if None).
        spec: Path to the spec file relative to project_dir (default: README.md).
    """
    if ui is None:
        ui = get_tui()
    ui.party_mode()
    print("[probe] party mode: starting — loading agent personas and workflow")

    agents_dir = project_dir / "agents"
    if not agents_dir.is_dir():
        # Try evolve's own agents
        agents_dir = Path(__file__).parent / "agents"

    if not agents_dir.is_dir() or not list(agents_dir.glob("*.md")):
        ui.warn("No agent personas found — skipping party mode")
        return

    # Load agents
    agents = []
    for f in sorted(agents_dir.glob("*.md")):
        try:
            agents.append({"file": f.name, "content": f.read_text()})
        except (OSError, UnicodeDecodeError):
            continue
    print(f"[probe] party mode: loaded {len(agents)} agent persona(s)")

    # Load workflow
    workflow = ""
    wf_dir = Path(__file__).parent / "workflows" / "party-mode"
    if not wf_dir.is_dir():
        wf_dir = project_dir / "workflows" / "party-mode"
    if wf_dir.is_dir():
        parts = []
        wf_file = wf_dir / "workflow.md"
        if wf_file.is_file():
            parts.append(wf_file.read_text())
        steps_dir = wf_dir / "steps"
        if steps_dir.is_dir():
            for sf in sorted(steps_dir.glob("step-*.md")):
                try:
                    parts.append(sf.read_text())
                except (OSError, UnicodeDecodeError):
                    continue
        workflow = "\n\n---\n\n".join(parts)
    print(f"[probe] party mode: workflow loaded ({len(workflow)} chars)")

    # Load context
    spec_file = spec or "README.md"
    spec_path = project_dir / spec_file
    readme = spec_path.read_text() if spec_path.is_file() else "(none)"
    improvements = (project_dir / "runs" / "improvements.md").read_text() if (project_dir / "runs" / "improvements.md").is_file() else "(none)"
    memory = (project_dir / "runs" / "memory.md").read_text() if (project_dir / "runs" / "memory.md").is_file() else "(none)"
    converged = (run_dir / "CONVERGED").read_text().strip() if (run_dir / "CONVERGED").is_file() else ""
    print("[probe] party mode: context loaded (README, improvements, memory)")

    roster = "\n".join(f"- {a['file']}" for a in agents)
    personas = "\n\n".join(f"### {a['file']}\n\n{a['content']}" for a in agents)

    # Derive proposal filename from spec (e.g. SPEC.md → SPEC_proposal.md)
    spec_stem = Path(spec_file).stem
    spec_suffix = Path(spec_file).suffix or ".md"
    proposal_filename = f"{spec_stem}_proposal{spec_suffix}"

    # Party mode produces exactly two files: a discussion report and a spec
    # proposal. The README is user-authored and is never written by the
    # evolution loop — see SPEC.md § "README as a user-level summary".
    outputs_block = (
        f"1. `{run_dir}/party_report.md` — full discussion with each agent's reasoning\n"
        f"2. `{run_dir}/{proposal_filename}` — complete updated spec for the next evolution"
    )
    readme_context_block = f"## Current Spec ({spec_file})\n{readme}"
    closing_instruction = (
        f"Simulate the discussion, then write both files. "
        f"The {proposal_filename} must be complete (not a diff)."
    )

    prompt = f"""\
You are a Party Mode facilitator. The project has CONVERGED — all improvements done.

Your job: orchestrate a multi-agent brainstorming session, then produce:
{outputs_block}

## Workflow
{workflow}

## Agents
{roster}

## Agent Personas
{personas}

{readme_context_block}

## Improvements History
{improvements}

## Memory
{memory}

## Convergence Reason
{converged}

{closing_instruction}
"""

    # Scan for captured TUI frames to attach as image blocks
    frames_dir = run_dir / "frames"
    frame_images: list[Path] = []
    if frames_dir.is_dir():
        all_frames = sorted(frames_dir.glob("*.png"))
        # Pick the last 3-5 frames (convergence + preceding rounds)
        frame_images = all_frames[-5:] if len(all_frames) > 5 else all_frames
        if frame_images:
            print(f"[probe] party mode: attaching {len(frame_images)} TUI frame(s) as visual context")

    try:
        from agent import run_claude_agent, _is_benign_runtime_error, _should_retry_rate_limit
        import asyncio
        import time
        import warnings
        warnings.filterwarnings("ignore", message=".*cancel scope.*")
        warnings.filterwarnings("ignore", message=".*Event loop is closed.*")

        max_retries = 5
        print("[probe] party mode: launching Claude agent for brainstorming session")
        for attempt in range(1, max_retries + 1):
            try:
                if attempt > 1:
                    print(f"[probe] party mode: retry attempt {attempt}/{max_retries}")
                asyncio.run(run_claude_agent(
                    prompt, project_dir, round_num=0, run_dir=run_dir,
                    log_filename="party_conversation.md",
                    images=frame_images if frame_images else None,
                ))
                print("[probe] party mode: agent session completed successfully")
                break
            except Exception as e:
                if isinstance(e, RuntimeError) and _is_benign_runtime_error(e):
                    print("[probe] party mode: agent session completed (benign runtime cleanup)")
                    break

                wait = _should_retry_rate_limit(e, attempt, max_retries)
                if wait is not None:
                    print(f"[probe] party mode: rate limited, waiting {wait}s before retry")
                    ui.sdk_rate_limited(wait, attempt, max_retries)
                    time.sleep(wait)
                    continue

                ui.warn(f"Party mode failed ({e})")
                return
    except ImportError:
        ui.warn("claude-agent-sdk not installed — skipping party mode")
        return

    proposal = run_dir / proposal_filename
    report = run_dir / "party_report.md"
    print(f"[probe] party mode: finished — report={'yes' if report.is_file() else 'no'}, proposal={'yes' if proposal.is_file() else 'no'}")
    ui.party_results(
        str(proposal) if proposal.is_file() else None,
        str(report) if report.is_file() else None,
    )


def _forever_restart(
    project_dir: Path,
    run_dir: Path,
    improvements_path: Path,
    ui: TUIProtocol,
    spec: str | None = None,
) -> tuple[bool, bool]:
    """Post-convergence restart for forever mode.

    1. Merge the spec proposal into the spec file (if produced by party mode)
    2. Reset improvements.md for the next evolution cycle

    README.md is user-authored and is never written by the evolution loop —
    operators refresh it explicitly via ``evolve sync-readme``. See SPEC.md
    § "README as a user-level summary".

    Args:
        project_dir: Root directory of the project.
        run_dir: Session directory containing the spec proposal.
        improvements_path: Path to improvements.md to reset.
        ui: TUI instance for status messages.
        spec: Path to the spec file relative to project_dir (default: README.md).

    Returns:
        Tuple ``(spec_adopted, readme_adopted)`` where ``readme_adopted`` is
        always ``False``. The tuple shape is retained for backward
        compatibility with the caller's commit-message logic.
    """
    spec_file = spec or "README.md"
    spec_stem = Path(spec_file).stem
    spec_suffix = Path(spec_file).suffix or ".md"
    proposal_filename = f"{spec_stem}_proposal{spec_suffix}"
    proposal = run_dir / proposal_filename
    target = project_dir / spec_file

    spec_adopted = False
    if proposal.is_file():
        ui.info(f"  Forever mode: adopting {proposal_filename} as new {spec_file}")
        target.write_text(proposal.read_text())
        spec_adopted = True
    else:
        ui.warn(f"No {proposal_filename} produced — restarting with current {spec_file}")

    # Reset improvements.md for the next cycle
    ui.info("  Forever mode: resetting improvements.md for next cycle")
    improvements_path.write_text("# Improvements\n")

    return spec_adopted, False

    # Remove the CONVERGED marker so the next cycle starts fresh
    converged_path = run_dir / "CONVERGED"
    if converged_path.is_file():
        # Keep it in the old run dir — it's already been processed
        pass


def _setup_forever_branch(project_dir: Path) -> None:
    """Create and switch to a dedicated branch for forever mode.

    Creates a branch named ``evolve/forever-<timestamp>`` from the current HEAD
    so that forever-mode changes are isolated from the main branch.

    Args:
        project_dir: Root directory of the project (must be a git repo).
    """
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    branch_name = f"evolve/forever-{timestamp}"
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
