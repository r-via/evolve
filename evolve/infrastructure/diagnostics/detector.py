"""Diagnostics detector — detection, reporting, and subprocess diagnostics.

Migrated from ``evolve/diagnostics.py`` as part of the DDD restructuring
(SPEC.md § "Source code layout — DDD", migration step 12).
All callers continue to import via ``evolve.diagnostics`` (backward-compat
shim) or ``evolve.infrastructure.diagnostics`` (re-export ``__init__``).
"""

from __future__ import annotations

import hashlib
import re
import shutil
import subprocess
from pathlib import Path


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Stale-README advisory constants — keep the runtime advisory aligned with
# SPEC.md § "Stale-README pre-flight check".
_README_STALE_ADVISORY_FMT = (
    "\u2139\ufe0f  README has not been updated in {days} days \u2014 "
    "consider `evolve sync-readme`"
)
_DEFAULT_README_STALE_THRESHOLD_DAYS = 30

# Circuit breaker: when the same failure signature repeats across this many
# consecutive failed rounds, the loop exits with code 4.
MAX_IDENTICAL_FAILURES = 3

_FILE_TOO_LARGE_LIMIT = 500

# DDD layer classification — mirrors tests/test_layering.py logic.
_DDD_LAYERS = ("domain", "application", "infrastructure", "interfaces")
# MIGRATION CARVE-OUT — mirrors tests/test_layering.py._ALLOWED.
# Temporary relaxation: application → infrastructure/interfaces,
# infrastructure → interfaces.  Tighten after DI wiring.
_DDD_ALLOWED = {
    "domain": set(),
    "application": {"domain", "application", "infrastructure", "interfaces"},
    "infrastructure": {"domain", "infrastructure", "interfaces"},
    "interfaces": {"application", "domain", "infrastructure", "interfaces"},
}


# ---------------------------------------------------------------------------
# Auto-detection
# ---------------------------------------------------------------------------


def _auto_detect_check(project_dir: Path) -> str | None:
    """Auto-detect the test framework. Returns command string or None."""
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
    ui: object,
) -> None:
    """Emit startup stale-README advisory when spec is newer than README.

    Args:
        project_dir: Root directory of the project.
        spec: Spec filename relative to project_dir, or None.
        ui: TUI instance (duck-typed — must expose ``.info(msg)``).
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
    """Fingerprint a failed round for circuit-breaker detection (16-char hex)."""
    tail = output[-500:].strip() if output else ""
    payload = f"{kind}|{returncode}|{tail}".encode("utf-8", errors="replace")
    return hashlib.sha256(payload).hexdigest()[:16]


def _is_circuit_breaker_tripped(signatures: list[str]) -> bool:
    """True when last MAX_IDENTICAL_FAILURES signatures match."""
    if len(signatures) < MAX_IDENTICAL_FAILURES:
        return False
    return len(set(signatures[-MAX_IDENTICAL_FAILURES:])) == 1


# ---------------------------------------------------------------------------
# Review verdict
# ---------------------------------------------------------------------------


def _check_review_verdict(run_dir: Path, round_num: int) -> tuple[str | None, str]:
    """Read review_round_N.md and return (verdict, findings)."""
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
    """Return ``(path, line_count)`` for files exceeding the 500-line cap."""
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
# DDD layering violation detection
# ---------------------------------------------------------------------------


def _detect_layering_violation(
    project_dir: Path,
) -> list[tuple[str, str, str, str]]:
    """Scan DDD-layer files for inward-violating imports.

    Returns list of ``(file, imported_module, source_layer, target_layer)``
    tuples.  Legacy flat modules under ``evolve/`` are excluded per SPEC
    migration carve-out.
    """
    import ast as _ast

    evolve_dir = project_dir / "evolve"
    if not evolve_dir.is_dir():
        return []
    violations: list[tuple[str, str, str, str]] = []
    for py_file in sorted(evolve_dir.rglob("*.py")):
        rel = py_file.relative_to(evolve_dir)
        parts = rel.parts
        if not parts or parts[0] not in _DDD_LAYERS:
            continue  # legacy — whitelisted
        src_layer = parts[0]
        allowed = _DDD_ALLOWED[src_layer]
        try:
            tree = _ast.parse(py_file.read_text(), filename=str(py_file))
        except (SyntaxError, OSError):
            continue
        for node in _ast.walk(tree):
            mod = None
            if isinstance(node, _ast.ImportFrom) and node.module:
                mod = node.module
            elif isinstance(node, _ast.Import):
                for alias in node.names:
                    if alias.name.startswith("evolve."):
                        mod = alias.name
                        break
            if not mod or not mod.startswith("evolve."):
                continue
            mod_parts = mod.split(".")
            tgt = mod_parts[1] if len(mod_parts) >= 2 and mod_parts[1] in _DDD_LAYERS else "legacy"
            if tgt not in allowed:
                violations.append((
                    str(rel), mod, src_layer, tgt,
                ))
    return violations


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
    """Detect newly-added ``[ ]`` items lacking US template structure.

    Returns list of violation descriptions (empty = pass).
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
# Legacy flat-layout violation detection (DDD migration-completion gate)
# ---------------------------------------------------------------------------

# Files exempt from the shim check (package markers / entry-point dispatcher).
_LEGACY_WHITELIST = {"__init__.py", "__main__.py"}


