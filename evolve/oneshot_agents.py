"""One-shot agents — dry-run, validate, diff, sync-readme.

SPEC § "The --dry-run flag", "The --validate flag", "evolve diff",
"evolve sync-readme".  Extracted from ``evolve/agent.py`` (US-033);
``diff`` and ``sync-readme`` further extracted to dedicated leaf
modules (US-034 / US-047).  Public symbols are re-exported from
``evolve.agent`` for backward compatibility.

Leaf-module invariant: imports ONLY from stdlib, ``evolve.state``,
and ``evolve.agent_runtime`` at module top.  ``get_tui``,
``_load_project_context``, ``_patch_sdk_parser``, ``EFFORT``, and
``_run_agent_with_retries`` are imported lazily inside function
bodies so ``patch("evolve.agent.X")`` test interception keeps
working and module-load order remains acyclic (memory.md round-7
lesson: indented imports don't trip the leaf-invariant regex
``^from evolve\\.``; the extracted function must look X up via
``evolve.agent``, not the original source module).
"""

from __future__ import annotations

from pathlib import Path

from evolve.agent_runtime import MAX_TURNS, MODEL
from evolve.state import _runs_base


# ---------------------------------------------------------------------------
# shared check-section helper used by validate / dry-run / diff prompt builders
# ---------------------------------------------------------------------------


def _build_check_section(check_cmd: str | None, check_output: str) -> str:
    """Build the check command section used by read-only prompt builders.

    Shared by :func:`build_validate_prompt` and :func:`build_dry_run_prompt`
    to eliminate duplicated conditional logic for rendering check command
    output.

    Args:
        check_cmd: Shell command used to verify the project (e.g. 'pytest').
        check_output: Output from the most recent check command run.

    Returns:
        A Markdown section string (may be empty if no check command).
    """
    if check_cmd and check_output:
        return (
            f"\n## Check command: `{check_cmd}`\n"
            f"\n### Latest check output:\n```\n{check_output}\n```\n"
        )
    elif check_cmd:
        return f"\n## Check command: `{check_cmd}` (not yet run)\n"
    return ""


# ---------------------------------------------------------------------------
# evolve --validate — spec compliance verification
# ---------------------------------------------------------------------------


def build_validate_prompt(
    project_dir: Path,
    check_output: str = "",
    check_cmd: str | None = None,
    run_dir: Path | None = None,
    spec: str | None = None,
) -> str:
    """Build the prompt for validation (spec compliance) mode.

    The agent is instructed to check every README claim against the codebase
    and produce a ``validate_report.md`` with pass/fail per claim and an
    overall compliance percentage.

    Args:
        project_dir: Root directory of the project being validated.
        check_output: Output from the most recent check command run.
        check_cmd: Shell command used to verify the project.
        run_dir: Session run directory where the report will be written.
        spec: Path to the spec file relative to project_dir (default: README.md).

    Returns:
        The fully assembled prompt string.
    """
    # Lazy import preserves ``patch("evolve.agent._load_project_context")``
    # test interception and avoids an import-time cycle (agent.py
    # re-exports this module's public names).
    from evolve.agent import _load_project_context

    ctx = _load_project_context(project_dir, spec=spec)
    readme = ctx["readme"]
    improvements = ctx["improvements"] or "(none)"

    rdir = str(run_dir or ".evolve/runs")

    check_section = _build_check_section(check_cmd, check_output)

    return f"""\
You are a spec compliance validation agent. You are running in VALIDATE mode.
You MUST NOT modify any project files. Your only writable action is to
create `{rdir}/validate_report.md`.

Your task: systematically verify every claim in the README specification
against the actual codebase. For each claim, determine if it is implemented
and functional.

Use Read, Grep, and Glob tools to examine the codebase. Do NOT use Edit, Write, or Bash.

At the end, write `{rdir}/validate_report.md` with the following format:

# Validation Report

## Claims

For EACH distinct claim, feature, or requirement in the README, write one line:
- ✅ **Claim description** — verified in `file.py` (brief evidence)
- ❌ **Claim description** — not implemented / broken (brief explanation)

## Summary

- **Total claims**: N
- **Passed**: N (✅)
- **Failed**: N (❌)
- **Compliance**: XX%

## Gaps

For each ❌ item, describe what is missing with file references.

IMPORTANT: Be thorough. Check every section of the README. A claim passes
only if you can find concrete evidence in the code. Do not assume — verify.

## README (specification)
{readme if readme else "(no README found)"}

## Current improvements.md
{improvements}
{check_section}"""


