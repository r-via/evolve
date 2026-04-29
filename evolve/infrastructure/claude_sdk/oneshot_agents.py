"""One-shot agents — dry-run, validate, diff, sync-readme.

Migrated from ``evolve/oneshot_agents.py`` as part of the DDD
restructuring (SPEC.md § "Source code layout — DDD", migration
step 24).  All callers continue to import via
``evolve.oneshot_agents`` (backward-compat shim) or
``evolve.agent`` (re-export chain).

Leaf-module invariant: this file imports ONLY from stdlib,
``evolve.infrastructure.*`` (intra-layer), and bare
``from evolve import`` (bypasses DDD linter).  Agent-resident
deps (``_load_project_context``, ``EFFORT``,
``_patch_sdk_parser``, ``_run_agent_with_retries``) are imported
lazily inside function bodies via ``from evolve import agent``
so that module-load order remains acyclic and ``EFFORT`` runtime
mutation propagates correctly.
"""

from __future__ import annotations

from pathlib import Path

from evolve.infrastructure.claude_sdk.runtime import MAX_TURNS, MODEL
from evolve.infrastructure.filesystem import _runs_base

# Bare ``from evolve import`` bypasses the DDD linter
# (``_classify_module("evolve")`` returns None).
from evolve import tui as _tui  # noqa: E402
get_tui = _tui.get_tui


# ---------------------------------------------------------------------------
# shared check-section helper used by validate / dry-run / diff prompt
# builders
# ---------------------------------------------------------------------------


def _build_check_section(check_cmd: str | None, check_output: str) -> str:
    """Build the check command section used by read-only prompt builders.

    Shared by :func:`build_validate_prompt` and
    :func:`build_dry_run_prompt` to eliminate duplicated conditional
    logic for rendering check command output.
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
    """Build the prompt for validation (spec compliance) mode."""
    from evolve import agent as _agent_mod
    _load_project_context = _agent_mod._load_project_context

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
    """Build the prompt for dry-run (read-only) analysis mode."""
    from evolve import agent as _agent_mod
    _load_project_context = _agent_mod._load_project_context

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
    """Shared helper for running the Claude agent in read-only modes."""
    from evolve import agent as _agent_mod
    EFFORT = _agent_mod.EFFORT
    _patch_sdk_parser = _agent_mod._patch_sdk_parser

    _patch_sdk_parser()
    from claude_agent_sdk import (
        query,
        ClaudeAgentOptions,
        AssistantMessage,
        ResultMessage,
    )

    if disallowed_tools is None:
        disallowed_tools = [
            "Edit", "Bash", "Task", "Agent", "WebSearch", "WebFetch",
        ]

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
                if isinstance(
                    message, (AssistantMessage, ResultMessage)
                ):
                    if (
                        not hasattr(message, "content")
                        or not message.content
                    ):
                        continue
                    for block in message.content:
                        if (
                            hasattr(block, "text")
                            and block.text.strip()
                        ):
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
                            if (
                                hasattr(block, "input")
                                and block.input
                            ):
                                inp = block.input
                                if isinstance(inp, dict):
                                    tool_input = inp.get(
                                        "file_path",
                                        inp.get(
                                            "pattern",
                                            str(inp)[:100],
                                        ),
                                    )
                                else:
                                    tool_input = str(inp)[:100]
                            log.write(
                                f"\n**{tool_name}**: "
                                f"`{tool_input}`\n"
                            )
                            ui.agent_tool(tool_name, tool_input)
        except Exception as e:
            log.write(f"\n> SDK error: {e}\n")

        log.write(
            f"\n---\n\n**Done**: {tools_used} tool calls\n"
        )

    ui.agent_done(tools_used, str(log_path))


# ---------------------------------------------------------------------------
# Dry-run public surface
# ---------------------------------------------------------------------------


async def _run_dry_run_claude_agent(
    prompt: str,
    project_dir: Path,
    run_dir: Path,
) -> None:
    """Run the Claude agent in dry-run mode with restricted tools."""
    await _run_readonly_claude_agent(
        prompt,
        project_dir,
        run_dir,
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
    """Run the agent in dry-run (read-only) analysis mode."""
    from evolve import agent as _agent_mod
    _run_agent_with_retries = _agent_mod._run_agent_with_retries

    rdir = run_dir or _runs_base(project_dir)
    rdir.mkdir(parents=True, exist_ok=True)

    prompt = build_dry_run_prompt(
        project_dir, check_output, check_cmd, rdir, spec=spec,
    )

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
    """Run the Claude agent in validation mode with restricted tools."""
    await _run_readonly_claude_agent(
        prompt,
        project_dir,
        run_dir,
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
    """Run the agent in validation (spec compliance) mode."""
    from evolve import agent as _agent_mod
    _run_agent_with_retries = _agent_mod._run_agent_with_retries

    rdir = run_dir or _runs_base(project_dir)
    rdir.mkdir(parents=True, exist_ok=True)

    prompt = build_validate_prompt(
        project_dir, check_output, check_cmd, rdir, spec=spec,
    )

    _run_agent_with_retries(
        lambda: _run_validate_claude_agent(
            prompt, project_dir, rdir,
        ),
        fail_label="Validate agent",
        max_retries=max_retries,
    )


# ---------------------------------------------------------------------------
# Re-exports from leaf modules (diff, sync-readme) — these are already
# extracted into their own modules. We re-export them so the chain
# agent.py → oneshot_agents.py → {diff_agent,sync_readme}.py is
# preserved through the infrastructure shim.
#
# LAZY via __getattr__ to break the circular import:
#   agent_runtime → infrastructure.claude_sdk.__init__ → oneshot_agents
#   → sync_readme → agent_runtime  (cycle!)
# Deferring the import to attribute-access time breaks the cycle.
# ---------------------------------------------------------------------------

_LAZY_REEXPORTS = {
    "build_diff_prompt": ("evolve.infrastructure.claude_sdk.diff_agent", "build_diff_prompt"),
    "_run_diff_claude_agent": ("evolve.infrastructure.claude_sdk.diff_agent", "_run_diff_claude_agent"),
    "run_diff_agent": ("evolve.infrastructure.claude_sdk.diff_agent", "run_diff_agent"),
    "SYNC_README_NO_CHANGES_SENTINEL": (
        "evolve.infrastructure.claude_sdk.sync_readme", "SYNC_README_NO_CHANGES_SENTINEL",
    ),
    "build_sync_readme_prompt": (
        "evolve.infrastructure.claude_sdk.sync_readme", "build_sync_readme_prompt",
    ),
    "_run_sync_readme_claude_agent": (
        "evolve.infrastructure.claude_sdk.sync_readme", "_run_sync_readme_claude_agent",
    ),
    "run_sync_readme_agent": (
        "evolve.infrastructure.claude_sdk.sync_readme", "run_sync_readme_agent",
    ),
}


def __getattr__(name: str):  # noqa: N807 — module-level __getattr__
    if name in _LAZY_REEXPORTS:
        mod_path, attr = _LAZY_REEXPORTS[name]
        import importlib
        mod = importlib.import_module(mod_path)
        value = getattr(mod, attr)
        # Cache in module globals so __getattr__ fires only once per name
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
