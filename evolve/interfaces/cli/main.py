"""evolve CLI main entry point.

Argparse, subcommand dispatch, and dependency checks.
Migrated from ``evolve/cli.py`` as part of the DDD restructuring.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import warnings
from datetime import datetime
from pathlib import Path

# Re-export CLI utility subcommands
from evolve.interfaces.cli.utils import (
    _clean_sessions,
    _show_history,
    _show_status,
)

# Re-export config-resolution helpers
from evolve.interfaces.cli.config import (
    EFFORT_LEVELS,
    _load_config,
    _resolve_config,
    _validate_effort,
)


def _check_deps():
    """Check that required dependencies are installed.

    Exits with code 2 and prints install instructions if claude-agent-sdk
    is not importable.
    """
    # __file__ is evolve/interfaces/cli/main.py; project root is three levels up.
    evolve_dir = Path(__file__).resolve().parent.parent.parent.parent
    venv_dir = evolve_dir / ".venv"

    # Check if we're running inside evolve's venv
    in_venv = hasattr(sys, "prefix") and str(venv_dir) in sys.prefix

    try:
        import claude_agent_sdk
        return  # all good
    except ImportError:
        pass

    print("ERROR: claude-agent-sdk is not installed.\n")

    if venv_dir.is_dir() and not in_venv:
        print(f"A virtual environment exists at {venv_dir}")
        print(f"Run evolve with the venv activated:\n")
        print(f"  source {venv_dir}/bin/activate")
        print(f"  python evolve.py start <project-dir>\n")
    elif venv_dir.is_dir() and in_venv:
        print(f"Install the SDK in the current venv:\n")
        print(f"  pip install claude-agent-sdk\n")
    else:
        print(f"Set up a virtual environment:\n")
        print(f"  cd {evolve_dir}")
        print(f"  python3 -m venv .venv")
        print(f"  source .venv/bin/activate")
        print(f"  pip install claude-agent-sdk")
        print(f"  python evolve.py start <project-dir>\n")

    sys.exit(2)


def main():
    """Entry point for the evolve CLI."""
    _check_deps()

    ap = argparse.ArgumentParser(
        prog="evolve",
        description="Self-improving evolution loop for any project. "
        "Reads the README as spec, iteratively fixes and improves code until convergence.",
    )
    ap.add_argument("--version", action="version", version="evolve 0.1.0")

    sub = ap.add_subparsers(dest="command", required=True)

    # --- init ---
    init_p = sub.add_parser("init", help="Initialize an evolve.toml config file")
    init_p.add_argument("project_dir", help="Path to the project to initialize")
    init_p.add_argument("--spec", default=None,
                        help="Spec filename relative to project dir (default: README.md, or EVOLVE_SPEC env var). "
                             "Used to self-document the scaffolded runs/memory.md pointer prose.")

    # --- start ---
    start_p = sub.add_parser("start", help="Start an evolution loop")
    start_p.add_argument("project_dir", help="Path to the project to evolve")
    start_p.add_argument("--rounds", type=int, default=10, help="Max evolution rounds")
    start_p.add_argument("--check", default=None, help="Verification command (e.g. 'pytest', 'npm test', 'cargo test')")
    start_p.add_argument("--allow-installs", action="store_true", dest="allow_installs",
                         help="Allow adding new packages/binaries for [needs-package] items")
    start_p.add_argument("--yolo", action="store_true", dest="_yolo_deprecated",
                         help=argparse.SUPPRESS)  # deprecated alias
    start_p.add_argument("--timeout", type=int, default=20, help="Timeout per check command in seconds (default: 20 — kills slow tests for analysis)")
    start_p.add_argument("--model", default=None, help="Claude model to use (default: claude-opus-4-6, or EVOLVE_MODEL env var)")
    start_p.add_argument("--resume", action="store_true", help="Resume the most recent interrupted session")
    start_p.add_argument("--forever", action="store_true", help="Autonomous forever mode — evolve indefinitely on a separate branch until convergence")
    start_p.add_argument("--dry-run", action="store_true", dest="dry_run", help="Read-only analysis mode — produces a report without modifying files")
    start_p.add_argument("--validate", action="store_true", help="Validate spec compliance — pass/fail per README claim (exit 0=pass, 1=fail)")
    start_p.add_argument("--json", action="store_true", help="Emit structured JSON events to stdout (for CI/CD)")
    start_p.add_argument("--spec", default=None, help="Path to spec file relative to project dir (default: README.md, or EVOLVE_SPEC env var)")
    start_p.add_argument("--capture-frames", action="store_true", dest="capture_frames", help="Capture TUI frames as PNG at round end / convergence / errors")
    start_p.add_argument(
        "--effort",
        type=_validate_effort,
        default=None,
        help="Reasoning effort level passed to the Claude Agent SDK: low, medium, high, or max (default: medium, or EVOLVE_EFFORT env var)",
    )
    start_p.add_argument(
        "--max-cost",
        type=float,
        default=None,
        dest="max_cost",
        help="Budget cap in USD — pause session after estimated cost exceeds this amount (default: no cap, or EVOLVE_MAX_COST env var)",
    )

    # --- status ---
    status_p = sub.add_parser("status", help="Show evolution status for a project")
    status_p.add_argument("project_dir", help="Path to the project")

    # --- history ---
    history_p = sub.add_parser("history", help="Show evolution timeline across all sessions")
    history_p.add_argument("project_dir", help="Path to the project")

    # --- clean ---
    clean_p = sub.add_parser("clean", help="Clean up old session directories")
    clean_p.add_argument("project_dir", help="Path to the project")
    clean_p.add_argument("--keep", type=int, default=5, help="Number of recent sessions to keep (default: 5)")

    # --- sync-readme ---
    sync_p = sub.add_parser(
        "sync-readme",
        help="Refresh README.md to reflect the current spec (one-shot, never runs during rounds)",
    )
    sync_p.add_argument("project_dir", nargs="?", default=".", help="Path to the project (default: cwd)")
    sync_p.add_argument("--spec", default=None,
                        help="Path to spec file relative to project dir (default: README.md, or EVOLVE_SPEC env var)")
    sync_p.add_argument("--apply", action="store_true",
                        help="Write directly to README.md and commit (default: write README_proposal.md only)")
    sync_p.add_argument("--model", default=None,
                        help="Claude model to use (default: claude-opus-4-6, or EVOLVE_MODEL env var)")
    sync_p.add_argument(
        "--effort",
        type=_validate_effort,
        default=None,
        help="Reasoning effort level passed to the Claude Agent SDK: low, medium, high, or max (default: medium, or EVOLVE_EFFORT env var)",
    )

    # --- diff ---
    diff_p = sub.add_parser(
        "diff",
        help="Show delta between spec and implementation (lightweight gap detection)",
    )
    diff_p.add_argument("project_dir", nargs="?", default=".", help="Path to the project (default: cwd)")
    diff_p.add_argument("--spec", default=None,
                        help="Path to spec file relative to project dir (default: README.md, or EVOLVE_SPEC env var)")
    diff_p.add_argument("--model", default=None,
                        help="Claude model to use (default: claude-opus-4-6, or EVOLVE_MODEL env var)")
    diff_p.add_argument(
        "--effort",
        type=_validate_effort,
        default=None,
        help="Reasoning effort level (default: low for diff subcommand)",
    )

    # --- update ---
    update_p = sub.add_parser(
        "update",
        help="Pull latest evolve from upstream (one-shot, never runs during rounds)",
    )
    update_p.add_argument(
        "--dry-run",
        action="store_true",
        dest="dry_run",
        help="Show what would change without applying",
    )
    update_p.add_argument(
        "--ref",
        default=None,
        help="Specific git ref to pull (default: origin/HEAD)",
    )

    # --- _round (internal) ---
    if len(sys.argv) > 1 and sys.argv[1] == "_round":
        args = _parse_round_args()
    else:
        args = ap.parse_args()

    if args.command == "init":
        project_path = Path(args.project_dir).resolve()
        args = _resolve_config(args, project_path)
        _init_config(project_path, spec=getattr(args, "spec", None))

    elif args.command == "start":
        project_path = Path(args.project_dir).resolve()
        if getattr(args, "_yolo_deprecated", False):
            warnings.warn(
                "--yolo is deprecated, use --allow-installs instead",
                DeprecationWarning,
                stacklevel=2,
            )
            args.allow_installs = True
        args = _resolve_config(args, project_path)
        if args.json:
            _tui_mod = __import__("evolve.tui", fromlist=["_use_json"])
            _tui_mod._use_json = True
        spec = getattr(args, "spec", None)
        if spec:
            spec_path = project_path / spec
            if not spec_path.is_file():
                print(f"ERROR: spec file not found: {spec_path}")
                sys.exit(2)

        if args.validate:
            __mod = __import__("evolve.orchestrator", fromlist=["run_validate"])
            run_validate = __mod.run_validate
            sys.exit(run_validate(
                project_dir=project_path,
                check_cmd=args.check,
                timeout=args.timeout,
                model=args.model,
                spec=spec,
                effort=getattr(args, "effort", "medium"),
            ))
        elif args.dry_run:
            __mod = __import__("evolve.orchestrator", fromlist=["run_dry_run"])
            run_dry_run = __mod.run_dry_run
            run_dry_run(
                project_dir=project_path,
                check_cmd=args.check,
                timeout=args.timeout,
                model=args.model,
                spec=spec,
                effort=getattr(args, "effort", "medium"),
            )
        else:
            __mod = __import__("evolve.orchestrator", fromlist=["evolve_loop"])
            evolve_loop = __mod.evolve_loop
            evolve_loop(
                project_dir=project_path,
                max_rounds=args.rounds,
                check_cmd=args.check,
                allow_installs=args.allow_installs,
                timeout=args.timeout,
                model=args.model,
                resume=args.resume,
                forever=args.forever,
                spec=spec,
                capture_frames=getattr(args, "capture_frames", False),
                effort=getattr(args, "effort", "medium"),
                max_cost=getattr(args, "max_cost", None),
            )

    elif args.command == "status":
        _show_status(Path(args.project_dir).resolve())

    elif args.command == "history":
        _show_history(Path(args.project_dir).resolve())

    elif args.command == "clean":
        _clean_sessions(Path(args.project_dir).resolve(), args.keep)

    elif args.command == "sync-readme":
        project_path = Path(args.project_dir).resolve()
        args = _resolve_config(args, project_path)
        __mod = __import__("evolve.orchestrator", fromlist=["run_sync_readme"])
        run_sync_readme = __mod.run_sync_readme
        sys.exit(run_sync_readme(
            project_dir=project_path,
            spec=getattr(args, "spec", None),
            apply=getattr(args, "apply", False),
            model=args.model or "claude-opus-4-6",
            effort=getattr(args, "effort", "medium"),
        ))

    elif args.command == "diff":
        import os as _os
        project_path = Path(args.project_dir).resolve()
        args = _resolve_config(args, project_path)
        _effort_cli = any(a == "--effort" or a.startswith("--effort=") for a in sys.argv)
        _effort_env = bool(_os.environ.get("EVOLVE_EFFORT", ""))
        _effort_cfg = "effort" in _load_config(project_path)
        if not (_effort_cli or _effort_env or _effort_cfg):
            args.effort = "low"
        spec = getattr(args, "spec", None)
        if spec:
            spec_path = project_path / spec
            if not spec_path.is_file():
                print(f"ERROR: spec file not found: {spec_path}")
                sys.exit(2)
        __mod = __import__("evolve.orchestrator", fromlist=["run_diff"])
        run_diff = __mod.run_diff
        sys.exit(run_diff(
            project_dir=project_path,
            spec=spec,
            model=args.model or "claude-opus-4-6",
            effort=getattr(args, "effort", "low"),
        ))

    elif args.command == "update":
        __mod = __import__("evolve.updater", fromlist=["run_update"])
        run_update = __mod.run_update
        sys.exit(run_update(
            dry_run=getattr(args, "dry_run", False),
            ref=getattr(args, "ref", None),
        ))

    elif args.command == "_round":
        __mod = __import__("evolve.orchestrator", fromlist=["run_single_round"])
        run_single_round = __mod.run_single_round
        run_single_round(
            project_dir=Path(args.project_dir).resolve(),
            round_num=args.round_num,
            check_cmd=args.check,
            allow_installs=args.allow_installs,
            timeout=args.timeout,
            run_dir=Path(args.run_dir) if args.run_dir else None,
            model=args.model,
            spec=args.spec,
            effort=args.effort,
        )


def _parse_round_args():
    """Parse CLI arguments for the internal ``evolve _round`` sub-command."""
    import argparse as _ap
    p = _ap.ArgumentParser(prog="evolve _round")
    p.add_argument("project_dir")
    p.add_argument("--round-num", type=int, required=True)
    p.add_argument("--check", default=None)
    p.add_argument("--timeout", type=int, default=20)
    p.add_argument("--run-dir", default=None)
    p.add_argument("--allow-installs", action="store_true", dest="allow_installs")
    p.add_argument("--yolo", action="store_true", dest="allow_installs")  # deprecated alias
    p.add_argument("--model", default="claude-opus-4-6")
    p.add_argument("--spec", default=None)
    p.add_argument("--effort", type=_validate_effort, default="medium")
    args = p.parse_args(sys.argv[2:])
    args.command = "_round"
    return args


_DEFAULT_EVOLVE_TOML = """\
# evolve.toml — configuration for evolve
# See README.md for details on each option.