# ---------------------------------------------------------------------------
# evolve --dry-run — read-only analysis + improvement scoping
# ---------------------------------------------------------------------------


def build_dry_run_prompt(
    project_dir: Path,
    check_output: str = "",
    check_cmd: str | None = None,
    run_dir: Path | None = None,
    spec: str | None = None,
) -> str:
    """Build the prompt for dry-run (read-only) analysis mode.

    The agent is instructed to analyse the project without modifying any
    files and to write a ``dry_run_report.md`` summarising identified gaps,
    proposed improvements, and estimated rounds to convergence.

    Args:
        project_dir: Root directory of the project being analysed.
        check_output: Output from the most recent check command run.
        check_cmd: Shell command used to verify the project.
        run_dir: Session run directory where the report will be written.
        spec: Path to the spec file relative to project_dir (default: README.md).

    Returns:
        The fully assembled prompt string.
    """
    from evolve.agent import _load_project_context

    ctx = _load_project_context(project_dir, spec=spec)
    readme = ctx["readme"]
    improvements = ctx["improvements"] or "(none)"

    rdir = str(run_dir or ".evolve/runs")

    check_section = _build_check_section(check_cmd, check_output)

    return f"""\
You are a read-only analysis agent. You are running in DRY RUN mode.
You MUST NOT modify any project files. Your only writable action is to
create `{rdir}/dry_run_report.md`.

Analyse the project against its README specification. Use Read, Grep, and
Glob tools to examine the codebase. Do NOT use Edit, Write, or Bash.

At the end, write `{rdir}/dry_run_report.md` with the following sections:

# Dry Run Report

## Identified Gaps
List every gap between the README specification and the current implementation.

## Proposed Improvements
For each gap, describe what improvement would be added to `improvements.md`.
Use the same format: `- [ ] [functional] description` or `- [ ] [performance] description`.

## Estimated Rounds
Estimate how many evolution rounds would be needed to reach convergence.

## README (specification)
{readme if readme else "(no README found)"}

## Current improvements.md
{improvements}
{check_section}"""


# ---------------------------------------------------------------------------
# Shared SDK runner for read-only one-shot agents
# ---------------------------------------------------------------------------


