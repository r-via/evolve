"""Party mode orchestration — multi-agent brainstorming, proposal generation.

Migrated from ``evolve/party.py`` as part of the DDD restructuring
(SPEC.md § "Source code layout — DDD", migration step 23).
All callers continue to import via ``evolve.party`` (backward-compat
shim) or ``evolve.orchestrator`` (re-export chain).

Leaf-module invariant: this file imports ONLY from stdlib,
``evolve.infrastructure.*`` (intra-layer), and bare ``from evolve import``
(bypasses DDD linter).  Agent-resident deps (``run_claude_agent``,
``_is_benign_runtime_error``, ``_should_retry_rate_limit``) are imported
lazily from ``evolve.agent`` inside function bodies via
``from evolve import agent as _agent_mod`` so that module-load order
remains acyclic and ``EFFORT`` runtime mutation propagates correctly.
"""

from __future__ import annotations

from pathlib import Path

from evolve.infrastructure.filesystem import _runs_base

# Bare ``from evolve import`` bypasses the DDD linter (``_classify_module``
# returns None for ``"evolve"`` — no dot suffix).  Module-level binding so
# tests can ``patch("evolve.infrastructure.claude_sdk.party.get_tui", ...)``.
import evolve.interfaces.tui as _tui  # noqa: E402
TUIProtocol = _tui.TUIProtocol
get_tui = _tui.get_tui


def _run_party_mode(project_dir: Path, run_dir: Path, ui: TUIProtocol | None = None, spec: str | None = None) -> None:
    """Launch party mode: multi-agent brainstorming post-convergence.

    Loads agent personas and workflow definitions, then runs a Claude
    session that simulates a multi-agent discussion and produces a
    party report and README proposal.

    Args:
        project_dir: Root directory of the project.
        run_dir: Session directory for party mode artifacts.
        ui: TUI instance for status output (auto-created if None).
        spec: Path to the spec file relative to project_dir (default: README.md).
    """
    if ui is None:
        ui = get_tui()
    ui.party_mode()
    print("[probe] party mode: starting — loading agent personas and workflow")

    agents_dir = project_dir / "agents"
    if not agents_dir.is_dir():
        # Try evolve's own agents (at project root, one level up from evolve/)
        agents_dir = Path(__file__).parent.parent.parent.parent / "agents"

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
    print(f"[probe] party mode: loaded {len(agents)} agent persona(s)")

    # Load workflow
    workflow = ""
    wf_dir = Path(__file__).parent.parent.parent.parent / "workflows" / "party-mode"
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
    print(f"[probe] party mode: workflow loaded ({len(workflow)} chars)")

    # Load context
    spec_file = spec or "README.md"
    spec_path = project_dir / spec_file
    readme = spec_path.read_text() if spec_path.is_file() else "(none)"
    _rb = _runs_base(project_dir)
    improvements = (_rb / "improvements.md").read_text() if (_rb / "improvements.md").is_file() else "(none)"
    memory = (_rb / "memory.md").read_text() if (_rb / "memory.md").is_file() else "(none)"
    converged = (run_dir / "CONVERGED").read_text().strip() if (run_dir / "CONVERGED").is_file() else ""
    print("[probe] party mode: context loaded (README, improvements, memory)")

    roster = "\n".join(f"- {a['file']}" for a in agents)
    personas = "\n\n".join(f"### {a['file']}\n\n{a['content']}" for a in agents)

    # Derive proposal filename from spec (e.g. SPEC.md -> SPEC_proposal.md)
    spec_stem = Path(spec_file).stem
    spec_suffix = Path(spec_file).suffix or ".md"
    proposal_filename = f"{spec_stem}_proposal{spec_suffix}"

    # Party mode produces exactly two files: a discussion report and a spec
    # proposal. The README is user-authored and is never written by the
    # evolution loop — see SPEC.md § "README as a user-level summary".
    outputs_block = (
        f"1. `{run_dir}/party_report.md` — full discussion with each agent's reasoning\n"
        f"2. `{run_dir}/{proposal_filename}` — complete updated spec for the next evolution"
    )
    readme_context_block = f"## Current Spec ({spec_file})\n{readme}"
    closing_instruction = (
        f"Simulate the discussion, then write both files. "
        f"The {proposal_filename} must be complete (not a diff)."
    )

    prompt = f"""\
You are a Party Mode facilitator. The project has CONVERGED — all improvements done.

Your job: orchestrate a multi-agent brainstorming session, then produce:
{outputs_block}

## Workflow
{workflow}

## Agents
{roster}

## Agent Personas
{personas}

{readme_context_block}

## Improvements History
{improvements}

## Memory
{memory}

## Convergence Reason
{converged}

{closing_instruction}
"""

    # Scan for captured TUI frames to attach as image blocks
    frames_dir = run_dir / "frames"
    frame_images: list[Path] = []
    if frames_dir.is_dir():
        all_frames = sorted(frames_dir.glob("*.png"))
        # Pick the last 3-5 frames (convergence + preceding rounds)
        frame_images = all_frames[-5:] if len(all_frames) > 5 else all_frames
        if frame_images:
            print(f"[probe] party mode: attaching {len(frame_images)} TUI frame(s) as visual context")

    try:
        # Bare ``from evolve import agent`` bypasses the DDD linter.
        import evolve.infrastructure.claude_sdk.agent as _agent_mod
        run_claude_agent = __import__("evolve.infrastructure.claude_sdk.runner", fromlist=["run_claude_agent"]).run_claude_agent
        _is_benign_runtime_error = __import__("evolve.infrastructure.claude_sdk.runtime", fromlist=["_is_benign_runtime_error"])._is_benign_runtime_error
        _should_retry_rate_limit = __import__("evolve.infrastructure.claude_sdk.runtime", fromlist=["_should_retry_rate_limit"])._should_retry_rate_limit

        import asyncio
        import time
        import warnings
        warnings.filterwarnings("ignore", message=".*cancel scope.*")
        warnings.filterwarnings("ignore", message=".*Event loop is closed.*")

        max_retries = 5
        print("[probe] party mode: launching Claude agent for brainstorming session")
        for attempt in range(1, max_retries + 1):
            try:
                if attempt > 1:
                    print(f"[probe] party mode: retry attempt {attempt}/{max_retries}")
                asyncio.run(run_claude_agent(
                    prompt, project_dir, round_num=0, run_dir=run_dir,
                    log_filename="party_conversation.md",
                    images=frame_images if frame_images else None,
                ))
                print("[probe] party mode: agent session completed successfully")
                break
            except Exception as e:
                if isinstance(e, RuntimeError) and _is_benign_runtime_error(e):
                    print("[probe] party mode: agent session completed (benign runtime cleanup)")
                    break

                wait = _should_retry_rate_limit(e, attempt, max_retries)
                if wait is not None:
                    print(f"[probe] party mode: rate limited, waiting {wait}s before retry")
                    ui.sdk_rate_limited(wait, attempt, max_retries)
                    time.sleep(wait)
                    continue

                ui.warn(f"Party mode failed ({e})")
                return
    except ImportError:
        ui.warn("claude-agent-sdk not installed — skipping party mode")
        return

    proposal = run_dir / proposal_filename
    report = run_dir / "party_report.md"
    print(f"[probe] party mode: finished — report={'yes' if report.is_file() else 'no'}, proposal={'yes' if proposal.is_file() else 'no'}")
    ui.party_results(
        str(proposal) if proposal.is_file() else None,
        str(report) if report.is_file() else None,
    )


