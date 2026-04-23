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


# Words that commonly appear as the token following "evolve " in prose but
# are NOT real subcommand names. Kept here so _extract_spec_claims can filter
# them out and only yield genuine CLI subcommands.
_EVOLVE_PROSE_BLOCKLIST = frozenset({
    "itself",
    "deliberately",
    "is",
    "works",
    "does",
    "stays",
    "features",
    "decides",
    "uses",
    "treats",
    "the",
    "to",
    "a",
    "also",
    "automatically",
    "and",
    "converges",
    "makes",
    "hooks",
    "only",
    "fires",
    "exits",
    "writes",
    "becomes",
    "has",
    "auto-detects",
    "prints",
    "logs",
    "error",
    "errors",
    "convergence",
    "ensures",
    "reads",
    "runs",
    "will",
    "would",
    "should",
    "must",
    "guarantees",
    "deletes",
    "picks",
    "gives",
    "may",
    "can",
    "knows",
    "keeps",
    "touches",
    "needs",
    "allows",
    "supports",
    "produces",
    "provides",
    "requires",
    "launches",
    "finishes",
    "feeds",
    "refuses",
    "expects",
    "watches",
    "emits",
    "scans",
    "extracts",
    "accepts",
    "accept",
    "reject",
    "merges",
    "snapshots",
    "tracks",
    "event",
    "events",
    "features",
    "project",
    "projects",
    "ships",
    "matches",
    "notices",
    "observes",
    "terminates",
    "attaches",
    "adopts",
    "discards",
})


def _extract_spec_claims(spec_text: str) -> list[tuple[str, str, str]]:
    """Extract user-visible claims from spec content (Mechanism A helper).

    Scans the spec for:

    - **CLI flags** — ``^###? The --<name>`` headers (e.g. ``### The --spec flag``)
    - **Subcommands** — ``evolve <word>`` patterns (where ``<word>`` is not a
      prose filler from ``_EVOLVE_PROSE_BLOCKLIST``)
    - **Environment variables** — tokens matching ``EVOLVE_[A-Z_]+``
    - **Requirements bullets** — ``-`` items under a ``## Requirements`` heading
    - **Shell examples** — lines starting with ``$ `` or inside ```` ```bash ````
      fences

    Each claim is returned with a type tag and the spec section it was found
    in, deduped by (claim, type).

    Args:
        spec_text: The full text of the spec file.

    Returns:
        List of ``(claim, claim_type, spec_section)`` tuples where
        ``claim_type`` is one of ``"flag"``, ``"subcommand"``, ``"env_var"``,
        ``"requirement"``, or ``"shell_example"``.
    """
    claims: list[tuple[str, str, str]] = []
    seen: set[tuple[str, str]] = set()

    def _add(claim: str, ctype: str, section: str) -> None:
        claim = claim.strip()
        if not claim:
            return
        key = (claim, ctype)
        if key in seen:
            return
        seen.add(key)
        claims.append((claim, ctype, section or "unspecified"))

    current_section = ""
    # Track generic fence state separately from bash-ness so that a ```python
    # fence's closing triple-backticks is not misread as opening a bash fence.
    in_any_fence = False
    in_bash_fence = False
    in_requirements = False

    for raw_line in spec_text.splitlines():
        line = raw_line.rstrip()

        # Code fence open/close (outside the fence, we parse headers; inside
        # a *bash* fence, we only parse shell examples).
        fence_m = re.match(r"^```([A-Za-z0-9_+-]*)", line.strip())
        if fence_m:
            lang = fence_m.group(1).lower()
            if not in_any_fence:
                in_any_fence = True
                in_bash_fence = lang in ("", "bash", "sh", "shell", "console", "zsh")
            else:
                in_any_fence = False
                in_bash_fence = False
            continue

        if not in_bash_fence:
            # Section tracking
            header_m = re.match(r"^(#{1,6})\s+(.+?)\s*$", line)
            if header_m:
                level = len(header_m.group(1))
                title = header_m.group(2).strip()
                if level <= 3:
                    current_section = title
                # Requirements flag is set on ## Requirements headers
                in_requirements = title.lower().rstrip(":") == "requirements"
                # CLI flag header: "### The --foo flag" or "### The --foo".
                # Extract before continuing so the section-tracking branch
                # doesn't swallow the header.
                flag_m = re.match(r"^###?\s+The\s+`?(--[a-z][a-z0-9-]*)`?", line)
                if flag_m:
                    _add(flag_m.group(1), "flag", current_section)
                continue

            # Environment variables in prose
            for envm in re.finditer(r"\bEVOLVE_[A-Z][A-Z0-9_]*\b", line):
                _add(envm.group(0), "env_var", current_section)

            # Subcommands: "evolve <word>" in prose
            for sm in re.finditer(r"\bevolve\s+([a-z][a-z-]{1,20})\b", line):
                word = sm.group(1)
                if word in _EVOLVE_PROSE_BLOCKLIST:
                    continue
                _add(f"evolve {word}", "subcommand", current_section)

            # Requirements bullets — only under ## Requirements
            if in_requirements:
                req_m = re.match(r"^[-*]\s+(.+?)\s*$", line)
                if req_m:
                    req_text = req_m.group(1).strip()
                    # Take the first segment before ':' or ' — ' as the claim
                    key = re.split(r"\s*[:—]\s*", req_text, maxsplit=1)[0]
                    # Strip backticks
                    key = key.strip("` ").strip()
                    if key:
                        _add(key, "requirement", current_section)
        else:
            # Inside a bash fence: collect shell examples.
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            # Normalise a leading "$ " prompt
            if stripped.startswith("$ "):
                stripped = stripped[2:].strip()
            elif stripped.startswith("$"):
                stripped = stripped[1:].strip()
            # We only care about invocations of the `evolve` binary —
            # they are the user-visible surface area the README should cover.
            if stripped.startswith("evolve "):
                # Dedupe by the first 60 chars — long examples often vary
                # only in comments or arguments.
                claim = stripped[:80]
                _add(claim, "shell_example", current_section)

    return claims


