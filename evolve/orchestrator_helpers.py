"""Scaffolding helpers and between-round agent passes for the orchestrator.

Extracted from ``evolve/orchestrator.py`` per SPEC § "Hard rule: source
files MUST NOT exceed 500 lines".  This is a leaf module: it imports
ONLY from stdlib, ``evolve.state``, ``evolve.diagnostics``, ``evolve.git``,
and ``evolve.tui`` at module top.  Any agent or CLI lookup happens via
function-local imports inside the function bodies — preserving the
patch-surface contract for ``patch("evolve.agent.X")`` /
``patch("evolve.cli.X")`` test targets and avoiding the lazy-import
trap documented in ``memory.md`` round-6-of-20260427_114957.

Symbols re-exported from ``evolve.orchestrator``:

- Probe helpers: ``_PROBE_PREFIX`` / ``_PROBE_WARN_PREFIX`` /
  ``_PROBE_OK_PREFIX`` constants and ``_probe`` / ``_probe_warn`` /
  ``_probe_ok`` functions.
- Scaffolding: ``_scaffold_shared_runtime_files``,
  ``_is_self_evolving``, ``_enforce_convergence_backstop``,
  ``_parse_report_summary``.
- Between-round agent passes: ``_run_curation_pass``,
  ``_should_run_spec_archival``, ``_run_spec_archival_pass``.
"""

from __future__ import annotations

import re
from pathlib import Path

from evolve.diagnostics import _save_subprocess_diagnostic
from evolve.git import _git_commit
from evolve.state import _detect_premature_converged, _runs_base
from evolve.tui import TUIProtocol


# ANSI-styled ``[probe]`` prefix.  The round subprocess writes to
# stdout; the parent orchestrator captures each line and routes it
# through ``RichTUI.subprocess_output`` which runs ``Text.from_ansi``
# on it, so embedding escape codes here gives us styled probe output
# in the Rich console without requiring a TUIProtocol method.  Dim
# cyan is visually quiet (probe lines are frequent and shouldn't
# compete with the agent's tool calls for attention) while still
# standing apart from plain text.
_PROBE_PREFIX = "\x1b[2;36m[probe]\x1b[0m"   # dim cyan
_PROBE_WARN_PREFIX = "\x1b[33m[probe]\x1b[0m"  # yellow — timeouts, failures
_PROBE_OK_PREFIX = "\x1b[32m[probe]\x1b[0m"    # green — PASSED, converged


def _probe(msg: str) -> None:
    """Emit a styled orchestrator probe line to stdout.

    Used by every orchestrator-side trace print so the ``[probe]``
    prefix renders consistently (dim cyan) in the Rich parent TUI
    and stays recognisable in plain-text logs.  Always flushes so
    the line arrives before the next tool call or subprocess event.
    """
    print(f"{_PROBE_PREFIX} {msg}", flush=True)


def _probe_warn(msg: str) -> None:
    """Probe line flagged as a warning (yellow prefix)."""
    print(f"{_PROBE_WARN_PREFIX} {msg}", flush=True)


def _probe_ok(msg: str) -> None:
    """Probe line flagged as a success (green prefix)."""
    print(f"{_PROBE_OK_PREFIX} {msg}", flush=True)


