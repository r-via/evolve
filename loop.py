"""Evolution loop orchestrator.

Each round runs as a separate subprocess so code changes are picked up immediately.
"""

from __future__ import annotations

import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path


def _count_checked(path: Path) -> int:
    if not path.is_file():
        return 0
    return len(re.findall(r"^- \[x\]", path.read_text(), re.MULTILINE))


def _count_unchecked(path: Path) -> int:
    if not path.is_file():
        return 0
    return len(re.findall(r"^- \[ \]", path.read_text(), re.MULTILINE))


def _is_needs_package(text: str) -> bool:
    """Check if an improvement text has [needs-package] as a leading tag token.

    Matches patterns like:
      [functional] [needs-package] description
      [performance] [needs-package] description
    Does NOT match [needs-package] mentioned in the description body.
    """
    return bool(re.match(r"\[[\w-]+\]\s+\[needs-package\]", text))


def _count_blocked(path: Path) -> int:
    """Count unchecked items that require [needs-package] (blocked without --yolo)."""
    if not path.is_file():
        return 0
    count = 0
    for line in path.read_text().splitlines():
        m = re.match(r"^- \[ \] (.+)$", line.strip())
        if m and _is_needs_package(m.group(1)):
            count += 1
    return count


def _get_current_improvement(path: Path, yolo: bool = False) -> str | None:
    if not path.is_file():
        return None
    for line in path.read_text().splitlines():
        m = re.match(r"^- \[ \] (.+)$", line.strip())
        if m:
            text = m.group(1)
            # Skip [needs-package] items unless --yolo is set
            if not yolo and _is_needs_package(text):
                continue
            return text
    return None


def evolve_loop(
    project_dir: Path,
    max_rounds: int = 10,
    check_cmd: str | None = None,
    yolo: bool = False,
    timeout: int = 300,
) -> None:
    """Orchestrate evolution by launching each round as a subprocess."""
    improvements_path = project_dir / "runs" / "improvements.md"

    # Create timestamped run directory
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = project_dir / "runs" / timestamp
    run_dir.mkdir(parents=True, exist_ok=True)
    print(f"  Run directory: {run_dir}")

    # Ensure git
    _ensure_git(project_dir)

    for round_num in range(1, max_rounds + 1):
        current = _get_current_improvement(improvements_path, yolo=yolo)
        checked = _count_checked(improvements_path)
        unchecked = _count_unchecked(improvements_path)

        print(f"\n{'#' * 60}")
        print(f"  EVOLUTION ROUND {round_num}/{max_rounds}")
        if current:
            print(f"  TARGET: {current}")
            print(f"  PROGRESS: {checked}/{checked + unchecked} improvements done")
        elif unchecked > 0:
            # All remaining unchecked items are blocked (needs-package without --yolo)
            blocked = _count_blocked(improvements_path)
            if blocked == unchecked:
                print(f"  ALL {blocked} remaining improvement(s) require new packages.")
                print(f"  Re-run with --yolo to allow package installation, or add new improvements.")
                print(f"{'#' * 60}")
                break
            print(f"  TARGET: (initial analysis)")
        else:
            print(f"  TARGET: (initial analysis)")
        print(f"{'#' * 60}")

        # Launch round as subprocess — picks up code changes from previous round
        evolve_script = Path(__file__).parent / "evolve.py"
        cmd = [
            sys.executable, str(evolve_script),
            "_round",
            str(project_dir),
            "--round-num", str(round_num),
            "--timeout", str(timeout),
            "--run-dir", str(run_dir),
        ]
        if check_cmd:
            cmd += ["--check", check_cmd]
        if yolo:
            cmd += ["--yolo"]

        result = subprocess.run(cmd, cwd=str(project_dir))

        if result.returncode != 0:
            print(f"\n  Round {round_num} failed (exit {result.returncode})")

        # Re-read improvements
        prev_checked = checked
        prev_unchecked = unchecked
        unchecked = _count_unchecked(improvements_path)
        checked = _count_checked(improvements_path)
        print(f"\n  Progress: {checked} done, {unchecked} remaining")

        # Stop if agent did nothing
        convo = run_dir / f"conversation_loop_{round_num}.md"
        if checked == prev_checked and unchecked == prev_unchecked:
            if not convo.is_file() or convo.stat().st_size < 100:
                print(f"\n  Agent made no progress — stopping.")
                print(f"  Is claude-agent-sdk installed? Run: evolve.py --help")
                break

        # Check convergence
        converged_path = run_dir / "CONVERGED"
        if converged_path.is_file():
            reason = converged_path.read_text().strip()
            print(f"\n*** CONVERGED at round {round_num} ***")
            print(f"  {reason}")

            # Launch party mode
            _run_party_mode(project_dir, run_dir)
            return

    unchecked = _count_unchecked(improvements_path)
    checked = _count_checked(improvements_path)
    print(f"\n*** Max rounds ({max_rounds}) reached — {checked} done, {unchecked} remaining ***")


