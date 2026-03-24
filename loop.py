"""Evolution loop orchestrator.

Each round runs as a separate subprocess so code changes are picked up immediately.
"""

from __future__ import annotations

import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from tui import get_tui


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
    model: str = "claude-opus-4-6",
    resume: bool = False,
) -> None:
    """Orchestrate evolution by launching each round as a subprocess."""
    improvements_path = project_dir / "runs" / "improvements.md"

    start_round = 1

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
                convos = sorted(run_dir.glob("conversation_loop_*.md"))
                if convos:
                    last = convos[-1].stem  # conversation_loop_N
                    try:
                        last_round = int(last.rsplit("_", 1)[1])
                        start_round = last_round + 1
                    except (ValueError, IndexError):
                        pass
                ui = get_tui()
                ui.run_dir_info(f"{run_dir} (resumed from round {start_round})")

                # Ensure git
                _ensure_git(project_dir, ui)

                # Jump to loop body
                return _run_rounds(
                    project_dir, run_dir, improvements_path, ui,
                    start_round, max_rounds, check_cmd, yolo, timeout, model,
                )

    # Create timestamped run directory
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = project_dir / "runs" / timestamp
    run_dir.mkdir(parents=True, exist_ok=True)
    ui = get_tui()
    ui.run_dir_info(str(run_dir))

    # Ensure git
    _ensure_git(project_dir, ui)

    _run_rounds(
        project_dir, run_dir, improvements_path, ui,
        1, max_rounds, check_cmd, yolo, timeout, model,
    )


def _run_rounds(
    project_dir: Path,
    run_dir: Path,
    improvements_path: Path,
    ui,
    start_round: int,
    max_rounds: int,
    check_cmd: str | None,
    yolo: bool,
    timeout: int,
    model: str,
) -> None:
    """Run evolution rounds from start_round to max_rounds."""
    for round_num in range(start_round, max_rounds + 1):
        current = _get_current_improvement(improvements_path, yolo=yolo)
        checked = _count_checked(improvements_path)
        unchecked = _count_unchecked(improvements_path)

        if current:
            ui.round_header(round_num, max_rounds, target=current,
                            checked=checked, total=checked + unchecked)
        elif unchecked > 0:
            # All remaining unchecked items are blocked (needs-package without --yolo)
            blocked = _count_blocked(improvements_path)
            if blocked == unchecked:
                ui.round_header(round_num, max_rounds)
                ui.blocked_message(blocked)
                sys.exit(1)
            ui.round_header(round_num, max_rounds, target="(initial analysis)")
        else:
            ui.round_header(round_num, max_rounds, target="(initial analysis)")

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
        if yolo:
            cmd += ["--yolo"]

        result = subprocess.run(cmd, cwd=str(project_dir))

        if result.returncode != 0:
            ui.round_failed(round_num, result.returncode)

        # Re-read improvements
        prev_checked = checked
        prev_unchecked = unchecked
        unchecked = _count_unchecked(improvements_path)
        checked = _count_checked(improvements_path)
        ui.progress_summary(checked, unchecked)

        # Stop if agent did nothing
        convo = run_dir / f"conversation_loop_{round_num}.md"
        if checked == prev_checked and unchecked == prev_unchecked:
            if not convo.is_file() or convo.stat().st_size < 100:
                ui.no_progress()
                sys.exit(1)

        # Check convergence
        converged_path = run_dir / "CONVERGED"
        if converged_path.is_file():
            reason = converged_path.read_text().strip()
            ui.converged(round_num, reason)

            # Launch party mode
            _run_party_mode(project_dir, run_dir, ui)
            sys.exit(0)

    unchecked = _count_unchecked(improvements_path)
    checked = _count_checked(improvements_path)
    ui.max_rounds(max_rounds, checked, unchecked)
    sys.exit(1)


def run_single_round(
    project_dir: Path,
    round_num: int,
    check_cmd: str | None = None,
    yolo: bool = False,
    timeout: int = 300,
    run_dir: Path | None = None,
    model: str = "claude-opus-4-6",
) -> None:
    """Execute a single evolution round (called as subprocess)."""
    from agent import analyze_and_fix
    import agent as _agent_mod
    _agent_mod.MODEL = model

    rdir = run_dir or (project_dir / "runs")
    rdir.mkdir(parents=True, exist_ok=True)
    improvements_path = project_dir / "runs" / "improvements.md"
    ui = get_tui()

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

    # 2. Let opus agent analyze and fix
    current = _get_current_improvement(improvements_path, yolo=yolo)
    ui.agent_working()
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
    _git_commit(project_dir, msg, ui)

    # 4. Re-run check after fixes
    if check_cmd:
        ui.check_result("verify", check_cmd, passed=None)
        try:
            result = subprocess.run(
                check_cmd, shell=True, cwd=str(project_dir),
                capture_output=True, text=True, timeout=timeout,
            )
            ok = result.returncode == 0
            ui.check_result("verify", check_cmd, passed=ok)

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


def _run_party_mode(project_dir: Path, run_dir: Path, ui=None) -> None:
    """Launch party mode: multi-agent brainstorming post-convergence."""
    if ui is None:
        ui = get_tui()
    ui.party_mode()

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
        from agent import run_claude_agent, _is_benign_runtime_error, _should_retry_rate_limit
        import asyncio
        import time
        import warnings
        warnings.filterwarnings("ignore", message=".*cancel scope.*")
        warnings.filterwarnings("ignore", message=".*Event loop is closed.*")

        max_retries = 5
        for attempt in range(1, max_retries + 1):
            try:
                asyncio.run(run_claude_agent(prompt, project_dir, round_num=0, run_dir=run_dir, log_filename="party_conversation.md"))
                break
            except Exception as e:
                if isinstance(e, RuntimeError) and _is_benign_runtime_error(e):
                    break

                wait = _should_retry_rate_limit(e, attempt, max_retries)
                if wait is not None:
                    ui.sdk_rate_limited(wait, attempt, max_retries)
                    time.sleep(wait)
                    continue

                ui.warn(f"Party mode failed ({e})")
                return
    except ImportError:
        ui.warn("claude-agent-sdk not installed — skipping party mode")
        return

    proposal = run_dir / "README_proposal.md"
    report = run_dir / "party_report.md"
    ui.party_results(
        str(proposal) if proposal.is_file() else None,
        str(report) if report.is_file() else None,
    )


def _ensure_git(project_dir: Path, ui=None) -> None:
    if ui is None:
        ui = get_tui()
    result = subprocess.run(
        ["git", "rev-parse", "--git-dir"],
        cwd=project_dir, capture_output=True, text=True,
    )
    if result.returncode != 0:
        ui.error(f"ERROR: {project_dir} is not a git repository.")
        sys.exit(1)

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


def _git_commit(project_dir: Path, message: str, ui=None) -> None:
    if ui is None:
        ui = get_tui()
    subprocess.run(["git", "add", "-A"], cwd=project_dir)
    status = subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=project_dir)
    if status.returncode == 0:
        ui.git_status(message, pushed=None)
        return
    subprocess.run(["git", "commit", "-m", message], cwd=project_dir, capture_output=True)
    result = subprocess.run(["git", "push"], cwd=project_dir, capture_output=True, text=True)
    if result.returncode == 0:
        ui.git_status(message, pushed=True)
    else:
        ui.git_status(message, pushed=False, error=result.stderr.strip()[:100])