def _scaffold_shared_runtime_files(project_dir: Path, spec: str | None) -> None:
    """Pre-create shared cross-round runtime files at ``{runs_base}``.

    Predictable files that every evolution session needs — the
    backlog (``improvements.md``) and the cumulative learning log
    (``memory.md``) — are created by code, not by the agent's
    system prompt.  Rationale:

    - The agent's prompt can INSTRUCT it to write in a particular
      path but cannot GUARANTEE the path: a prompt glitch, a model
      change, or an ambiguous interpretation of "runs/improvements.md"
      vs "{run_dir}/improvements.md" can land the file in the wrong
      place, costing rounds to recover.
    - Pre-existing files at the canonical location are
      unambiguous — the agent reads and appends to them, rather
      than deciding where to create them.
    - Both files are trivial to scaffold with a sane default
      template; no work is lost and the contract is simple.

    Idempotent: existing files are never overwritten.  Both files
    land under ``_runs_base(project_dir)`` — the canonical
    ``.evolve/runs/`` on fresh projects, the legacy ``runs/`` on
    projects still mid-migration.

    Args:
        project_dir: Root directory of the project being evolved.
        spec: Optional spec filename (``SPEC.md``, ``README.md``, …)
            — forwarded to the memory scaffolder so the default
            pointer prose names the actual spec file.
    """
    runs_base = _runs_base(project_dir)
    runs_base.mkdir(parents=True, exist_ok=True)

    # improvements.md — shared backlog
    imp_path = runs_base / "improvements.md"
    if not imp_path.is_file():
        imp_path.write_text(
            "# Improvements\n\n"
            "Backlog of user-story items driving evolution — "
            "see SPEC.md § \"Item format — user story with "
            "acceptance criteria\".  Entries are appended by the "
            "Winston → John → final-draft persona pipeline; the "
            "orchestrator's pre-commit check rejects free-form "
            "additions.  New sessions append here; this file is "
            "shared across rounds and across sessions of the same "
            "project.\n"
        )

    # memory.md — cumulative learning log with typed sections
    mem_path = runs_base / "memory.md"
    if not mem_path.is_file():
        # Reuse the CLI's memory template renderer when available so
        # the cold-start scaffold is identical whether the operator
        # runs ``evolve init`` first or jumps straight to
        # ``evolve start``.  Falls back to a minimal inline template
        # if the import fails (defensive).  Function-local import
        # preserves the leaf-module invariant — ``evolve.cli`` MUST
        # NOT appear in this module's top-level imports.
        try:
            from evolve.cli import _render_default_memory_md
            mem_path.write_text(_render_default_memory_md(spec))
        except ImportError:  # pragma: no cover — defensive fallback
            mem_path.write_text(
                "# Agent Memory\n\n"
                "## Errors\n\n## Decisions\n\n## Patterns\n\n## Insights\n"
            )


def _is_self_evolving(project_dir: Path) -> bool:
    """Return True when evolve is evolving its own source tree.

    The ``RESTART_REQUIRED`` structural-change protocol protects the
    running orchestrator from stale imports after a rename, __init__.py
    edit, or entry-point move.  That only matters when the project
    being evolved IS the orchestrator's own code — typically, when
    ``project_dir`` resolves to the directory that contains the
    currently-imported ``evolve/`` package (this module's own parent).

    When evolve is driving a third-party project (the common case —
    ``python -m evolve start /path/to/foo``), structural changes in
    ``foo/`` never touch ``evolve/`` and the orchestrator's imports
    stay valid.  RESTART_REQUIRED in that case would be pure theatre:
    the marker still gets written as an audit trail, but the
    orchestrator keeps running.

    Comparison is done on resolved absolute paths to survive symlinks
    and relative invocations.

    Args:
        project_dir: Root directory of the project being evolved.

    Returns:
        True iff ``project_dir`` resolves to the same directory as the
        project that contains the currently-imported ``evolve`` package.
    """
    try:
        evolve_package_dir = Path(__file__).resolve().parent  # .../evolve
        evolve_project_root = evolve_package_dir.parent       # .../ (repo root)
        return project_dir.resolve() == evolve_project_root
    except (OSError, RuntimeError):
        # If we can't resolve (e.g. symlink loop, stale parent dir),
        # err on the side of caution — treat as self-evolving so the
        # safety protocol still fires.  A false positive is a harmless
        # exit 3; a false negative could leave a stale orchestrator.
        return True


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
    _probe(f"convergence-gate backstop rejected: {reason}")
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


