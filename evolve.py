#!/usr/bin/env python3
"""evolve — Self-improving evolution loop for any project.

Takes a project directory with a README (the spec) and iteratively improves
the code until it fully converges to the specification.

Usage:
  python evolve.py init <project-dir>
  python evolve.py start <project-dir> [--rounds 10] [--check "pytest"] [--timeout 300] [--model claude-opus-4-6] [--allow-installs] [--json]
  python evolve.py start <project-dir> --resume
  python evolve.py status <project-dir>
  python evolve.py clean <project-dir> [--keep 5]
"""

import argparse
import subprocess
import sys
import warnings
from datetime import datetime
from pathlib import Path


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

    # Field definitions: (name, env_var, default, type)
    # type is "str", "int", or "bool" — controls parsing of env/file values
    # and CLI-set detection logic.
    fields = [
        ("check", "EVOLVE_CHECK", None, "str"),
        ("rounds", "EVOLVE_ROUNDS", 10, "int"),
        ("timeout", "EVOLVE_TIMEOUT", 300, "int"),
        ("model", "EVOLVE_MODEL", "claude-opus-4-6", "str"),
        ("allow_installs", "EVOLVE_ALLOW_INSTALLS", False, "bool"),
        ("spec", "EVOLVE_SPEC", None, "str"),
        ("capture_frames", "EVOLVE_CAPTURE_FRAMES", False, "bool"),
    ]

    # Deprecated fallback: check old yolo config/env if new name not found
    # (handled after the main loop)

    for name, env_var, default, ftype in fields:
        current = getattr(args, name, None)

        # Step 1: Check if CLI flag was explicitly set
        if ftype == "bool":
            cli_set = bool(current)
        elif ftype == "int":
            cli_set = any(
                a == f"--{name}" or a.startswith(f"--{name}=") for a in sys.argv
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
            elif ftype == "bool":
                if env_val.lower() in ("1", "true", "yes"):
                    setattr(args, name, True)
                    continue
            else:  # str
                setattr(args, name, env_val)
                continue

        # Step 3: Check config file
        if name in file_config:
            file_val = file_config[name]
            if ftype == "int":
                setattr(args, name, int(file_val))
            elif ftype == "bool":
                setattr(args, name, bool(file_val))
            elif file_val:  # str — only set if truthy (non-empty)
                setattr(args, name, file_val)
                continue
            if ftype != "str":
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
    evolve_dir = Path(__file__).parent
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

    # --- _round (internal) ---
    if len(sys.argv) > 1 and sys.argv[1] == "_round":
        args = _parse_round_args()
    else:
        args = ap.parse_args()

    if args.command == "init":
        _init_config(Path(args.project_dir).resolve())

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
            import tui as _tui_mod
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
            ))
        elif args.dry_run:
            from loop import run_dry_run
            run_dry_run(
                project_dir=project_path,
                check_cmd=args.check,
                timeout=args.timeout,
                model=args.model,
                spec=spec,
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
            )

    elif args.command == "status":
        _show_status(Path(args.project_dir).resolve())

    elif args.command == "history":
        _show_history(Path(args.project_dir).resolve())

    elif args.command == "clean":
        _clean_sessions(Path(args.project_dir).resolve(), args.keep)

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


def _init_config(project_dir: Path) -> None:
    """Scaffold an evolve.toml with default settings.

    Args:
        project_dir: Root directory where evolve.toml will be created.
    """
    config_path = project_dir / "evolve.toml"
    if config_path.is_file():
        print(f"evolve.toml already exists at {config_path}")
        return
    project_dir.mkdir(parents=True, exist_ok=True)
    config_path.write_text(_DEFAULT_EVOLVE_TOML)
    print(f"Created {config_path}")


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

    from tui import get_tui
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
    from tui import get_tui
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
