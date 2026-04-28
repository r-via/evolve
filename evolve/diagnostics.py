"""Diagnostics helpers — detection, reporting, and subprocess diagnostics.

Extracted from orchestrator.py to keep it under the 500-line cap.
All functions are near-pure (read filesystem, return data) with minimal
coupling to orchestrator state.
"""

from __future__ import annotations

import hashlib
import re
import shutil
import subprocess
from pathlib import Path

from evolve.reporting import _generate_evolution_report  # noqa: F401 — re-export
from evolve.state import (
    _detect_backlog_violation,
    _detect_premature_converged,
    _runs_base,
)
from evolve.tui import TUIProtocol

# Re-export state diagnostics so consumers can import from one place.
__all__ = [
    "_auto_detect_check",
    "_check_review_verdict",
    "_detect_backlog_violation",
    "_detect_file_too_large",
    "_detect_premature_converged",
    "_emit_stale_readme_advisory",
    "_failure_signature",
    "_generate_evolution_report",
    "_is_circuit_breaker_tripped",
    "_save_subprocess_diagnostic",
    "_detect_us_format_violation",
    "MAX_IDENTICAL_FAILURES",
]

# Re-export for consolidated access (originals in evolve.state)
_detect_backlog_violation = _detect_backlog_violation
_detect_premature_converged = _detect_premature_converged


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

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

# Circuit breaker: when the same failure signature repeats across this many
# consecutive failed rounds, the loop exits with code 4 so an outer supervisor
# can restart from a clean slate.  Single source of truth for the threshold —
# see SPEC.md § "Circuit breakers".
MAX_IDENTICAL_FAILURES = 3

_FILE_TOO_LARGE_LIMIT = 500


# ---------------------------------------------------------------------------
# Auto-detection
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Stale-README advisory
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Failure signature and circuit breaker
# ---------------------------------------------------------------------------


def _failure_signature(kind: str, returncode: int, output: str) -> str:
    """Fingerprint a failed round attempt for circuit-breaker detection.

    Two attempts with the same fingerprint are treated as the same failure
    — evidence that retrying is futile.  Only the trailing 500 bytes of
    ``output`` are hashed so mostly-deterministic failures with varying
    prefixes (timestamps, progress counters) still match.

    Args:
        kind: Failure category — ``"stalled"``, ``"crashed"``, or
            ``"no-progress"``.
        returncode: Subprocess exit code (may be negative for signals).
        output: Captured subprocess output (stdout+stderr merged).

    Returns:
        A 16-char hex digest suitable for equality comparison and logging.
    """
    tail = output[-500:].strip() if output else ""
    payload = f"{kind}|{returncode}|{tail}".encode("utf-8", errors="replace")
    return hashlib.sha256(payload).hexdigest()[:16]


def _is_circuit_breaker_tripped(signatures: list[str]) -> bool:
    """Return True when the last ``MAX_IDENTICAL_FAILURES`` signatures match.

    Implements the threshold test for SPEC § "Circuit breakers".  A caller
    appends each failed-round signature to ``signatures`` (and clears the
    list on any successful round), then queries this helper to decide
    whether the loop has entered a deterministic failure cycle.
    """
    if len(signatures) < MAX_IDENTICAL_FAILURES:
        return False
    return len(set(signatures[-MAX_IDENTICAL_FAILURES:])) == 1


# ---------------------------------------------------------------------------
# Review verdict
# ---------------------------------------------------------------------------


def _check_review_verdict(run_dir: Path, round_num: int) -> tuple[str | None, str]:
    """Read the adversarial review file and return the verdict.

    Returns:
        (verdict, findings) where verdict is one of
        "APPROVED", "CHANGES REQUESTED", "BLOCKED", or None (file absent),
        and findings is the raw text of HIGH and MEDIUM-severity findings
        (empty string when verdict is APPROVED or None).  HIGH and MEDIUM
        are both surfaced because the orchestrator auto-fixes them on the
        next attempt — the operator never arbitrates findings manually
        (SPEC § "Adversarial round review (Phase 3.6)").
    """
    review_path = run_dir / f"review_round_{round_num}.md"
    if not review_path.is_file():
        return None, ""
    try:
        content = review_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None, ""

    # Parse verdict line — expected format: "**Verdict:** APPROVED" or similar
    verdict = None
    for line in content.splitlines():
        low = line.lower().strip()
        if "verdict" in low:
            if "blocked" in low:
                verdict = "BLOCKED"
            elif "changes requested" in low or "changes_requested" in low:
                verdict = "CHANGES REQUESTED"
            elif "approved" in low:
                verdict = "APPROVED"
            if verdict:
                break

    # Extract HIGH and MEDIUM findings for diagnostic context — both are
    # auto-fixed on the next attempt.
    findings: list[str] = []
    if verdict and verdict != "APPROVED":
        in_finding = False
        for line in content.splitlines():
            is_severity_header = (
                ("HIGH" in line or "MEDIUM" in line)
                and ("finding" in line.lower() or ":" in line or "-" in line[:5])
            )
            if is_severity_header:
                in_finding = True
                findings.append(line.strip())
            elif in_finding and line.strip() and not line.startswith("#"):
                findings.append(line.strip())
            elif in_finding and (line.startswith("#") or not line.strip()):
                in_finding = False

    return verdict, "\n".join(findings)