def run_single_round(
    project_dir: Path,
    round_num: int,
    check_cmd: str | None = None,
    yolo: bool = False,
    timeout: int = 300,
    run_dir: Path | None = None,
) -> None:
    """Execute a single evolution round (called as subprocess)."""
    from agent import analyze_and_fix

    rdir = run_dir or (project_dir / "runs")
    rdir.mkdir(parents=True, exist_ok=True)
    improvements_path = project_dir / "runs" / "improvements.md"

    # 1. Run check command if provided
    check_output = ""
    if check_cmd:
        print(f"\n  [check] Running: {check_cmd}")
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
            print(f"  [check] {'PASS' if ok else 'FAIL'} (exit {result.returncode})")
        except subprocess.TimeoutExpired:
            check_output = f"TIMEOUT after {timeout}s"
            print(f"  [check] TIMEOUT")
    else:
        print(f"  [check] No check command configured")

    # 2. Let opus agent analyze and fix
    current = _get_current_improvement(improvements_path, yolo=yolo)
    print(f"\n  [agent] Claude opus working...")
    analyze_and_fix(
        project_dir=project_dir,
        check_output=check_output,
        check_cmd=check_cmd,
        yolo=yolo,
        round_num=round_num,
        run_dir=rdir,
    )

    # 3. Git commit + push
    commit_msg_path = rdir / "COMMIT_MSG"
    if commit_msg_path.is_file():
        msg = commit_msg_path.read_text().strip()
        commit_msg_path.unlink()
    else:
        new_current = _get_current_improvement(improvements_path, yolo=yolo)
        if current and new_current != current:
            msg = f"feat(evolve): ✓ {current}"
        else:
            msg = f"chore(evolve): round {round_num}"
    _git_commit(project_dir, msg)

    # 4. Re-run check after fixes
    if check_cmd:
        print(f"\n  [verify] Re-running: {check_cmd}")
        try:
            result = subprocess.run(
                check_cmd, shell=True, cwd=str(project_dir),
                capture_output=True, text=True, timeout=timeout,
            )
            ok = result.returncode == 0
            print(f"  [verify] {'PASS' if ok else 'FAIL'} (exit {result.returncode})")

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
            print(f"  [verify] TIMEOUT")


def _run_party_mode(project_dir: Path, run_dir: Path) -> None:
    """Launch party mode: multi-agent brainstorming post-convergence."""
    print("\n  Launching Party Mode — multi-agent brainstorming...")

    agents_dir = project_dir / "agents"
    if not agents_dir.is_dir():
        # Try evolve's own agents
        agents_dir = Path(__file__).parent / "agents"

    if not agents_dir.is_dir() or not list(agents_dir.glob("*.md")):
        print("  WARN: No agent personas found — skipping party mode")
        return

    # Load agents
    agents = []
    for f in sorted(agents_dir.glob("*.md")):
        try:
            agents.append({"file": f.name, "content": f.read_text()})
        except (OSError, UnicodeDecodeError):
            continue

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

    # Load context
    readme = (project_dir / "README.md").read_text() if (project_dir / "README.md").is_file() else "(none)"
    improvements = (project_dir / "runs" / "improvements.md").read_text() if (project_dir / "runs" / "improvements.md").is_file() else "(none)"
    memory = (project_dir / "runs" / "memory.md").read_text() if (project_dir / "runs" / "memory.md").is_file() else "(none)"
    converged = (run_dir / "CONVERGED").read_text().strip() if (run_dir / "CONVERGED").is_file() else ""

    roster = "\n".join(f"- {a['file']}" for a in agents)
    personas = "\n\n".join(f"### {a['file']}\n\n{a['content']}" for a in agents)

    prompt = f"""\
You are a Party Mode facilitator. The project has CONVERGED — all improvements done.

Your job: orchestrate a multi-agent brainstorming session, then produce:
1. `{run_dir}/party_report.md` — full discussion with each agent's reasoning
2. `{run_dir}/README_proposal.md` — complete updated README for the next evolution

## Workflow
{workflow}

## Agents
{roster}

## Agent Personas
{personas}

## Current README
{readme}

## Improvements History
{improvements}

## Memory
{memory}

## Convergence Reason
{converged}

Simulate the discussion, then write both files. The README_proposal.md must be complete (not a diff).
"""

    try:
        from agent import run_claude_agent
        import asyncio
        import warnings
        warnings.filterwarnings("ignore", message=".*cancel scope.*")
        warnings.filterwarnings("ignore", message=".*Event loop is closed.*")

        try:
            asyncio.run(run_claude_agent(prompt, project_dir, round_num=0, run_dir=run_dir, log_filename="party_conversation.md"))
        except RuntimeError as e:
            if "cancel scope" not in str(e) and "Event loop is closed" not in str(e):
                print(f"  WARN: Party mode failed ({e})")
                return
    except ImportError:
        print("  WARN: claude-agent-sdk not installed — skipping party mode")
        return

    proposal = run_dir / "README_proposal.md"
    report = run_dir / "party_report.md"
    if proposal.is_file():
        print(f"\n  README_proposal.md → {proposal}")
    if report.is_file():
        print(f"  party_report.md   → {report}")
    if proposal.is_file():
        print("  Review and accept/reject. If accepted: cp README_proposal.md README.md && evolve start .")


def _ensure_git(project_dir: Path) -> None:
    result = subprocess.run(
        ["git", "rev-parse", "--git-dir"],
        cwd=project_dir, capture_output=True, text=True,
    )
    if result.returncode != 0:
        print(f"ERROR: {project_dir} is not a git repository.", file=sys.stderr)
        sys.exit(1)

    status = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=project_dir, capture_output=True, text=True,
    )
    if status.stdout.strip():
        print("Uncommitted changes — committing snapshot...")
        subprocess.run(["git", "add", "-A"], cwd=project_dir)
        subprocess.run(
            ["git", "commit", "-m", "evolve: snapshot before evolution"],
            cwd=project_dir, capture_output=True,
        )


def _git_commit(project_dir: Path, message: str) -> None:
    subprocess.run(["git", "add", "-A"], cwd=project_dir)
    status = subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=project_dir)
    if status.returncode == 0:
        print(f"  [git] no changes")
        return
    subprocess.run(["git", "commit", "-m", message], cwd=project_dir, capture_output=True)
    result = subprocess.run(["git", "push"], cwd=project_dir, capture_output=True, text=True)
    if result.returncode == 0:
        print(f"  [git] {message} → pushed")
    else:
        print(f"  [git] {message} (push failed: {result.stderr.strip()[:100]})")
