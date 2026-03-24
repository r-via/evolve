#!/usr/bin/env python3
"""evolve — Self-improving evolution loop for any project.

Takes a project directory with a README (the spec) and iteratively improves
the code until it fully converges to the specification.

Usage:
  python evolve.py start <project-dir> [--rounds 10] [--check "pytest"] [--timeout 300] [--yolo]
  python evolve.py status <project-dir>
"""

import argparse
import subprocess
import sys
from datetime import datetime
from pathlib import Path


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

    sys.exit(1)


def main():
    _check_deps()

    ap = argparse.ArgumentParser(
        prog="evolve",
        description="Self-improving evolution loop for any project. "
        "Reads the README as spec, iteratively fixes and improves code until convergence.",
    )
    ap.add_argument("--version", action="version", version="evolve 0.1.0")

    sub = ap.add_subparsers(dest="command", required=True)

    # --- start ---
    start_p = sub.add_parser("start", help="Start an evolution loop")
    start_p.add_argument("project_dir", help="Path to the project to evolve")
    start_p.add_argument("--rounds", type=int, default=10, help="Max evolution rounds")
    start_p.add_argument("--check", default=None, help="Verification command (e.g. 'pytest', 'npm test', 'cargo test')")
    start_p.add_argument("--yolo", action="store_true", help="Allow adding new packages/binaries")
    start_p.add_argument("--timeout", type=int, default=300, help="Timeout per check command in seconds (default: 300)")

    # --- status ---
    status_p = sub.add_parser("status", help="Show evolution status for a project")
    status_p.add_argument("project_dir", help="Path to the project")

    # --- _round (internal) ---
    if len(sys.argv) > 1 and sys.argv[1] == "_round":
        args = _parse_round_args()
    else:
        args = ap.parse_args()

    if args.command == "start":
        from loop import evolve_loop
        evolve_loop(
            project_dir=Path(args.project_dir).resolve(),
            max_rounds=args.rounds,
            check_cmd=args.check,
            yolo=args.yolo,
            timeout=args.timeout,
        )

    elif args.command == "status":
        _show_status(Path(args.project_dir).resolve())

    elif args.command == "_round":
        from loop import run_single_round
        run_single_round(
            project_dir=Path(args.project_dir).resolve(),
            round_num=args.round_num,
            check_cmd=args.check,
            yolo=args.yolo,
            timeout=args.timeout,
            run_dir=Path(args.run_dir) if args.run_dir else None,
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
    args = p.parse_args(sys.argv[2:])
    args.command = "_round"
    return args


def _show_status(project_dir: Path):
    runs_dir = project_dir / "runs"
    improvements_path = runs_dir / "improvements.md"
    memory_path = runs_dir / "memory.md"

    print(f"\n  Project: {project_dir}")
    print(f"  README:  {'exists' if (project_dir / 'README.md').is_file() else 'MISSING'}")

    if improvements_path.is_file():
        content = improvements_path.read_text()
        import re
        checked = len(re.findall(r"^- \[x\]", content, re.MULTILINE))
        unchecked = len(re.findall(r"^- \[ \]", content, re.MULTILINE))
        # Count blocked items (needs-package without --yolo)
        from loop import _count_blocked
        blocked = _count_blocked(improvements_path)
        status_line = f"  Improvements: {checked} done, {unchecked} remaining"
        if blocked > 0:
            status_line += f" ({blocked} blocked (needs-package))"
        print(status_line)
    else:
        print(f"  Improvements: (none yet)")

    if memory_path.is_file():
        lines = [l for l in memory_path.read_text().splitlines() if l.startswith("## Error:")]
        print(f"  Memory: {len(lines)} entries")
    else:
        print(f"  Memory: (empty)")

    # Show latest session
    if runs_dir.is_dir():
        sessions = sorted([d for d in runs_dir.iterdir() if d.is_dir() and d.name[0].isdigit()], reverse=True)
        if sessions:
            latest = sessions[0]
            converged = (latest / "CONVERGED").is_file()
            convos = len(list(latest.glob("conversation_loop_*.md")))
            checks = len(list(latest.glob("check_round_*.txt")))
            print(f"  Latest session: {latest.name} ({convos} rounds, {checks} checks)")
            print(f"  Converged: {'YES' if converged else 'NO'}")
            if converged:
                print(f"  Reason: {(latest / 'CONVERGED').read_text().strip()[:200]}")
    print()


if __name__ == "__main__":
    main()