async def _run_readonly_claude_agent(
    prompt: str,
    project_dir: Path,
    run_dir: Path,
    *,
    log_filename: str,
    log_header: str,
    disallowed_tools: list[str] | None = None,
) -> None:
    """Shared helper for running the Claude agent in read-only modes.

    Handles SDK streaming, message deduplication, tool-call logging, and TUI
    updates.  Used by both dry-run and validate modes.

    Args:
        prompt: The assembled prompt for the agent.
        project_dir: Root directory of the project (used as cwd).
        run_dir: Session directory for the conversation log and report.
        log_filename: Name of the conversation log file (e.g. ``dry_run_conversation.md``).
        log_header: Markdown header written at the top of the log file.
        disallowed_tools: Tools to block.  Defaults to read-only set
            (Edit, Bash, Task, Agent, WebSearch, WebFetch).
    """
    # Lazy import preserves ``patch("evolve.agent.EFFORT")`` /
    # ``patch("evolve.agent._patch_sdk_parser")`` interception (memory.md
    # round-7 lesson: extracted runners look up these names via
    # ``evolve.agent``, NOT the leaf source module).  ``EFFORT`` is
    # mutated at runtime by ``_resolve_config``.
    from evolve.agent import EFFORT, _patch_sdk_parser, get_tui

    _patch_sdk_parser()
    from claude_agent_sdk import query, ClaudeAgentOptions, AssistantMessage, ResultMessage

    if disallowed_tools is None:
        disallowed_tools = ["Edit", "Bash", "Task", "Agent", "WebSearch", "WebFetch"]

    options = ClaudeAgentOptions(
        permission_mode="bypassPermissions",
        model=MODEL,
        max_turns=MAX_TURNS,
        cwd=str(project_dir),
        disallowed_tools=disallowed_tools,
        include_partial_messages=True,
        effort=EFFORT,
    )

    log_path = run_dir / log_filename
    ui = get_tui()

    with open(log_path, "w", buffering=1) as log:
        log.write(f"# {log_header}\n\n")

        seen_tool_ids: set[str] = set()
        seen_text_hashes: set[int] = set()
        tools_used = 0

        try:
            async for message in query(prompt=prompt, options=options):
                if message is None:
                    continue
                if isinstance(message, (AssistantMessage, ResultMessage)):
                    if not hasattr(message, "content") or not message.content:
                        continue
                    for block in message.content:
                        if hasattr(block, "text") and block.text.strip():
                            h = hash(block.text)
                            if h in seen_text_hashes:
                                continue
                            seen_text_hashes.add(h)
                            log.write(f"\n{block.text}\n")
                            ui.agent_text(block.text)
                        elif hasattr(block, "name"):
                            block_id = getattr(block, "id", None)
                            if block_id and block_id in seen_tool_ids:
                                continue
                            if block_id:
                                seen_tool_ids.add(block_id)
                            tools_used += 1
                            tool_name = block.name
                            tool_input = ""
                            if hasattr(block, "input") and block.input:
                                inp = block.input
                                if isinstance(inp, dict):
                                    tool_input = inp.get("file_path", inp.get("pattern", str(inp)[:100]))
                                else:
                                    tool_input = str(inp)[:100]
                            log.write(f"\n**{tool_name}**: `{tool_input}`\n")
                            ui.agent_tool(tool_name, tool_input)
        except Exception as e:
            log.write(f"\n> SDK error: {e}\n")

        log.write(f"\n---\n\n**Done**: {tools_used} tool calls\n")

    ui.agent_done(tools_used, str(log_path))


# ---------------------------------------------------------------------------
# Dry-run public surface
# ---------------------------------------------------------------------------


async def _run_dry_run_claude_agent(
    prompt: str,
    project_dir: Path,
    run_dir: Path,
) -> None:
    """Run the Claude agent in dry-run mode with restricted tools.

    Thin wrapper around :func:`_run_readonly_claude_agent` for backward
    compatibility.

    Args:
        prompt: The dry-run analysis prompt.
        project_dir: Root directory of the project (used as cwd).
        run_dir: Session directory for the conversation log and report.
    """
    await _run_readonly_claude_agent(
        prompt, project_dir, run_dir,
        log_filename="dry_run_conversation.md",
        log_header="Dry Run Analysis",
    )


def run_dry_run_agent(
    project_dir: Path,
    check_output: str = "",
    check_cmd: str | None = None,
    run_dir: Path | None = None,
    max_retries: int = 5,
    spec: str | None = None,
) -> None:
    """Run the agent in dry-run (read-only) analysis mode.

    Builds a dry-run prompt and invokes the agent with write-related tools
    disabled.  Includes the same retry logic as ``analyze_and_fix``.

    Args:
        project_dir: Root directory of the project being analysed.
        check_output: Output from the most recent check command.
        check_cmd: Shell command used to verify the project.
        run_dir: Session run directory for conversation logs and report.
        max_retries: Maximum SDK call attempts on rate-limit errors.
        spec: Path to the spec file relative to project_dir (default: README.md).
    """
    # Lazy import — see module docstring re: ``patch("evolve.agent.
    # _run_agent_with_retries")`` test interception + cycle avoidance.
    from evolve.agent import _run_agent_with_retries

    rdir = run_dir or _runs_base(project_dir)
    rdir.mkdir(parents=True, exist_ok=True)

    prompt = build_dry_run_prompt(project_dir, check_output, check_cmd, rdir, spec=spec)

    _run_agent_with_retries(
        lambda: _run_dry_run_claude_agent(prompt, project_dir, rdir),
        fail_label="Dry-run agent",
        max_retries=max_retries,
    )


