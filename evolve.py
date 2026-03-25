#!/usr/bin/env python3
"""evolve — Self-improving evolution loop for any project.

Takes a project directory with a README (the spec) and iteratively improves
the code until it fully converges to the specification.

Usage:
  python evolve.py init <project-dir>
  python evolve.py start <project-dir> [--rounds 10] [--check "pytest"] [--timeout 300] [--model claude-opus-4-6] [--yolo] [--json]
  python evolve.py start <project-dir> --resume
  python evolve.py status <project-dir>
  python evolve.py clean <project-dir> [--keep 5]
"""

import argparse
import subprocess
import sys
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
    """
    import os

    file_config = _load_config(project_dir)

    # Defaults
    defaults = {
        "check": None,
        "rounds": 10,
        "timeout": 300,
        "model": "claude-opus-4-6",
        "yolo": False,
    }

    # For each setting, apply resolution order
    # check: CLI (non-None) > env > file > default
    if args.check is not None:
        pass  # CLI wins
    elif os.environ.get("EVOLVE_CHECK"):
        args.check = os.environ["EVOLVE_CHECK"]
    elif "check" in file_config and file_config["check"]:
        args.check = file_config["check"]
    else:
        args.check = defaults["check"]

    # rounds: CLI (non-default) > env > file > default
    cli_rounds_set = any(a == "--rounds" or a.startswith("--rounds=") for a in sys.argv)
    if cli_rounds_set:
        pass  # CLI wins
    elif os.environ.get("EVOLVE_ROUNDS"):
        try:
            args.rounds = int(os.environ["EVOLVE_ROUNDS"])
        except ValueError:
            pass
    elif "rounds" in file_config:
        args.rounds = int(file_config["rounds"])

    # timeout: CLI (non-default) > env > file > default
    cli_timeout_set = "--timeout" in sys.argv
    if cli_timeout_set:
        pass  # CLI wins
    elif os.environ.get("EVOLVE_TIMEOUT"):
        try:
            args.timeout = int(os.environ["EVOLVE_TIMEOUT"])
        except ValueError:
            pass
    elif "timeout" in file_config:
        args.timeout = int(file_config["timeout"])

    # model: CLI (non-None) > env > file > default
    if args.model is not None:
        pass  # CLI wins
    elif os.environ.get("EVOLVE_MODEL"):
        args.model = os.environ["EVOLVE_MODEL"]
    elif "model" in file_config:
        args.model = file_config["model"]
    else:
        args.model = defaults["model"]

    # yolo: CLI (True) > env > file > default
    if args.yolo:
        pass  # CLI wins
    elif os.environ.get("EVOLVE_YOLO", "").lower() in ("1", "true", "yes"):
        args.yolo = True
    elif "yolo" in file_config:
        args.yolo = bool(file_config["yolo"])

    return args


def _check_deps():
    """Check that required dependencies are installed. Exit with install instructions if not."""
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
    start_p.add_argument("--yolo", action="store_true", help="Allow adding new packages/binaries")
    start_p.add_argument("--timeout", type=int, default=300, help="Timeout per check command in seconds (default: 300)")
    start_p.add_argument("--model", default=None, help="Claude model to use (default: claude-opus-4-6, or EVOLVE_MODEL env var)")
    start_p.add_argument("--resume", action="store_true", help="Resume the most recent interrupted session")
    start_p.add_argument("--forever", action="store_true", help="Autonomous forever mode — evolve indefinitely on a separate branch until convergence")
    start_p.add_argument("--json", action="store_true", help="Emit structured JSON events to stdout (for CI/CD)")

    # --- status ---
    status_p = sub.add_parser("status", help="Show evolution status for a project")
    status_p.add_argument("project_dir", help="Path to the project")

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
        # Merge CLI flags with config file + env vars
        args = _resolve_config(args, project_path)
        # Enable JSON TUI mode if --json flag is set
        if args.json:
            import tui as _tui_mod
            _tui_mod._use_json = True
        from loop import evolve_loop
        evolve_loop(
            project_dir=project_path,
            max_rounds=args.rounds,
            check_cmd=args.check,
            yolo=args.yolo,
            timeout=args.timeout,
            model=args.model,
            resume=args.resume,
            forever=args.forever,
        )

    elif args.command == "status":
        _show_status(Path(args.project_dir).resolve())

    elif args.command == "clean":
        _clean_sessions(Path(args.project_dir).resolve(), args.keep)

    elif args.command == "_round":
        from loop import run_single_round
        run_single_round(
            project_dir=Path(args.project_dir).resolve(),
            round_num=args.round_num,
            check_cmd=args.check,
            yolo=args.yolo,
            timeout=args.timeout,
            run_dir=Path(args.run_dir) if args.run_dir else None,
            model=args.model,
        )


def _parse_round_args():
    import argparse as _ap
    p = _ap.ArgumentParser(prog="evolve _round")
    p.add_argument("project_dir")
    p.add_argument("--round-num", type=int, required=True)
    p.add_argument("--check", default=None)
    p.add_argument("--timeout", type=int, default=300)
    p.add_argument("--run-dir", default=None)
    p.add_argument("--yolo", action="store_true")
    p.add_argument("--model", default="claude-opus-4-6")
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
yolo = false
"""


def _init_config(project_dir: Path) -> None:
    """Scaffold an evolve.toml with default settings."""
    config_path = project_dir / "evolve.toml"
    if config_path.is_file():
        print(f"evolve.toml already exists at {config_path}")
        return
    project_dir.mkdir(parents=True, exist_ok=True)
    config_path.write_text(_DEFAULT_EVOLVE_TOML)
    print(f"Created {config_path}")


def _clean_sessions(project_dir: Path, keep: int = 5) -> None:
    """Remove old session directories, keeping the N most recent."""
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


def _show_status(project_dir: Path):
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