def _suggest_readme_section(claim_type: str) -> str:
    """Suggest a plausible README section for a claim of the given type.

    Used by ``_audit_readme_sync`` to generate human-friendly improvement
    items like "mention --foo in Usage".

    Args:
        claim_type: One of ``"flag"``, ``"subcommand"``, ``"env_var"``,
            ``"requirement"``, ``"shell_example"``.

    Returns:
        A short section name (not necessarily a literal heading in README.md).
    """
    return {
        "flag": "Usage",
        "subcommand": "Usage",
        "env_var": "Configuration",
        "requirement": "Requirements",
        "shell_example": "Examples",
    }.get(claim_type, "README")


def _audit_readme_sync(
    project_dir: Path,
    improvements_path: Path,
    spec: str | None = None,
) -> int:
    """Pre-convergence README audit — Mechanism A from SPEC.md § "README sync discipline".

    Before convergence, extract user-visible claims from the spec file and,
    for each claim that is **not** mentioned in ``README.md``, append an
    improvement item of the form::

        - [ ] [functional] README sync: mention `<claim>` in <README section>
          (documented in <spec> § <section> but absent from README.md)

    The audit is intentionally a grep-level heuristic — being cheap is what
    lets it run every round without budget concerns. It **never blocks
    convergence**; the sync items it appends simply become the first targets
    of the next evolution cycle.

    The audit is a no-op when:

    - ``spec`` is unset or equals ``"README.md"`` (nothing to sync with)
    - Either the spec file or ``README.md`` is missing
    - There are no gaps (every claim is already mentioned)
    - Every gap's sync item already exists in ``improvements.md`` (idempotent)

    Args:
        project_dir: Root directory of the project.
        improvements_path: Path to ``improvements.md``.
        spec: Spec file path relative to ``project_dir`` (default
            ``README.md``).

    Returns:
        Number of sync items appended to ``improvements.md``.
    """
    spec_file = spec or "README.md"

    # Mechanism A only runs when the spec is a *separate* file.
    if spec_file == "README.md":
        return 0

    spec_path = project_dir / spec_file
    readme_path = project_dir / "README.md"

    if not spec_path.is_file() or not readme_path.is_file():
        return 0

    try:
        spec_text = spec_path.read_text(errors="replace")
        readme_text = readme_path.read_text(errors="replace")
    except OSError:
        return 0

    claims = _extract_spec_claims(spec_text)
    if not claims:
        return 0

    readme_lower = readme_text.lower()
    existing_items = improvements_path.read_text() if improvements_path.is_file() else ""

    new_items: list[str] = []
    for claim, ctype, section in claims:
        # Claim is considered mentioned if its lowercase form appears
        # anywhere in README.md — even a brief reference or a link suffices.
        if claim.lower() in readme_lower:
            continue
        readme_section = _suggest_readme_section(ctype)
        item = (
            f"- [ ] [functional] README sync: mention `{claim}` "
            f"in {readme_section} "
            f"(documented in {spec_file} § {section} but absent from README.md)"
        )
        if item in existing_items:
            continue  # idempotent
        new_items.append(item)

    if not new_items:
        return 0

    # Append (preserving a trailing newline if the file already had one)
    separator = "" if existing_items.endswith("\n") or not existing_items else "\n"
    with improvements_path.open("a") as f:
        f.write(separator)
        for item in new_items:
            f.write(item + "\n")

    return len(new_items)


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


