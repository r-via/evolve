"""Use case: run a single evolution round.

Application layer — orchestration bounded context.
"""

from __future__ import annotations

import subprocess
import threading
import time
from pathlib import Path

__mod = __import__("evolve.tui", fromlist=["TUIProtocol"])
TUIProtocol = __mod.TUIProtocol


def run_single_round(
    project_dir: Path,
    round_num: int,
    check_cmd: str | None = None,
    allow_installs: bool = False,
    timeout: int = 20,
    run_dir: Path | None = None,
    model: str = "claude-opus-4-6",
    spec: str | None = None,
    yolo: bool | None = None,
    effort: str | None = "medium",
) -> None:
    """Execute a single evolution round (called as subprocess)."""
    if yolo is not None:
        allow_installs = yolo
    __mod = __import__("evolve.infrastructure.claude_sdk", fromlist=["runtime"])
    _runtime = __mod.runtime
    _runtime.MODEL = model
    _runtime.EFFORT = effort

    __mod = __import__("evolve.orchestrator", fromlist=["_runs_base", "get_tui", "_probe"])
    _runs_base = __mod._runs_base
    get_tui = __mod.get_tui
    _probe = __mod._probe

    rdir = run_dir or _runs_base(project_dir)
    rdir.mkdir(parents=True, exist_ok=True)
    improvements_path = _runs_base(project_dir) / "improvements.md"
    ui = get_tui()

    __mod = __import__("evolve.agent", fromlist=["MAX_TURNS"])
    _MAX_TURNS = __mod.MAX_TURNS
    _probe(
        f"round {round_num} starting — project={project_dir.name}, "
        f"model={model}, effort={effort}, max_turns={_MAX_TURNS}"
    )

    _round_heartbeat_stop = threading.Event()
    _round_start_time = time.monotonic()

    def _round_heartbeat():
        while not _round_heartbeat_stop.wait(30):
            elapsed = int(time.monotonic() - _round_start_time)
            _probe(f"round {round_num} alive — {elapsed}s elapsed")

    _round_hb_thread = threading.Thread(target=_round_heartbeat, daemon=True)
    _round_hb_thread.start()
    try:
        _run_single_round_body(
            project_dir=project_dir,
            round_num=round_num,
            check_cmd=check_cmd,
            allow_installs=allow_installs,
            timeout=timeout,
            rdir=rdir,
            improvements_path=improvements_path,
            ui=ui,
            spec=spec,
        )
    finally:
        _round_heartbeat_stop.set()


def _run_single_round_body(
    *,
    project_dir: Path,
    round_num: int,
    check_cmd: str | None,
    allow_installs: bool,
    timeout: int,
    rdir: Path,
    improvements_path: Path,
    ui: TUIProtocol,
    spec: str | None,
) -> None:
    """Body of ``run_single_round``."""
    __mod = __import__("evolve.agent", fromlist=["analyze_and_fix"])
    analyze_and_fix = __mod.analyze_and_fix

    __mod = __import__("evolve.orchestrator", fromlist=["_get_current_improvement", "_git_commit", "_probe", "_probe_ok", "_probe_warn"])
    _get_current_improvement = __mod._get_current_improvement
    _git_commit = __mod._git_commit
    _probe = __mod._probe
    _probe_ok = __mod._probe_ok
    _probe_warn = __mod._probe_warn

    check_output = ""
    pre_check_failed = False
    if check_cmd:
        _probe(f"running pre-check: {check_cmd} (max {timeout}s)")
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
            pre_check_failed = not ok
            ui.check_result("check", check_cmd, passed=ok)
            if ok:
                _probe_ok(f"pre-check PASSED (exit {result.returncode})")
            else:
                _probe_warn(f"pre-check FAILED (exit {result.returncode})")
        except subprocess.TimeoutExpired:
            check_output = f"TIMEOUT after {timeout}s"
            pre_check_failed = True
            ui.check_result("check", check_cmd, timeout=True)
            _probe_warn(f"pre-check TIMEOUT after {timeout}s (hit ceiling)")
    else:
        ui.no_check()
        _probe("no check command configured")

    current = _get_current_improvement(improvements_path, allow_installs=allow_installs)
    agent_subtype: str | None = None
    round_kind = "implement" if (current or pre_check_failed) else "draft"
    if round_kind == "implement":
        if not current and pre_check_failed:
            _probe(
                "pre-check failed with drained backlog — routing to implement "
                "(Phase 1 fixes the check before any drafting)"
            )
        _probe(f"invoking implement agent — target: {current}")
        ui.agent_working()
        __mod = __import__("evolve.agent", fromlist=["analyze_and_fix"])
        _analyze_and_fix = __mod.analyze_and_fix
        agent_subtype = _analyze_and_fix(
            project_dir=project_dir,
            check_output=check_output,
            check_cmd=check_cmd,
            allow_installs=allow_installs,
            round_num=round_num,
            run_dir=rdir,
            spec=spec,
            check_timeout=timeout,
        )
        _probe(f"implement agent finished (subtype={agent_subtype})")

        if agent_subtype:
            subtype_path = rdir / f"agent_subtype_round_{round_num}.txt"
            subtype_path.write_text(agent_subtype)
    else:
        _probe("backlog drained — invoking draft agent (Winston + John, Opus low)")
        ui.agent_working()
        __mod = __import__("evolve.agent", fromlist=["run_draft_agent"])
        _run_draft_agent = __mod.run_draft_agent
        _run_draft_agent(
            project_dir=project_dir,
            run_dir=rdir,
            spec=spec,
        )
        _probe("draft agent finished")

    commit_msg_path = rdir / "COMMIT_MSG"
    if commit_msg_path.is_file():
        msg = commit_msg_path.read_text().strip()
        commit_msg_path.unlink()
    else:
        new_current = _get_current_improvement(improvements_path, allow_installs=allow_installs)
        if current and new_current != current:
            msg = f"feat(evolve): ✓ {current}"
        else:
            msg = f"chore(evolve): round {round_num}"
    _probe(f"git commit: {msg[:80]}")
    _git_commit(project_dir, msg, ui)

    if check_cmd:
        _probe(f"running post-check: {check_cmd} (max {timeout}s)")
        ui.check_result("verify", check_cmd, passed=None)
        try:
            result = subprocess.run(
                check_cmd, shell=True, cwd=str(project_dir),
                capture_output=True, text=True, timeout=timeout,
            )
            ok = result.returncode == 0
            ui.check_result("verify", check_cmd, passed=ok)
            if ok:
                _probe_ok(f"post-check PASSED (exit {result.returncode})")
            else:
                _probe_warn(f"post-check FAILED (exit {result.returncode})")

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
            _probe_warn(f"post-check TIMEOUT after {timeout}s (hit ceiling)")

    if round_kind == "implement":
        try:
            __mod = __import__("evolve.agent", fromlist=["run_review_agent"])
            _run_review_agent = __mod.run_review_agent
            _probe("invoking review agent (Zara, Opus low)")
            _run_review_agent(
                project_dir=project_dir,
                run_dir=rdir,
                round_num=round_num,
                spec=spec,
            )
            _probe("review agent finished")
        except Exception as exc:
            _probe_warn(f"review agent error: {exc}")
    else:
        _probe("draft round — skipping review (Zara reviews implement rounds only)")

    _probe_ok(f"round {round_num} complete")
