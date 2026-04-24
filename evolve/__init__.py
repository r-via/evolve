"""evolve — Self-improving evolution loop for any project.

Package marker and CLI entry point.  Re-exports all public names from
the CLI module for backward compatibility — ``from evolve import main``
continues to work.

Takes a project directory with a README (the spec) and iteratively improves
the code until it fully converges to the specification.

Usage:
  evolve init <project-dir>
  evolve start <project-dir> [--rounds 10] [--check "pytest"] [--timeout 300] [--model claude-opus-4-6] [--allow-installs] [--json]
  evolve start <project-dir> --resume
  evolve status <project-dir>
  evolve clean <project-dir> [--keep 5]
"""

import argparse
import subprocess
import sys
import warnings
from datetime import datetime
from pathlib import Path


#: Accepted values for the ``--effort`` flag / ``effort`` config key /
#: ``EVOLVE_EFFORT`` env var — SPEC.md § "The --effort flag".
EFFORT_LEVELS = ("low", "medium", "high", "max")


def _validate_effort(value: str) -> str:
    """Argparse ``type=`` validator for the ``--effort`` flag.

    Accepts only the four documented literals: ``low``, ``medium``,
    ``high``, ``max``.  Raises :class:`argparse.ArgumentTypeError` on any
    other value so the CLI exits with a clear message rather than
    failing opaquely inside ``ClaudeAgentOptions``.
    """
    if value not in EFFORT_LEVELS:
        raise argparse.ArgumentTypeError(
            f"invalid effort level: {value!r} "
            f"(must be one of: {', '.join(EFFORT_LEVELS)})"
        )
    return value


def _load_config(project_dir: Path) -> dict:
    """Load configuration from evolve.toml or pyproject.toml [tool.evolve].

    Resolution order (handled by caller — this returns file-level config):
    1. CLI flags (caller handles)
    2. Environment variables (caller handles)
    3. evolve.toml in project root
    4. pyproject.toml [tool.evolve] section
    5. Built-in defaults (caller handles)

    Args:
        project_dir: Root directory of the project to load config from.

    Returns:
        A dict of configuration values, or empty dict if no config found.
    """
    try:
        import tomllib
    except ImportError:
        try:
            import tomli as tomllib  # type: ignore[no-redef]
        except ImportError:
            return {}

    # Try evolve.toml first
    evolve_toml = project_dir / "evolve.toml"
    if evolve_toml.is_file():
        try:
            with open(evolve_toml, "rb") as f:
                return tomllib.load(f)
        except Exception:
            return {}

    # Fall back to pyproject.toml [tool.evolve]
    pyproject_toml = project_dir / "pyproject.toml"
    if pyproject_toml.is_file():
        try:
            with open(pyproject_toml, "rb") as f:
                data = tomllib.load(f)
            return data.get("tool", {}).get("evolve", {})
        except Exception:
            return {}

    return {}