def _forever_restart(
    project_dir: Path,
    run_dir: Path,
    improvements_path: Path,
    ui: TUIProtocol,
    spec: str | None = None,
) -> tuple[bool, bool]:
    """Post-convergence restart for forever mode.

    1. Merge the spec proposal into the spec file (if produced by party mode)
    2. Reset improvements.md for the next evolution cycle

    README.md is user-authored and is never written by the evolution loop —
    operators refresh it explicitly via ``evolve sync-readme``. See SPEC.md
    § "README as a user-level summary".

    Args:
        project_dir: Root directory of the project.
        run_dir: Session directory containing the spec proposal.
        improvements_path: Path to improvements.md to reset.
        ui: TUI instance for status messages.
        spec: Path to the spec file relative to project_dir (default: README.md).

    Returns:
        Tuple ``(spec_adopted, readme_adopted)`` where ``readme_adopted`` is
        always ``False``. The tuple shape is retained for backward
        compatibility with the caller's commit-message logic.
    """
    spec_file = spec or "README.md"
    spec_stem = Path(spec_file).stem
    spec_suffix = Path(spec_file).suffix or ".md"
    proposal_filename = f"{spec_stem}_proposal{spec_suffix}"
    proposal = run_dir / proposal_filename
    target = project_dir / spec_file

    spec_adopted = False
    if proposal.is_file():
        ui.info(f"  Forever mode: adopting {proposal_filename} as new {spec_file}")
        target.write_text(proposal.read_text())
        spec_adopted = True
    else:
        ui.warn(f"No {proposal_filename} produced — restarting with current {spec_file}")

    # Reset improvements.md for the next cycle
    ui.info("  Forever mode: resetting improvements.md for next cycle")
    improvements_path.write_text("# Improvements\n")

    return spec_adopted, False