def _run_curation_pass(
    project_dir: Path,
    run_dir: Path,
    round_num: int,
    improvements_path: Path,
    spec: str | None,
    ui: TUIProtocol,
) -> None:
    """Run memory curation (Mira) between rounds if triggered.

    SPEC § "Dedicated memory curation (Mira)".  Delegates to
    ``agent.run_memory_curation`` which handles prompt building, SDK
    invocation, shrinkage checks, and abort recovery.  This function
    is a thin orchestrator-side wrapper that resolves paths and logs
    the verdict.

    Function-local imports preserve the leaf-module invariant —
    ``evolve.agent`` MUST NOT appear in this module's top-level
    imports.  ``_runs_base`` / ``_git_commit`` are imported via
    ``evolve.orchestrator`` (re-exported binding) so existing tests
    that ``patch("evolve.orchestrator._git_commit")`` /
    ``patch("evolve.orchestrator._runs_base")`` continue to
    intercept — same lesson as ``memory.md`` round-7-of-20260427_114957.
    """
    from evolve.agent import run_memory_curation
    from evolve.orchestrator import _git_commit, _runs_base

    memory_path = _runs_base(project_dir) / "memory.md"
    spec_path = project_dir / (spec or "README.md") if spec else project_dir / "README.md"

    verdict = run_memory_curation(
        project_dir=project_dir,
        run_dir=run_dir,
        round_num=round_num,
        memory_path=memory_path,
        spec_path=spec_path,
    )

    if verdict == "SKIPPED":
        return  # silent — no log needed
    elif verdict == "CURATED":
        _probe(f"memory curation: CURATED at round {round_num}")
        # SPEC § "Dedicated memory curation (Mira)" — verdict routing:
        # CURATED → commit with `memory: compaction` marker so the
        # byte-size sanity gate (§ "Byte-size sanity gate") accepts
        # the shrink.  The curation modified memory.md in-place and
        # wrote an audit log; commit both.
        commit_msg = (
            f"chore(memory): curation round {round_num}\n\n"
            f"memory: compaction\n"
        )
        _git_commit(project_dir, commit_msg, ui)
    elif verdict == "ABORTED":
        _probe_warn(f"memory curation: ABORTED at round {round_num} (>80% shrink)")
    elif verdict == "SDK_FAIL":
        _probe_warn(f"memory curation: SDK_FAIL at round {round_num}")


def _should_run_spec_archival(project_dir: Path, round_num: int, spec: str | None = None) -> bool:
    """Return True when SPEC archival should run this round.

    Thin wrapper around ``agent._should_run_spec_archival`` that resolves
    the spec path from the project directory.  SPEC § "SPEC archival (Sid)"
    AC 3: this function lives in ``evolve/orchestrator.py``.

    Function-local import preserves the leaf-module invariant.
    """
    from evolve.agent import _should_run_spec_archival as _agent_check

    spec_path = project_dir / (spec or "README.md")
    return _agent_check(spec_path, round_num)


def _run_spec_archival_pass(
    project_dir: Path,
    run_dir: Path,
    round_num: int,
    spec: str | None,
    ui: TUIProtocol,
) -> None:
    """Run SPEC archival (Sid) between rounds if triggered.

    SPEC § "SPEC archival (Sid)".  Delegates to
    ``agent.run_spec_archival`` which handles prompt building, SDK
    invocation, shrinkage checks, and abort recovery.  This function
    is a thin orchestrator-side wrapper that resolves paths and logs
    the verdict.

    Function-local imports preserve the leaf-module invariant —
    ``_git_commit`` is imported via ``evolve.orchestrator`` so
    ``patch("evolve.orchestrator._git_commit")`` keeps intercepting.
    """
    from evolve.agent import run_spec_archival
    from evolve.orchestrator import _git_commit

    spec_path = project_dir / (spec or "README.md")
    if not spec_path.is_file():
        return

    verdict = run_spec_archival(
        project_dir=project_dir,
        run_dir=run_dir,
        round_num=round_num,
        spec_path=spec_path,
    )

    if verdict == "SKIPPED":
        return  # silent
    elif verdict == "ARCHIVED":
        _probe(f"SPEC archival: ARCHIVED at round {round_num}")
        commit_msg = (
            f"chore(spec): archival round {round_num}\n\n"
            f"SPEC sections moved to SPEC/archive/.\n"
        )
        _git_commit(project_dir, commit_msg, ui)
    elif verdict == "ABORTED":
        _probe_warn(f"SPEC archival: ABORTED at round {round_num} (>80% shrink)")
    elif verdict == "SDK_FAIL":
        _probe_warn(f"SPEC archival: SDK_FAIL at round {round_num}")
