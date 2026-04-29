"""Diff one-shot agent — lightweight spec-vs-implementation gap detection.

SPEC § "evolve diff" — one-shot subcommand that shows the delta
between the current spec and the implementation.  Lighter-weight
than ``--validate``: uses ``--effort low``, does not run the check
command, checks for presence/absence of major features rather than
exhaustive claim-by-claim verification.  Exit codes:
0 = compliant, 1 = gaps found, 2 = error.

Extracted from ``evolve/oneshot_agents.py`` (US-047) to satisfy the
SPEC § "Hard rule: source files MUST NOT exceed 500 lines" cap.
Migrated from ``evolve/diff_agent.py`` to
``evolve/infrastructure/claude_sdk/diff_agent.py`` (US-081) as part
of the DDD migration program.

Public symbols (``build_diff_prompt``, ``_run_diff_claude_agent``,
``run_diff_agent``) are re-exported through the shim chain:
``evolve.agent`` → ``evolve.oneshot_agents`` →
``evolve.diff_agent`` → this module.

Leaf-module invariant: this file imports ONLY from stdlib and
``evolve.agent_runtime`` at module top.  ``grep -E
"^from evolve\\.(agent|orchestrator|cli|oneshot_agents)( |$|\\.)"
evolve/infrastructure/claude_sdk/diff_agent.py`` returns zero
matches.  Agent.py-resident dependencies
(``_load_project_context``, ``_run_agent_with_retries``) and
``evolve.oneshot_agents``-resident dependencies
(``_run_readonly_claude_agent``) are imported lazily inside function
bodies.

``_runs_base`` is imported from ``evolve.state`` — a pure leaf
module, no cycle risk.
"""

from __future__ import annotations

from pathlib import Path

from evolve.state import _runs_base


# ---------------------------------------------------------------------------
# evolve diff — lightweight spec-vs-implementation gap detection
# ---------------------------------------------------------------------------


def build_diff_prompt(
    project_dir: Path,
    run_dir: Path | None = None,
    spec: str | None = None,
) -> str:
    """Build the prompt for the ``evolve diff`` one-shot subcommand.

    The agent scans the spec for major features/architectural claims and
    checks whether each is present in the codebase.  Produces a
    ``diff_report.md`` with per-section compliance and overall percentage.

    Args:
        project_dir: Root directory of the project.
        run_dir: Session directory where the report will be written.
        spec: Path to the spec file relative to project_dir (default: README.md).

    Returns:
        The fully assembled prompt string.
    """
    # Lazy import preserves ``patch("evolve.agent._load_project_context")``
    # test interception and avoids an import-time cycle (agent.py
    # re-exports oneshot_agents which re-exports this module).
    from evolve.agent import _load_project_context

    ctx = _load_project_context(project_dir, spec=spec)
    readme = ctx["readme"]
    improvements = ctx["improvements"] or "(none)"

    rdir = str(run_dir or ".evolve/runs")

    return f"""\
You are a lightweight spec compliance agent. You are running in DIFF mode.
You MUST NOT modify any project files. Your only writable action is to
create `{rdir}/diff_report.md`.

Your task: scan the spec for major features and architectural claims. For
each one, check whether it is present in the codebase. Report gaps — do
NOT verify exhaustively (that is what --validate is for). Focus on
presence/absence of major capabilities, not line-by-line correctness.

Use Read, Grep, and Glob tools to examine the codebase. Do NOT use Edit, Write, or Bash.

At the end, write `{rdir}/diff_report.md` with the following format:

# Diff Report

## Sections

For EACH major section or feature area in the spec, write one line:
- ✅ **Section/feature name** — present (brief evidence)
- ❌ **Section/feature name** — missing (brief description of gap)

## Summary

- **Total sections**: N
- **Present**: N (✅)
- **Missing**: N (❌)
- **Compliance**: XX%

## Gaps

For each ❌ item, briefly describe what is missing.

IMPORTANT: Keep it concise. This is a quick gap-detection pass, not an
exhaustive audit. Check for the presence of major features, modules, CLI
flags, and architectural patterns described in the spec.

## Spec (specification)
{readme if readme else "(no spec found)"}

## Current improvements.md
{improvements}"""


async def _run_diff_claude_agent(
    prompt: str,
    project_dir: Path,
    run_dir: Path,
) -> None:
    """Run the Claude agent in diff mode with restricted tools.

    Thin wrapper around :func:`evolve.oneshot_agents._run_readonly_claude_agent`.

    Args:
        prompt: The diff prompt.
        project_dir: Root directory of the project (used as cwd).
        run_dir: Session directory for the conversation log and report.
    """
    # Lazy import: ``_run_readonly_claude_agent`` is shared with dry-run +
    # validate and stays in ``evolve.oneshot_agents``.  Importing it via
    # ``evolve.oneshot_agents`` (NOT a top-level import) preserves the
    # leaf invariant and keeps the patch surface intact.
    from evolve.oneshot_agents import _run_readonly_claude_agent

    await _run_readonly_claude_agent(
        prompt, project_dir, run_dir,
        log_filename="diff_conversation.md",
        log_header="Diff Analysis",
    )


def run_diff_agent(
    project_dir: Path,
    run_dir: Path | None = None,
    max_retries: int = 5,
    spec: str | None = None,
) -> None:
    """Run the agent for the ``evolve diff`` one-shot subcommand.

    Builds a diff prompt and invokes the agent with write-related tools
    disabled.  Includes the same retry logic as the other agents.

    Args:
        project_dir: Root directory of the project.
        run_dir: Session run directory for conversation logs and report.
        max_retries: Maximum SDK call attempts on rate-limit errors.
        spec: Path to the spec file relative to project_dir (default: README.md).
    """
    # Lazy import — see module docstring re: ``patch("evolve.agent.
    # _run_agent_with_retries")`` test interception + cycle avoidance.
    from evolve.agent import _run_agent_with_retries

    rdir = run_dir or _runs_base(project_dir)
    rdir.mkdir(parents=True, exist_ok=True)

    prompt = build_diff_prompt(project_dir, rdir, spec=spec)

    _run_agent_with_retries(
        lambda: _run_diff_claude_agent(prompt, project_dir, rdir),
        fail_label="Diff agent",
        max_retries=max_retries,
    )