def _resolve_config(args, project_dir: Path) -> argparse.Namespace:
    """Merge CLI args with config file settings.

    Resolution order (first wins):
    1. CLI flags
    2. Environment variables
    3. evolve.toml / pyproject.toml [tool.evolve]
    4. Built-in defaults

    Settings are resolved via a data-driven loop over field definitions,
    eliminating per-field duplication.

    Args:
        args: Parsed argparse Namespace from the CLI.
        project_dir: Root directory of the project.

    Returns:
        The mutated args Namespace with resolved values.
    """
    import os

    file_config = _load_config(project_dir)

    # Field definitions: (name, env_var, default, type[, config_key])
    # type is "str", "int", "bool", or "float" — controls parsing of env/file
    # values and CLI-set detection logic.  Optional 5th element overrides the
    # config-file key (defaults to name).
    fields = [
        ("check", "EVOLVE_CHECK", None, "str"),
        ("rounds", "EVOLVE_ROUNDS", 10, "int"),
        ("timeout", "EVOLVE_TIMEOUT", 300, "int"),
        ("model", "EVOLVE_MODEL", "claude-opus-4-6", "str"),
        ("allow_installs", "EVOLVE_ALLOW_INSTALLS", False, "bool"),
        ("spec", "EVOLVE_SPEC", None, "str"),
        ("capture_frames", "EVOLVE_CAPTURE_FRAMES", False, "bool"),
        ("effort", "EVOLVE_EFFORT", "medium", "str"),
        ("max_cost", "EVOLVE_MAX_COST", None, "float", "max_cost_usd"),
    ]

    # Deprecated fallback: check old yolo config/env if new name not found
    # (handled after the main loop)

    for field in fields:
        name, env_var, default, ftype = field[:4]
        config_key = field[4] if len(field) > 4 else name
        current = getattr(args, name, None)

        # Step 1: Check if CLI flag was explicitly set
        if ftype == "bool":
            cli_set = bool(current)
        elif ftype in ("int", "float"):
            cli_flag = f"--{name.replace('_', '-')}"
            cli_set = any(
                a == cli_flag or a.startswith(f"{cli_flag}=") for a in sys.argv
            )
        else:  # str
            cli_set = current is not None

        if cli_set:
            continue  # CLI wins

        # Step 2: Check environment variable
        env_val = os.environ.get(env_var, "")
        if env_val:
            if ftype == "int":
                try:
                    setattr(args, name, int(env_val))
                except ValueError:
                    pass  # invalid int — leave as-is
                continue  # env was present; skip file/default regardless
            elif ftype == "float":
                try:
                    setattr(args, name, float(env_val))
                    continue  # valid float from env — skip file/default
                except ValueError:
                    pass  # invalid float — fall through to file/default
            elif ftype == "bool":
                if env_val.lower() in ("1", "true", "yes"):
                    setattr(args, name, True)
                    continue
            else:  # str
                setattr(args, name, env_val)
                continue

        # Step 3: Check config file
        if config_key in file_config:
            file_val = file_config[config_key]
            if ftype == "int":
                setattr(args, name, int(file_val))
            elif ftype == "float":
                setattr(args, name, float(file_val))
            elif ftype == "bool":
                setattr(args, name, bool(file_val))
            elif file_val:  # str — only set if truthy (non-empty)
                setattr(args, name, file_val)
                continue
            if ftype not in ("str",):
                continue

        # Step 4: Apply default (only if not already set)
        if getattr(args, name, None) is None:
            setattr(args, name, default)

    # Deprecated fallback: if allow_installs is still False, check old
    # yolo config/env names and emit DeprecationWarning if found.
    if not getattr(args, "allow_installs", False):
        _yolo_env = os.environ.get("EVOLVE_YOLO", "")
        if _yolo_env.lower() in ("1", "true", "yes"):
            warnings.warn(
                "EVOLVE_YOLO is deprecated, use EVOLVE_ALLOW_INSTALLS instead",
                DeprecationWarning,
                stacklevel=2,
            )
            args.allow_installs = True
        elif "yolo" in file_config and file_config["yolo"]:
            warnings.warn(
                "'yolo' config key is deprecated, use 'allow_installs' instead",
                DeprecationWarning,
                stacklevel=2,
            )
            args.allow_installs = True
        elif "allow_installs" in file_config and file_config["allow_installs"]:
            args.allow_installs = True

    return args