def _compute_readme_sync(
    project_dir: Path,
    run_dir: Path,
    round_num: int,
    spec: str | None,
) -> dict | None:
    """Compute README drift status for Mechanism C.

    Implements SPEC.md § "README sync discipline" Mechanism C. Compares
    ``mtime(spec_file)`` vs ``mtime(README.md)`` and tracks how many
    rounds the README has been stale via a persistent
    ``readme_stale_since_round`` counter read from the prior
    ``state.json``. Returns a ``readme_sync`` dict suitable for embedding
    in ``state.json``, or ``None`` when Mechanism C is inactive
    (``--spec`` unset or equal to ``README.md``).

    Args:
        project_dir: Root directory of the project.
        run_dir: Session directory — used to read prior state.json.
        round_num: Current round number.
        spec: Spec filename (relative to project_dir). ``None`` or
              ``"README.md"`` means Mechanism C is a no-op.

    Returns:
        A dict with keys ``stale``, ``spec_mtime``, ``readme_mtime``,
        ``rounds_since_stale`` when drift tracking is active, or
        ``None`` when inactive (README is the spec or spec unset).
    """
    if not spec or spec == "README.md":
        return None

    spec_path = project_dir / spec
    readme_path = project_dir / "README.md"
    if not spec_path.is_file() or not readme_path.is_file():
        return None

    spec_mtime = spec_path.stat().st_mtime
    readme_mtime = readme_path.stat().st_mtime

    spec_iso = datetime.fromtimestamp(spec_mtime, tz=timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    readme_iso = datetime.fromtimestamp(readme_mtime, tz=timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )

    stale = spec_mtime > readme_mtime

    # Load prior readme_stale_since_round counter from previous state.json
    # so the counter survives across rounds. Cleared whenever stale is False
    # (README was touched since the last stale round).
    prior_since: int | None = None
    existing = run_dir / "state.json"
    if existing.is_file():
        try:
            prev = json.loads(existing.read_text())
            prev_sync = prev.get("readme_sync") or {}
            prior_since = prev_sync.get("readme_stale_since_round")
        except (json.JSONDecodeError, OSError):
            prior_since = None

    if stale:
        since = prior_since if prior_since is not None else round_num
        rounds_since_stale = max(0, round_num - since)
    else:
        since = None
        rounds_since_stale = 0

    result: dict = {
        "stale": stale,
        "spec_mtime": spec_iso,
        "readme_mtime": readme_iso,
        "rounds_since_stale": rounds_since_stale,
    }
    # Persist the round the drift started so the next round can compute
    # rounds_since_stale correctly. Not in the SPEC's documented schema
    # but read back by this function on the next round — internal-only.
    if since is not None:
        result["readme_stale_since_round"] = since
    return result


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
    readme_sync: dict | None = None,
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

    state: dict = {
        "version": 1,
        "session": run_dir.name,
        "project": project_dir.name,
        "round": round_num,
        "max_rounds": max_rounds,
        "phase": phase,
        "status": status,
        "improvements": {"done": done, "remaining": remaining, "blocked": blocked},
        "last_check": last_check if last_check else {},
        "started_at": started_at,
        "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    if readme_sync is not None:
        state["readme_sync"] = readme_sync

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
    )


# Maximum number of debug retries when a round fails, stalls, or makes no progress.
MAX_DEBUG_RETRIES = 2
# Seconds of silence before the watchdog considers a subprocess stalled.
WATCHDOG_TIMEOUT = 120


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
    error_log.write_text(
        f"Round {round_num} — {reason} (attempt {attempt})\n"
        f"Command: {' '.join(str(c) for c in cmd)}\n\n"
        f"{escape_hatch_banner}"
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

            # --- Debug retry loop: run the round, diagnose failures, retry ---
            print(f"[probe] launching subprocess for round {round_num}")
            round_succeeded = False
            for attempt in range(1, MAX_DEBUG_RETRIES + 2):  # 1..MAX_DEBUG_RETRIES+1
                # Snapshot conversation log size before subprocess so we can detect new output
                convo = run_dir / f"conversation_loop_{round_num}.md"
                convo_size_before = convo.stat().st_size if convo.is_file() else 0

                # Snapshot improvements.md bytes before subprocess for zero-progress detection
                imp_snapshot_before = improvements_path.read_bytes() if improvements_path.is_file() else b""

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

                    # Either condition alone triggers zero-progress
                    if no_commit_msg or imp_unchanged:
                        no_progress_reasons: list[str] = []
                        if no_commit_msg:
                            no_progress_reasons.append(
                                "no COMMIT_MSG written (fallback commit message)"
                            )
                        if imp_unchanged:
                            no_progress_reasons.append(
                                "improvements.md byte-identical to pre-round state"
                            )
                        reason_str = " AND ".join(no_progress_reasons)
                        _save_subprocess_diagnostic(
                            run_dir, round_num, cmd, output,
                            reason=f"NO PROGRESS: {reason_str}",
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

            # Mechanism C — README drift warning (observability only, never blocks).
            # Computed before writing state.json so the drift status lands in state.
            readme_sync = _compute_readme_sync(project_dir, run_dir, round_num, spec)
            if readme_sync and readme_sync.get("stale") and readme_sync.get("rounds_since_stale", 0) > 3:
                spec_label = spec or "SPEC.md"
                ui.warn(
                    f"README drift: {spec_label} touched "
                    f"{readme_sync['rounds_since_stale']} rounds ago, "
                    f"README.md unchanged"
                )

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
                readme_sync=readme_sync,
            )

            # Clean up diagnostic file on success (no longer relevant)
            error_log = run_dir / f"subprocess_error_round_{round_num}.txt"
            if error_log.is_file():
                error_log.unlink()

            # Check convergence — requires spec freshness gate
            # (mtime(improvements.md) >= mtime(spec)) AND CONVERGED file
            converged_path = run_dir / "CONVERGED"
            if converged_path.is_file():
                # Pre-convergence README audit (Mechanism A). Runs before the
                # freshness gate because it may append items to improvements.md,
                # which refreshes its mtime and naturally satisfies the gate.
                # The audit is a no-op unless --spec points at a non-README file.
                sync_added = _audit_readme_sync(project_dir, improvements_path, spec=spec)
                if sync_added > 0:
                    print(
                        f"[probe] README audit: appended {sync_added} sync "
                        f"item(s) to improvements.md (does not block convergence)"
                    )
                    # Commit the updated improvements.md so the next cycle
                    # picks it up cleanly and the working tree stays clean.
                    _git_commit(
                        project_dir,
                        f"docs(readme-sync): add {sync_added} README sync item(s) from pre-convergence audit",
                        ui,
                    )

                # Verify spec freshness gate — don't mark stale again,
                # just check the mtime relationship
                spec_file = spec or "README.md"
                spec_path = project_dir / spec_file
                if spec_path.is_file() and improvements_path.is_file():
                    if spec_path.stat().st_mtime > improvements_path.stat().st_mtime:
                        print("[probe] convergence rejected: spec is newer than improvements.md — removing CONVERGED marker")
                        converged_path.unlink()
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
                    readme_sync=readme_sync,
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
                    # Auto-merge spec proposal (and README_proposal when spec
                    # differs from README.md) into their targets, then restart.
                    adoption_result = _forever_restart(
                        project_dir, run_dir, improvements_path, ui, spec=spec
                    )
                    # Backwards-compat: historical _forever_restart returned
                    # None; new signature returns (spec_adopted, readme_adopted).
                    if isinstance(adoption_result, tuple):
                        spec_adopted, readme_adopted = adoption_result
                    else:
                        spec_adopted, readme_adopted = False, False

                    # Create a new session directory for the next cycle
                    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                    run_dir = project_dir / "runs" / timestamp
                    run_dir.mkdir(parents=True, exist_ok=True)
                    # Update TUI with new run_dir for frame capture
                    if capture_frames:
                        ui = get_tui(run_dir=run_dir, capture_frames=capture_frames)
                    ui.run_dir_info(str(run_dir))

                    # Git commit the proposal adoption + reset. When --spec
                    # differs from README.md, use the documented atomic-adoption
                    # template so both files move together in a single commit
                    # (Mechanism B). Otherwise fall back to the legacy message.
                    spec_file_for_msg = spec or "README.md"
                    if spec_file_for_msg != "README.md" and (spec_adopted or readme_adopted):
                        spec_stem_msg = Path(spec_file_for_msg).stem
                        spec_suffix_msg = Path(spec_file_for_msg).suffix or ".md"
                        proposal_name_msg = f"{spec_stem_msg}_proposal{spec_suffix_msg}"
                        commit_lines = [
                            f"feat(spec): adopt {proposal_name_msg}",
                            "",
                        ]
                        if spec_adopted:
                            commit_lines.append(
                                f"- {spec_file_for_msg} updated from {proposal_name_msg}"
                            )
                        if readme_adopted:
                            commit_lines.append(
                                "- README.md updated from README_proposal.md"
                            )
                        commit_lines.append("- improvements.md reset")
                        commit_msg = "\n".join(commit_lines)
                    else:
                        commit_msg = (
                            "chore(evolve): forever mode — adopt README_proposal, "
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
                readme_sync=_compute_readme_sync(project_dir, run_dir, max_rounds, spec),
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

    # Mechanism B — when --spec points at a file other than README.md, also
    # produce a README_proposal.md so spec + README move together.
    needs_readme_proposal = spec_file != "README.md"
    readme_md_path = project_dir / "README.md"
    current_readme_text = readme_md_path.read_text() if readme_md_path.is_file() else "(none)"

    if needs_readme_proposal:
        outputs_block = (
            f"1. `{run_dir}/party_report.md` — full discussion with each agent's reasoning\n"
            f"2. `{run_dir}/{proposal_filename}` — complete updated spec for the next evolution\n"
            f"3. `{run_dir}/README_proposal.md` — user-facing README rewritten to reflect the "
            f"claims of the new spec proposal. Preserve the README's tutorial voice "
            f"(brevity, examples, links to {spec_file} for internals) rather than copying "
            f"the spec verbatim."
        )
        readme_context_block = (
            f"## Current Spec ({spec_file})\n{readme}\n\n"
            f"## Current README.md\n{current_readme_text}"
        )
        closing_instruction = (
            f"Simulate the discussion, then write all three files. Both "
            f"{proposal_filename} and README_proposal.md must be complete documents "
            f"(not diffs). The README_proposal.md must reflect the claims of the new "
            f"{proposal_filename} while preserving the README's own voice — tutorial, "
            f"examples, brevity, and links to {spec_file} for details rather than "
            f"duplicating spec prose."
        )
    else:
        outputs_block = (
            f"1. `{run_dir}/party_report.md` — full discussion with each agent's reasoning\n"
            f"2. `{run_dir}/{proposal_filename}` — complete updated spec for the next evolution"
        )
        readme_context_block = f"## Current README\n{readme}"
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
    2. When ``spec`` differs from ``README.md``, also adopt
       ``README_proposal.md`` (Mechanism B — the two move atomically)
    3. Reset improvements.md for the next evolution cycle

    Args:
        project_dir: Root directory of the project.
        run_dir: Session directory containing the spec proposal.
        improvements_path: Path to improvements.md to reset.
        ui: TUI instance for status messages.
        spec: Path to the spec file relative to project_dir (default: README.md).

    Returns:
        Tuple ``(spec_adopted, readme_adopted)`` indicating whether each
        proposal was applied, so the caller can tailor the commit message.
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

    # Mechanism B: when the spec is not README.md, adopt README_proposal.md
    # alongside the spec proposal so user-facing docs stay in sync. We only
    # emit a drift warning when the spec proposal *was* adopted — if party
    # mode produced nothing at all, the earlier SPEC-level warning already
    # tells the full story and a second warning would be noise.
    readme_adopted = False
    if spec_file != "README.md":
        readme_proposal = run_dir / "README_proposal.md"
        readme_target = project_dir / "README.md"
        if readme_proposal.is_file():
            ui.info("  Forever mode: adopting README_proposal.md as new README.md")
            readme_target.write_text(readme_proposal.read_text())
            readme_adopted = True
        elif spec_adopted:
            ui.warn(
                "No README_proposal.md produced — README.md will drift from "
                f"updated {spec_file} until the next cycle"
            )

    # Reset improvements.md for the next cycle
    ui.info("  Forever mode: resetting improvements.md for next cycle")
    improvements_path.write_text("# Improvements\n")

    return spec_adopted, readme_adopted

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
