"""One-shot agents — dry-run, validate, diff, and sync-readme.

SPEC § "The --dry-run flag", "The --validate flag", "evolve diff",
"evolve sync-readme" — read-only / single-shot agent invocations
extracted from ``evolve/agent.py`` (US-033) to satisfy the SPEC §
"Hard rule: source files MUST NOT exceed 500 lines" cap.  Mirrors
the extraction pattern used by US-027 (diagnostics), US-030
(agent_runtime), US-031 (memory_curation), the round-6
spec_archival split, and US-032 (draft_review).

Public symbols are re-exported from ``evolve.agent`` for backward
compatibility with the existing test suite (``patch("evolve.agent.
run_dry_run_agent", ...)``, ``from evolve.agent import
_run_validate_claude_agent``, etc.) and with the orchestrator's
late-binding imports inside ``_run_rounds``-adjacent helpers.

Leaf-module invariant: this file imports ONLY from stdlib,
``evolve.tui``, ``evolve.state``, and ``evolve.agent_runtime`` at
module top.  Spec-fixed runtime constants (``MODEL`` /
``MAX_TURNS``) come from the leaf module ``evolve.agent_runtime`` —
no cycle.  The agent.py-resident dependencies
(``_load_project_context`` in build functions; ``_patch_sdk_parser``
/ ``EFFORT`` in the SDK runners; ``_run_agent_with_retries`` in
the public ``run_*_agent`` wrappers) are imported lazily inside
function bodies so that:

1. tests that ``patch("evolve.agent.X")`` continue to intercept
   (memory.md round-7 lesson: "the extracted function must look X
   up via ``evolve.agent``, NOT the original source module");
2. module load order remains acyclic;
3. indented imports do NOT trip the leaf-invariant regex
   ``^from evolve\\.`` (memory.md round-7 entry).

``EFFORT`` is mutated at runtime in ``evolve.agent`` by
``_resolve_config`` per memory.md "--effort plumbing: 3-attempt
pattern" — the function-local imports inside the SDK runners
honor that mutation while keeping the module-level import set
leaf-clean.
"""

from __future__ import annotations

from pathlib import Path

from evolve.agent_runtime import MAX_TURNS, MODEL
from evolve.state import _runs_base

# NOTE: ``get_tui`` is intentionally imported lazily inside each SDK runner
# below, NOT at module top.  Tests do ``patch("evolve.agent.get_tui",
# return_value=mock_tui)`` and rely on the runner looking the name up via
# ``evolve.agent`` (the re-export binding) — a top-level
# ``from evolve.tui import get_tui`` here would bypass that patch
# (memory.md round-7 lesson: "the extracted function must look X up via
# ``evolve.agent``, NOT the original source module").


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
# evolve diff — lightweight spec-vs-implementation gap detection
# ---------------------------------------------------------------------------
#
# SPEC § "evolve diff" — one-shot subcommand that shows the delta between
# the spec and the implementation.  Lighter-weight than --validate: uses
# --effort low, does not run the check command, checks for presence/absence
# of major features rather than exhaustive claim-by-claim verification.
# Exit codes: 0 = compliant, 1 = gaps found, 2 = error.


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

    Thin wrapper around :func:`_run_readonly_claude_agent`.

    Args:
        prompt: The diff prompt.
        project_dir: Root directory of the project (used as cwd).
        run_dir: Session directory for the conversation log and report.
    """
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
    from evolve.agent import _run_agent_with_retries

    rdir = run_dir or _runs_base(project_dir)
    rdir.mkdir(parents=True, exist_ok=True)

    prompt = build_diff_prompt(project_dir, rdir, spec=spec)

    _run_agent_with_retries(
        lambda: _run_diff_claude_agent(prompt, project_dir, rdir),
        fail_label="Diff agent",
        max_retries=max_retries,
    )


# ---------------------------------------------------------------------------
# evolve sync-readme — refresh README.md to reflect the current spec
# ---------------------------------------------------------------------------
#
# SPEC.md § "evolve sync-readme" — one-shot subcommand that refreshes
# README.md to reflect the current spec.  Never runs as part of the
# evolution loop.  Exit codes: 0 = proposal written / applied,
# 1 = README already in sync (agent writes the sentinel below), 2 = error.

# Sentinel file the agent writes inside ``run_dir`` when README is already
# in sync with the spec.  The orchestrator checks for this file to map the
# agent's "no changes needed" signal onto exit code 1.
SYNC_README_NO_CHANGES_SENTINEL = "NO_SYNC_NEEDED"


def build_sync_readme_prompt(
    project_dir: Path,
    run_dir: Path,
    spec: str | None = None,
    apply: bool = False,
) -> str:
    """Build the prompt for the ``evolve sync-readme`` one-shot subcommand.

    The agent is asked to refresh README.md so it reflects the current
    spec while preserving the README's tutorial voice.  The agent has
    exactly two valid outputs:

    - Write the new README content to ``output_path`` (project-root
      ``README_proposal.md`` in default mode, ``README.md`` in apply mode).
    - Write the sentinel file ``run_dir/NO_SYNC_NEEDED`` when the README
      is already in sync with the spec — this maps to exit code 1.

    Args:
        project_dir: Root directory of the project.
        run_dir: Session directory where the agent's conversation log and
            the optional ``NO_SYNC_NEEDED`` sentinel are written.
        spec: Path to the spec file relative to ``project_dir`` (defaults
            to ``README.md``).
        apply: When ``True``, the agent writes directly to ``README.md``;
            otherwise it writes to ``README_proposal.md``.

    Returns:
        The fully assembled prompt string.
    """
    from evolve.agent import _load_project_context

    ctx = _load_project_context(project_dir, spec=spec)
    spec_text = ctx["readme"]  # _load_project_context returns spec as 'readme'
    spec_name = spec or "README.md"

    readme_path = project_dir / "README.md"
    readme_text = readme_path.read_text() if readme_path.is_file() else ""

    output_name = "README.md" if apply else "README_proposal.md"
    output_path = project_dir / output_name
    sentinel_path = run_dir / SYNC_README_NO_CHANGES_SENTINEL

    return f"""\