def _check_deps():
    """Check that required dependencies are installed.

    Exits with code 2 and prints install instructions if claude-agent-sdk
    is not importable.  Detects whether a venv exists and tailors the
    instructions accordingly.
    """
    # __file__ is evolve/__init__.py; project root is one level up.
    evolve_dir = Path(__file__).resolve().parent.parent
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
    """Entry point for the evolve CLI.

    Parses command-line arguments and dispatches to the appropriate
    subcommand: init, start, status, or clean.
    """
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
    start_p.add_argument("--timeout", type=int, default=300, help="Timeout per check command in seconds (default: 300)")
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

    # --- _round (internal) ---
    if len(sys.argv) > 1 and sys.argv[1] == "_round":
        args = _parse_round_args()
    else:
        args = ap.parse_args()

    if args.command == "init":
        project_path = Path(args.project_dir).resolve()
        # Resolve --spec via _resolve_config so EVOLVE_SPEC env and (on
        # subsequent init re-runs) an existing evolve.toml are honored.
        # When spec is None / unspecified, the memory.md scaffold uses
        # the spec-filename-agnostic fallback prose.
        args = _resolve_config(args, project_path)
        _init_config(project_path, spec=getattr(args, "spec", None))

    elif args.command == "start":
        project_path = Path(args.project_dir).resolve()
        # Handle deprecated --yolo alias
        if getattr(args, "_yolo_deprecated", False):
            warnings.warn(
                "--yolo is deprecated, use --allow-installs instead",
                DeprecationWarning,
                stacklevel=2,
            )
            args.allow_installs = True
        # Merge CLI flags with config file + env vars
        args = _resolve_config(args, project_path)
        # Enable JSON TUI mode if --json flag is set
        if args.json:
            import evolve.tui as _tui_mod
            _tui_mod._use_json = True
        # Validate spec file exists if --spec is set
        spec = getattr(args, "spec", None)
        if spec:
            spec_path = project_path / spec
            if not spec_path.is_file():
                print(f"ERROR: spec file not found: {spec_path}")
                sys.exit(2)

        if args.validate:
            from loop import run_validate
            sys.exit(run_validate(
                project_dir=project_path,
                check_cmd=args.check,
                timeout=args.timeout,
                model=args.model,
                spec=spec,
                effort=getattr(args, "effort", "medium"),
            ))
        elif args.dry_run:
            from loop import run_dry_run
            run_dry_run(
                project_dir=project_path,
                check_cmd=args.check,
                timeout=args.timeout,
                model=args.model,
                spec=spec,
                effort=getattr(args, "effort", "medium"),
            )
        else:
            from loop import evolve_loop
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
        # Resolve --spec / --model via _resolve_config so EVOLVE_SPEC /
        # EVOLVE_MODEL env vars and evolve.toml are honored exactly like
        # they are for `evolve start`.
        args = _resolve_config(args, project_path)
        from loop import run_sync_readme
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
        # SPEC § "evolve diff": default effort is "low", not "medium".
        # _resolve_config defaults effort to "medium"; override to "low"
        # when the user didn't explicitly set effort via CLI/env/config.
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
        from loop import run_diff
        sys.exit(run_diff(
            project_dir=project_path,
            spec=spec,
            model=args.model or "claude-opus-4-6",
            effort=getattr(args, "effort", "low"),
        ))

    elif args.command == "_round":
        from loop import run_single_round
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
    """Parse CLI arguments for the internal ``evolve _round`` sub-command.

    This is invoked by the orchestrator when it spawns a monitored subprocess
    for a single evolution round.  It expects ``sys.argv[2:]`` to contain the
    project directory and flags such as ``--round-num``, ``--check``,
    ``--timeout``, ``--run-dir``, ``--allow-installs``, and ``--model``.

    Returns:
        An ``argparse.Namespace`` with all round parameters plus
        ``command="_round"``.
    """
    import argparse as _ap
    p = _ap.ArgumentParser(prog="evolve _round")
    p.add_argument("project_dir")
    p.add_argument("--round-num", type=int, required=True)
    p.add_argument("--check", default=None)
    p.add_argument("--timeout", type=int, default=300)
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
timeout = 300
model = "claude-opus-4-6"
allow_installs = false
spec = "README.md"
"""


# SPEC.md § "memory.md — cumulative learning log" — the four typed section
# headers the agent is expected to append into (`## Errors`, `## Decisions`,
# `## Patterns`, `## Insights`).  Pre-seeding the file with this scaffold
# gives new projects a concrete shape to append into from round 1 instead
# of starting with a bare `# Agent Memory` header and hoping the agent
# picks the right structure.  Empty sections are explicitly fine per SPEC
# ("The section shape is a scaffold, not a form to fill in.").
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
    """Return the runs/memory.md scaffold, optionally specialized to a spec.

    When ``spec`` is None (or ``"README.md"`` — the default spec), the
    returned string is ``_DEFAULT_MEMORY_MD`` verbatim, which uses the
    spec-filename-agnostic pointer prose ``"your project's spec file"``.
    When ``spec`` is an explicit filename (e.g. ``"SPEC.md"``,
    ``"CLAIMS.md"``, ``"docs/specification.md"``), the prose is
    substituted so the scaffolded file is self-documenting:
    ``"SPEC.md § `memory.md`"`` instead of the generic placeholder.

    The constant ``_DEFAULT_MEMORY_MD`` itself is untouched so the
    constant-drift test in ``tests/test_constant_drift.py`` still passes
    (it asserts the template doesn't hardcode any specific spec name).
    """
    if spec is None or spec == "README.md":
        return _DEFAULT_MEMORY_MD
    return _DEFAULT_MEMORY_MD.replace("your project's spec file", spec)


def _init_config(project_dir: Path, spec: str | None = None) -> None:
    """Scaffold an evolve.toml with default settings.

    Also pre-seeds ``runs/memory.md`` with the four typed section headers
    (``## Errors``, ``## Decisions``, ``## Patterns``, ``## Insights``) so
    new projects start with the structure SPEC.md § "memory.md" expects.
    Existing files are never overwritten — the scaffold only runs on a
    cold start.

    Args:
        project_dir: Root directory where evolve.toml will be created.
        spec: Optional spec filename (e.g. ``"SPEC.md"``). When provided,
            the memory.md scaffold's pointer prose references the actual
            filename so projects using a dedicated spec file get a
            self-documenting scaffold. When ``None`` (or ``"README.md"``),
            the spec-agnostic default wording is kept.
    """
    config_path = project_dir / "evolve.toml"
    config_created = False
    if config_path.is_file():
        print(f"evolve.toml already exists at {config_path}")
    else:
        project_dir.mkdir(parents=True, exist_ok=True)
        config_path.write_text(_DEFAULT_EVOLVE_TOML)
        print(f"Created {config_path}")
        config_created = True

    memory_path = project_dir / "runs" / "memory.md"
    if memory_path.is_file():
        if config_created:
            print(f"runs/memory.md already exists at {memory_path} — left untouched")
    else:
        memory_path.parent.mkdir(parents=True, exist_ok=True)
        memory_path.write_text(_render_default_memory_md(spec))
        print(f"Created {memory_path}")


def _clean_sessions(project_dir: Path, keep: int = 5) -> None:
    """Remove old session directories, keeping the N most recent.

    Args:
        project_dir: Root directory of the project.
        keep: Number of most-recent sessions to retain.
    """
    import shutil

    runs_dir = project_dir / "runs"
    if not runs_dir.is_dir():
        print("No runs directory found.")
        return

    sessions = sorted(
        [d for d in runs_dir.iterdir() if d.is_dir() and d.name[0].isdigit()],
        reverse=True,
    )

    if len(sessions) <= keep:
        print(f"Only {len(sessions)} session(s) found — nothing to clean (keeping {keep}).")
        return

    to_remove = sessions[keep:]
    for s in to_remove:
        shutil.rmtree(s)
        print(f"Removed {s.name}")

    print(f"Cleaned {len(to_remove)} session(s), kept {keep}.")


def _show_history(project_dir: Path) -> None:
    """Show evolution timeline across all sessions.

    Parses evolution_report.md and CONVERGED markers from each session
    directory to build a table of sessions with round counts, status,
    and improvement statistics.

    Args:
        project_dir: Root directory of the project.
    """
    import re as _re

    from evolve.tui import get_tui
    ui = get_tui()

    runs_dir = project_dir / "runs"
    if not runs_dir.is_dir():
        ui.history_empty(str(project_dir))
        return

    sessions = sorted(
        [d for d in runs_dir.iterdir() if d.is_dir() and d.name[0].isdigit()],
    )

    if not sessions:
        ui.history_empty(str(project_dir))
        return

    rows: list[dict] = []
    total_rounds = 0
    total_improvements = 0

    for session_dir in sessions:
        name = session_dir.name
        converged = (session_dir / "CONVERGED").is_file()
        status = "CONVERGED" if converged else "IN PROGRESS"

        # Parse evolution_report.md for round info
        report_path = session_dir / "evolution_report.md"
        rounds_str = "?"
        checked = 0
        unchecked = 0

        if report_path.is_file():
            report_text = report_path.read_text(errors="replace")
            # Extract "Rounds: N/M"
            m = _re.search(r"\*\*Rounds:\*\*\s*(\d+)/(\d+)", report_text)
            if m:
                rounds_str = f"{m.group(1)}/{m.group(2)}"
                total_rounds += int(m.group(1))
            # Extract "N improvements completed"
            m = _re.search(r"(\d+)\s+improvements completed", report_text)
            if m:
                checked = int(m.group(1))
                total_improvements += checked
            # Extract "N improvements remaining"
            m = _re.search(r"(\d+)\s+improvements remaining", report_text)
            if m:
                unchecked = int(m.group(1))
            # Extract status from report
            m = _re.search(r"\*\*Status:\*\*\s*(\w+)", report_text)
            if m:
                status = m.group(1)
        else:
            # Fall back: count conversation logs for round info
            convos = list(session_dir.glob("conversation_loop_*.md"))
            if convos:
                rounds_str = f"{len(convos)}/?"
                total_rounds += len(convos)

        rows.append({
            "name": name,
            "rounds": rounds_str,
            "status": status,
            "checked": checked,
            "unchecked": unchecked,
        })

    ui.history_table(str(project_dir), rows, len(sessions), total_rounds, total_improvements)


def _show_status(project_dir: Path):
    """Display the current evolution status of *project_dir*.

    Reads ``runs/improvements.md`` and ``runs/memory.md`` to summarise
    checked / unchecked / blocked improvements and accumulated errors.
    Also identifies the latest session directory and reports its round
    count, check count, and convergence status.

    Args:
        project_dir: Path to the target project.
    """
    from evolve.tui import get_tui
    ui = get_tui()

    runs_dir = project_dir / "runs"
    improvements_path = runs_dir / "improvements.md"
    memory_path = runs_dir / "memory.md"

    ui.status_header(str(project_dir), (project_dir / "README.md").is_file())

    if improvements_path.is_file():
        content = improvements_path.read_text()
        import re
        checked = len(re.findall(r"^- \[x\]", content, re.MULTILINE))
        unchecked = len(re.findall(r"^- \[ \]", content, re.MULTILINE))
        from loop import _count_blocked
        blocked = _count_blocked(improvements_path)
        ui.status_improvements(checked, unchecked, blocked)
    else:
        ui.status_no_improvements()

    if memory_path.is_file():
        lines = [l for l in memory_path.read_text().splitlines() if l.startswith("## Error:")]
        ui.status_memory(len(lines))
    else:
        ui.status_memory(0)

    # Show latest session
    if runs_dir.is_dir():
        sessions = sorted([d for d in runs_dir.iterdir() if d.is_dir() and d.name[0].isdigit()], reverse=True)
        if sessions:
            latest = sessions[0]
            converged = (latest / "CONVERGED").is_file()
            convos = len(list(latest.glob("conversation_loop_*.md")))
            checks = len(list(latest.glob("check_round_*.txt")))
            reason = (latest / "CONVERGED").read_text().strip() if converged else ""
            ui.status_session(latest.name, convos, checks, converged, reason)

    ui.status_flush()


if __name__ == "__main__":
    main()