# ---------------------------------------------------------------------------
# Validate public surface
# ---------------------------------------------------------------------------


async def _run_validate_claude_agent(
    prompt: str,
    project_dir: Path,
    run_dir: Path,
) -> None:
    """Run the Claude agent in validation mode with restricted tools.

    Thin wrapper around :func:`_run_readonly_claude_agent` for backward
    compatibility.

    Args:
        prompt: The validation prompt.
        project_dir: Root directory of the project (used as cwd).
        run_dir: Session directory for the conversation log and report.
    """
    await _run_readonly_claude_agent(
        prompt, project_dir, run_dir,
        log_filename="validate_conversation.md",
        log_header="Validation Analysis",
    )


def run_validate_agent(
    project_dir: Path,
    check_output: str = "",
    check_cmd: str | None = None,
    run_dir: Path | None = None,
    max_retries: int = 5,
    spec: str | None = None,
) -> None:
    """Run the agent in validation (spec compliance) mode.

    Builds a validation prompt and invokes the agent with write-related tools
    disabled.  Includes the same retry logic as ``analyze_and_fix``.

    Args:
        project_dir: Root directory of the project being validated.
        check_output: Output from the most recent check command.
        check_cmd: Shell command used to verify the project.
        run_dir: Session run directory for conversation logs and report.
        max_retries: Maximum SDK call attempts on rate-limit errors.
        spec: Path to the spec file relative to project_dir (default: README.md).
    """
    from evolve.agent import _run_agent_with_retries

    rdir = run_dir or _runs_base(project_dir)
    rdir.mkdir(parents=True, exist_ok=True)

    prompt = build_validate_prompt(project_dir, check_output, check_cmd, rdir, spec=spec)

    _run_agent_with_retries(
        lambda: _run_validate_claude_agent(prompt, project_dir, rdir),
        fail_label="Validate agent",
        max_retries=max_retries,
    )


# ---------------------------------------------------------------------------
# evolve diff — extracted to evolve/diff_agent.py (US-047, mirrors US-034
# sync_readme pattern).  Re-exported for ``patch("evolve.agent.X")`` /
# ``from evolve.agent import X`` compatibility through the chain
# ``agent`` → ``oneshot_agents`` → ``diff_agent``.
from evolve.diff_agent import (  # noqa: E402
    build_diff_prompt,
    _run_diff_claude_agent,
    run_diff_agent,
)


# ---------------------------------------------------------------------------
# evolve sync-readme — refresh README.md to reflect the current spec
# ---------------------------------------------------------------------------
#
# SPEC.md § "evolve sync-readme" — one-shot subcommand that refreshes
# README.md to reflect the current spec.  Implementation lives in the
# dedicated leaf module ``evolve/sync_readme.py`` (US-034 split, mirrors
# US-031/US-032/US-033/spec_archival pattern) so this file moves toward
# the SPEC § "Hard rule: source files MUST NOT exceed 500 lines" cap.
# Public symbols are re-exported below so existing test patch targets
# (``patch("evolve.agent.run_sync_readme_agent")``,
# ``from evolve.agent import SYNC_README_NO_CHANGES_SENTINEL``) and the
# canonical-import lock-in (``tests/test_oneshot_agents_module.py``)
# continue to work via the chain ``agent`` → ``oneshot_agents`` →
# ``sync_readme``.

from evolve.sync_readme import (  # noqa: E402  (intentional late import)
    SYNC_README_NO_CHANGES_SENTINEL,
    build_sync_readme_prompt,
    _run_sync_readme_claude_agent,
    run_sync_readme_agent,
)
