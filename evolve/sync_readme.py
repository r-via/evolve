"""Sync-readme one-shot agent — refreshes README.md to reflect the current spec.

SPEC § "evolve sync-readme" — never runs as part of the evolution
loop, always invoked explicitly by the operator.  Exit codes:
0 = proposal written / applied, 1 = README already in sync (sentinel
file ``NO_SYNC_NEEDED`` written by the agent), 2 = error.

Extracted from ``evolve/oneshot_agents.py`` (US-034) to satisfy the
SPEC § "Hard rule: source files MUST NOT exceed 500 lines" cap.
Mirrors the extraction pattern of US-027 (diagnostics), US-030
(agent_runtime), US-031 (memory_curation), the round-6 spec_archival
split, US-032 (draft_review), and US-033 (oneshot_agents).

Public symbols (``build_sync_readme_prompt``,
``_run_sync_readme_claude_agent``, ``run_sync_readme_agent``,
``SYNC_README_NO_CHANGES_SENTINEL``) are re-exported from
``evolve.oneshot_agents`` and ``evolve.agent`` for backward
compatibility — existing tests patch ``evolve.agent.X``, the
orchestrator imports from ``evolve.agent``, and
``tests/test_oneshot_agents_module.py`` asserts each name is also
bound under ``evolve.oneshot_agents`` (the re-export chain keeps
both modules pointing at the SAME object — ``is``-identical).

Leaf-module invariant: this file imports ONLY from stdlib and
``evolve.agent_runtime`` at module top.  ``grep -E
"^from evolve\\.(agent|orchestrator|cli|oneshot_agents)( |$|\\.)"
evolve/sync_readme.py`` returns zero matches.  Agent.py-resident
dependencies (``_load_project_context``, ``_patch_sdk_parser``,
``EFFORT``, ``get_tui``, ``_run_agent_with_retries``,
``build_sync_readme_prompt``) are imported lazily inside function
bodies so:

1. tests that ``patch("evolve.agent.X")`` continue to intercept
   (memory.md round-7 lesson + round-2-of-20260427_200209 entry:
   "the extracted function must look X up via ``evolve.agent``,
   NOT the original source module");
2. ``EFFORT`` runtime mutation by ``_resolve_config`` keeps
   propagating into the SDK options;
3. module-load order remains acyclic (memory.md round-7 entry:
   indented imports do NOT trip the leaf-invariant regex
   ``^from evolve\\.``).
"""

from __future__ import annotations

from pathlib import Path

from evolve.agent_runtime import MAX_TURNS, MODEL


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
    # Lazy import preserves ``patch("evolve.agent._load_project_context")``
    # test interception and avoids an import-time cycle (agent.py
    # re-exports this module's public names).
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
    # Lazy import via ``evolve.agent`` so ``patch("evolve.agent.X")`` in tests
    # intercepts the call (memory.md round-7 lesson — re-export ≠ patch
    # surface unless internal call sites bind the re-exported name).
    from evolve.agent import (
        _run_agent_with_retries,
        build_sync_readme_prompt as _build_sync_readme_prompt,
    )

    run_dir.mkdir(parents=True, exist_ok=True)

    prompt = _build_sync_readme_prompt(project_dir, run_dir, spec=spec, apply=apply)

    _run_agent_with_retries(
        lambda: _run_sync_readme_claude_agent(
            prompt, project_dir, run_dir,
        ),
        fail_label="Sync-readme agent",
        max_retries=max_retries,
    )