def _detect_legacy_layout_violation(
    project_dir: Path,
) -> list[tuple[str, str, int]]:
    """Detect unmigrated production code at ``evolve/*.py`` (top level only).

    Uses the same AST classifier as ``tests/test_legacy_flat_layout_empty.py``
    (SPEC § "Migration-completion gate (HARD)").

    Returns list of ``(filename, offending_node_kind, line_number)`` tuples.
    Empty list = all files are whitelisted or pure shims.
    """
    import ast as _ast

    evolve_dir = project_dir / "evolve"
    if not evolve_dir.is_dir():
        return []

    violations: list[tuple[str, str, int]] = []
    for py_file in sorted(evolve_dir.glob("*.py")):
        if py_file.name in _LEGACY_WHITELIST:
            continue
        try:
            source = py_file.read_text(encoding="utf-8")
            tree = _ast.parse(source, filename=str(py_file))
        except (OSError, SyntaxError):
            continue

        for node in tree.body:
            if isinstance(node, (_ast.Import, _ast.ImportFrom)):
                continue
            # Module docstring (string constant expression)
            if isinstance(node, _ast.Expr) and isinstance(
                getattr(node, "value", None), _ast.Constant
            ) and isinstance(node.value.value, str):
                continue
            # warnings.warn(...) as bare expression
            if isinstance(node, _ast.Expr) and isinstance(
                getattr(node, "value", None), _ast.Call
            ):
                func = node.value.func
                if isinstance(func, _ast.Attribute) and func.attr == "warn":
                    continue
            # __all__ assignment
            if isinstance(node, _ast.Assign):
                is_all = any(
                    isinstance(t, _ast.Name) and t.id == "__all__"
                    for t in node.targets
                )
                if is_all:
                    continue
                # warnings.warn() as RHS of assignment
                if isinstance(node.value, _ast.Call):
                    func = node.value.func
                    if isinstance(func, _ast.Attribute) and func.attr == "warn":
                        continue
            # if __name__ == "__main__": block
            if isinstance(node, _ast.If):
                test = node.test
                if (
                    isinstance(test, _ast.Compare)
                    and len(test.ops) == 1
                    and isinstance(test.ops[0], _ast.Eq)
                    and isinstance(test.left, _ast.Name)
                    and test.left.id == "__name__"
                    and len(test.comparators) == 1
                    and isinstance(test.comparators[0], _ast.Constant)
                    and test.comparators[0].value == "__main__"
                ):
                    continue

            # Everything else is a violation
            kind = type(node).__name__
            line = getattr(node, "lineno", 0)
            violations.append((py_file.name, kind, line))
    return violations


# ---------------------------------------------------------------------------
# TDD violation detection
# ---------------------------------------------------------------------------


def _detect_tdd_violation(
    project_dir: Path, run_dir: Path, round_num: int, is_structural: bool
) -> str | None:
    """Detect production code under ``evolve/`` without test changes.

    Returns violation description or None. Structural commits exempt.
    """
    if is_structural:
        return None
    try:
        result = subprocess.run(
            ["git", "diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD"],
            cwd=str(project_dir), capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0:
            return None
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return None

    touched = [ln.strip() for ln in result.stdout.splitlines() if ln.strip()]
    if not touched:
        return None

    prod_files = [
        f for f in touched
        if f.startswith("evolve/") and f.endswith(".py")
    ]
    test_files = [
        f for f in touched
        if f.startswith("tests/") and f.endswith(".py")
    ]

    if prod_files and not test_files:
        prod_list = ", ".join(prod_files[:10])
        return (
            f"Production files modified without test changes: {prod_list}"
        )
    return None


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
    """Write subprocess_error_round_N.txt for a failed/stalled round."""
    error_log = run_dir / f"subprocess_error_round_{round_num}.txt"
    next_attempt = attempt + 1
    escape_hatch_banner = ""
    if next_attempt >= 3:
        escape_hatch_banner = (
            "### Phase 1 escape hatch notice\n"
            f"The next run of round {round_num} will be attempt "
            f"{next_attempt} of 3 \u2014 the FINAL retry. If Phase 1 check "
            "failures are still unresolved AND the failing output references "
            "NO files named in the current improvement target, the Phase 1 "
            "escape hatch is PERMITTED (see prompts/system.md \u00a7 'Phase 1 "
            "escape hatch'). Log blocked errors to memory.md, append a "
            "'Phase 1 bypass' item to improvements.md, proceed with the "
            "target, and include a 'Phase 1 bypass: <summary>' line in "
            "COMMIT_MSG.\n\n"
        )
    prev_attempt_log = run_dir / f"conversation_loop_{round_num}_attempt_{attempt}.md"
    prev_attempt_section = (
        "### Previous attempt log\n"
        f"Full conversation log of attempt {attempt}: {prev_attempt_log}\n"
        "Read this file FIRST in the next attempt \u2014 do not redo the "
        "investigation, continue from where it stopped.\n\n"
    )
    error_log.write_text(
        f"Round {round_num} \u2014 {reason} (attempt {attempt})\n"
        f"Command: {' '.join(str(c) for c in cmd)}\n\n"
        f"{escape_hatch_banner}"
        f"{prev_attempt_section}"
        f"Output (last 3000 chars):\n{(output or '')[-3000:]}\n"
    )