check = ""
rounds = 10
timeout = 20
model = "claude-opus-4-6"
allow_installs = false
spec = "README.md"
"""

_DEFAULT_MEMORY_MD = """\
# Agent Memory

Cumulative learning log across evolution rounds. Append-only. See
your project's spec file § `memory.md` for the discipline (length
cap ≤ 5 lines / 400 chars, telegraphic style, non-obvious gate).
Compact only when the file exceeds ~500 lines; archive entries older
than 20 rounds under a `## Archive` section rather than deleting them.

## Errors

## Decisions

## Patterns

## Insights
"""


def _render_default_memory_md(spec: str | None = None) -> str:
    """Return the runs/memory.md scaffold, optionally specialized to a spec."""
    if spec is None or spec == "README.md":
        return _DEFAULT_MEMORY_MD
    return _DEFAULT_MEMORY_MD.replace("your project's spec file", spec)


def _init_config(project_dir: Path, spec: str | None = None) -> None:
    """Scaffold an evolve.toml with default settings."""
    config_path = project_dir / "evolve.toml"
    config_created = False
    if config_path.is_file():
        print(f"evolve.toml already exists at {config_path}")
    else:
        project_dir.mkdir(parents=True, exist_ok=True)
        config_path.write_text(_DEFAULT_EVOLVE_TOML)
        print(f"Created {config_path}")
        config_created = True

    __mod = __import__("evolve.state", fromlist=["_runs_base"])
    _runs_base = __mod._runs_base
    memory_path = _runs_base(project_dir) / "memory.md"
    if memory_path.is_file():
        if config_created:
            print(f"runs/memory.md already exists at {memory_path} — left untouched")
    else:
        memory_path.parent.mkdir(parents=True, exist_ok=True)
        memory_path.write_text(_render_default_memory_md(spec))
        print(f"Created {memory_path}")