# ---------------------------------------------------------------------------
# File size enforcement
# ---------------------------------------------------------------------------


def _detect_file_too_large(
    project_dir: Path,
) -> list[tuple[str, int]]:
    """Return ``(path, line_count)`` for every ``evolve/**/*.py`` or
    ``tests/**/*.py`` file that exceeds :data:`_FILE_TOO_LARGE_LIMIT` lines.

    The scan is cheap (pure line-count via ``Path.read_text`` splitlines),
    does not invoke ``wc -l``, and silently skips unreadable files.
    """
    oversized: list[tuple[str, int]] = []
    for pattern in ("evolve/**/*.py", "tests/**/*.py"):
        for p in sorted(project_dir.glob(pattern)):
            try:
                count = len(p.read_text(errors="replace").splitlines())
            except OSError:
                continue
            if count > _FILE_TOO_LARGE_LIMIT:
                oversized.append((str(p.relative_to(project_dir)), count))
    return oversized


# ---------------------------------------------------------------------------
# US format validation
# ---------------------------------------------------------------------------

# Regex for valid US header: - [ ] [type] (optional more tags) US-NNN: summary
_US_HEADER_RE = re.compile(
    r"^- \[ \] \[\w+\](?:\s+\[\w+\])* US-\d{3,}: "
)

# Required section headers in the US body
_US_REQUIRED_SECTIONS = ("**As**", "**Acceptance criteria", "**Definition of done")


def _detect_us_format_violation(
    improvements_path: Path, pre_round_lines: list[str]
) -> list[str]:
    """Detect newly-added ``[ ]`` items lacking the US template structure.

    Diffs new ``[ ]`` lines against ``pre_round_lines``, checks each
    against the US header regex and verifies the item body contains the
    three required section headers (``**As**``, ``**Acceptance criteria``,
    ``**Definition of done``).

    Args:
        improvements_path: Path to ``improvements.md`` post-round.
        pre_round_lines: Lines from ``improvements.md`` before the round.

    Returns:
        List of violation descriptions (empty = pass).
    """
    if not improvements_path.is_file():
        return []
    try:
        post_text = improvements_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    post_lines = post_text.splitlines()
    pre_set = set(pre_round_lines)
    # Find newly-added unchecked item header lines
    new_headers: list[tuple[int, str]] = []
    for i, line in enumerate(post_lines):
        if line.strip().startswith("- [ ]") and line not in pre_set:
            new_headers.append((i, line))
    if not new_headers:
        return []
    violations: list[str] = []
    for idx, header in new_headers:
        # Check header format
        header_stripped = header.strip()
        if not _US_HEADER_RE.match(header_stripped):
            violations.append(
                f"Item at line {idx + 1} has malformed header "
                f"(expected `- [ ] [type] US-NNN: ...`): "
                f"{header_stripped[:120]}"
            )
            continue
        # Collect body lines until next item or EOF
        body_lines: list[str] = []
        for j in range(idx + 1, len(post_lines)):
            if post_lines[j].strip().startswith("- ["):
                break
            body_lines.append(post_lines[j])
        body_text = "\n".join(body_lines)
        missing = [
            s for s in _US_REQUIRED_SECTIONS if s not in body_text
        ]
        if missing:
            violations.append(
                f"Item at line {idx + 1} missing required sections: "
                f"{', '.join(missing)}: {header_stripped[:120]}"
            )
    return violations


# ---------------------------------------------------------------------------
# Subprocess diagnostic
# ---------------------------------------------------------------------------


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