You are the evolve sync-readme agent. Your single task: refresh
README.md so it reflects the current spec ({spec_name}), while
preserving the README's tutorial voice.

Voice constraints (MANDATORY):
- Brevity — README is a user-level summary, NOT an exhaustive copy of
  the spec. Keep examples short and link to {spec_name} for internals.
- Do NOT copy the spec verbatim. Synthesize.
- Do NOT invent features that aren't in the spec.
- Preserve the README's structure where it still fits the spec.

You have exactly two valid outputs:

1. If the README is already in sync with the spec (no user-visible
   drift), write the sentinel file:
       {sentinel_path}
   with any short rationale as its content. Do NOT write to
   {output_path}. The orchestrator will exit with code 1 ("README
   already in sync — no changes proposed").

2. Otherwise, write the new README content to:
       {output_path}
   Use the Write tool. Do NOT modify any other file in the project.
   The orchestrator will exit with code 0 ("proposal written" /
   "applied").

Mode: {"APPLY (writing directly to README.md, will be committed)" if apply else "PROPOSAL (writing to README_proposal.md for human review)"}

## Spec ({spec_name})

{spec_text if spec_text else "(spec file empty or missing)"}

## Current README.md

{readme_text if readme_text else "(no README.md found — write a fresh one based on the spec)"}
"""


async def _run_sync_readme_claude_agent(
    prompt: str,
    project_dir: Path,
    run_dir: Path,
) -> None:
    """Run the Claude agent for the ``sync-readme`` one-shot subcommand.

    Allows ``Write`` (the agent must produce ``README.md`` /
    ``README_proposal.md`` / ``NO_SYNC_NEEDED``) and ``Read`` / ``Grep``
    / ``Glob`` for context, but disallows ``Edit``, ``Bash``, ``Task``,
    ``Agent``, ``WebSearch``, and ``WebFetch`` so the agent cannot
    sprawl beyond its one-shot mandate.
    """
    from evolve.agent import EFFORT, _patch_sdk_parser, get_tui

    _patch_sdk_parser()
    from claude_agent_sdk import query, ClaudeAgentOptions, AssistantMessage, ResultMessage

    options = ClaudeAgentOptions(
        permission_mode="bypassPermissions",
        model=MODEL,
        max_turns=MAX_TURNS,
        cwd=str(project_dir),
        disallowed_tools=["Edit", "Bash", "Task", "Agent", "WebSearch", "WebFetch"],
        include_partial_messages=True,
        effort=EFFORT,
    )

    log_path = run_dir / "sync_readme_conversation.md"
    ui = get_tui()

    with open(log_path, "w", buffering=1) as log:
        log.write("# Sync README\n\n")

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


def run_sync_readme_agent(
    project_dir: Path,
    run_dir: Path,
    spec: str | None = None,
    apply: bool = False,
    max_retries: int = 5,
) -> None:
    """Run the agent for the ``evolve sync-readme`` one-shot subcommand.

    Builds the sync-readme prompt and invokes the agent with the same
    retry shell as the other one-shot agents.  The orchestrator
    (``loop.run_sync_readme``) interprets the resulting filesystem
    state to compute the exit code.

    Args:
        project_dir: Root directory of the project.
        run_dir: Session directory for conversation log and sentinel.
        spec: Path to the spec file relative to project_dir.
        apply: When True, agent writes directly to README.md.
        max_retries: Maximum SDK call attempts on rate-limit errors.
    """
    from evolve.agent import _run_agent_with_retries

    run_dir.mkdir(parents=True, exist_ok=True)

    prompt = build_sync_readme_prompt(project_dir, run_dir, spec=spec, apply=apply)

    _run_agent_with_retries(
        lambda: _run_sync_readme_claude_agent(
            prompt, project_dir, run_dir,
        ),
        fail_label="Sync-readme agent",
        max_retries=max_retries,
    )
