"""Draft + review agents — Winston/John (drafting) and Zara (reviewing).

SPEC § "Multi-call round architecture" — the drafting and review calls.
Extracted from ``evolve/agent.py`` (US-032) to satisfy the SPEC § "Hard
rule: source files MUST NOT exceed 500 lines" cap.  Mirrors the
extraction pattern used by US-027 (diagnostics), US-030 (agent_runtime),
US-031 (memory_curation), and the round-6 spec_archival split.

Public symbols are re-exported from ``evolve.agent`` for backward
compatibility with the existing test suite (``patch("evolve.agent.
run_draft_agent", ...)``, ``monkeypatch.setattr(agent_mod, "run_review_agent",
...)``) and with the orchestrator's late-binding import
(``from evolve.agent import run_draft_agent`` inside ``_run_rounds``).

Leaf-module invariant: this file imports ONLY from stdlib, ``evolve.tui``,
``evolve.state``, and ``evolve.agent_runtime`` at module top.  Spec-fixed
runtime constants (``MODEL`` / ``MAX_TURNS`` / ``DRAFT_EFFORT`` /
``REVIEW_EFFORT``) come from the leaf module ``evolve.agent_runtime`` —
no cycle.  The agent.py-resident dependencies (``_load_project_context``
in build functions, ``_patch_sdk_parser`` / ``_summarise_tool_input`` /
``_run_agent_with_retries`` in the SDK runners) are imported lazily
inside function bodies so that:

1. tests that ``patch("evolve.agent.X")`` continue to intercept
   (memory.md round-7 lesson: "the extracted function must look X up
   via ``evolve.agent``, NOT the original source module");
2. module load order remains acyclic;
3. indented imports do NOT trip the leaf-invariant regex
   ``^from evolve\\.`` (memory.md round-7 entry).

DRAFT_EFFORT / REVIEW_EFFORT are spec-fixed at ``"low"`` per SPEC §
"Multi-call round architecture" table — pinning is independent of the
session-wide ``EFFORT`` global, which the operator's ``--effort`` flag
overrides for the implement / dry-run / validate / sync-readme /
curation paths.
"""

from __future__ import annotations

from pathlib import Path

from evolve.agent_runtime import (
    DRAFT_EFFORT,
    MAX_TURNS,
    MODEL,
    REVIEW_EFFORT,
)
from evolve.state import _runs_base
from evolve.tui import get_tui


# ---------------------------------------------------------------------------
# draft_agent — Winston + John pipeline, one US per call
# ---------------------------------------------------------------------------
#
# SPEC § "Multi-call round architecture" — the drafting call.  Runs as a
# dedicated SDK session when the backlog is drained, produces exactly one
# new US item in improvements.md, and returns.  Uses the centralized
# ``MODEL`` (Opus) — see SPEC § "Single model: Opus everywhere" for the
# rationale.  ``effort=DRAFT_EFFORT`` (spec-fixed at ``"low"``) +
# ``max_turns=MAX_TURNS``.


def _build_draft_prompt(
    project_dir: Path,
    run_dir: Path,
    spec: str | None = None,
) -> str:
    """Build the system prompt for the draft agent.

    Loads ``prompts/draft.md``, substitutes placeholders
    (``{project_dir}``, ``{run_dir}``, ``{runs_base}``), and appends the
    project context (spec + current improvements.md + memory.md) so the
    agent has everything it needs to find the first unimplemented spec
    claim and draft a US for it.
    """
    # Lazy import — keeps the module-top leaf invariant AND lets tests
    # that ``patch("evolve.agent._load_project_context", ...)`` intercept.
    from evolve.agent import _load_project_context

    prompt_path = Path(__file__).resolve().parent.parent / "prompts" / "draft.md"
    template = prompt_path.read_text() if prompt_path.is_file() else ""
    rdir = str(run_dir)
    runs_base_str = str(_runs_base(project_dir))
    template = template.replace("{project_dir}", str(project_dir))
    template = template.replace("{run_dir}", rdir)
    template = template.replace("{runs_base}", runs_base_str)

    # Load project context for injection.
    ctx = _load_project_context(project_dir, spec=spec)
    readme = ctx["readme"] or "(no spec file found)"
    improvements = ctx["improvements"] or "(no improvements.md — cold start)"
    memory_path = _runs_base(project_dir) / "memory.md"
    memory = memory_path.read_text() if memory_path.is_file() else ""

    sections = [template, f"\n\n## Spec ({spec or 'README.md'})\n{readme}"]
    sections.append(f"\n\n## Current improvements.md\n{improvements}")
    if memory:
        sections.append(f"\n\n## memory.md\n{memory}")
    return "".join(sections)


async def _run_draft_claude_agent(
    prompt: str,
    project_dir: Path,
    run_dir: Path,
) -> None:
    """Spawn the draft agent as a dedicated SDK call.

    Opus (centralized ``MODEL``), ``effort=DRAFT_EFFORT`` (spec-fixed at
    ``"low"`` per SPEC § "Multi-call round architecture"), ``max_turns=MAX_TURNS``.
    Edit is allowed (needs to modify ``improvements.md``); Bash / Task /
    Agent / Web* are disallowed.
    """
    # Lazy import — keeps leaf invariant AND lets tests that
    # ``patch.object(agent_mod, "_patch_sdk_parser", ...)`` intercept.
    from evolve.agent import _patch_sdk_parser, _summarise_tool_input

    _patch_sdk_parser()
    from claude_agent_sdk import query, ClaudeAgentOptions, AssistantMessage, ResultMessage

    options = ClaudeAgentOptions(
        permission_mode="bypassPermissions",
        model=MODEL,
        max_turns=MAX_TURNS,
        cwd=str(project_dir),
        disallowed_tools=["Bash", "Task", "Agent", "WebSearch", "WebFetch"],
        include_partial_messages=True,
        effort=DRAFT_EFFORT,
    )

    log_path = run_dir / "draft_conversation.md"
    ui = get_tui()
    with open(log_path, "w", buffering=1) as log:
        log.write("# Draft Agent — Winston + John\n\n")
        seen_tool_ids: set[str] = set()
        try:
            async for message in query(prompt=prompt, options=options):
                msg_type = type(message).__name__
                if msg_type == "AssistantMessage":
                    for block in getattr(message, "content", []):
                        block_type = type(block).__name__
                        if block_type == "TextBlock":
                            text = getattr(block, "text", "") or ""
                            if text.strip():
                                log.write(f"\n{text}\n")
                                ui.agent_text(text)
                        elif block_type == "ToolUseBlock":
                            block_id = getattr(block, "id", None)
                            if block_id and block_id in seen_tool_ids:
                                continue
                            if block_id:
                                seen_tool_ids.add(block_id)
                            tool_name = getattr(block, "name", "?")
                            tool_input = _summarise_tool_input(
                                getattr(block, "input", None)
                            )
                            log.write(f"\n**{tool_name}**: `{tool_input}`\n")
                            ui.agent_tool(tool_name, tool_input)
        except Exception as e:
            log.write(f"\n> SDK error: {e}\n")


def run_draft_agent(
    project_dir: Path,
    run_dir: Path,
    spec: str | None = None,
    max_retries: int = 3,
) -> None:
    """Drive the draft call of a round.

    Invoked by the orchestrator when ``improvements.md`` has zero
    unchecked ``[ ]`` items.  Produces exactly one new US item (or
    writes nothing if every spec claim is implemented — Phase 4
    convergence handles the latter case).

    Args:
        project_dir: Root directory of the project being evolved.
        run_dir: Session directory for logs.
        spec: Path to the spec file relative to project_dir
            (default: README.md).
        max_retries: Maximum SDK call attempts on rate-limit errors.
    """
    # Lazy import — keeps leaf invariant AND lets tests that
    # ``patch("evolve.agent._run_agent_with_retries", ...)`` intercept.
    from evolve.agent import _run_agent_with_retries

    run_dir.mkdir(parents=True, exist_ok=True)
    prompt = _build_draft_prompt(project_dir, run_dir, spec=spec)
    _run_agent_with_retries(
        lambda: _run_draft_claude_agent(prompt, project_dir, run_dir),
        fail_label="Draft agent",
        max_retries=max_retries,
    )


# ---------------------------------------------------------------------------
# review_agent — Zara's adversarial review as a dedicated SDK call
# ---------------------------------------------------------------------------
#
# SPEC § "Multi-call round architecture" — the review call.  Runs after
# the implement call's commit + post-check.  Writes review_round_N.md
# with a verdict the orchestrator routes via _check_review_verdict.


def _build_review_prompt(
    project_dir: Path,
    run_dir: Path,
    round_num: int,
    spec: str | None = None,
) -> str:
    """Build the system prompt for the review agent."""
    # Lazy import — keeps leaf invariant AND lets tests that
    # ``patch("evolve.agent._load_project_context", ...)`` intercept.
    from evolve.agent import _load_project_context

    prompt_path = Path(__file__).resolve().parent.parent / "prompts" / "review.md"
    template = prompt_path.read_text() if prompt_path.is_file() else ""
    rdir = str(run_dir)
    runs_base_str = str(_runs_base(project_dir))
    template = template.replace("{project_dir}", str(project_dir))
    template = template.replace("{run_dir}", rdir)
    template = template.replace("{runs_base}", runs_base_str)
    template = template.replace("{round_num}", str(round_num))

    # Load the artifacts Zara needs: the implement conversation log for
    # this round, the git diff of the last commit, and the spec.
    ctx = _load_project_context(project_dir, spec=spec)
    readme = ctx["readme"] or "(no spec file found)"

    # Implement-call conversation log.  Prefer the per-attempt variant
    # if present; fall back to the canonical.
    convo_path = run_dir / f"conversation_loop_{round_num}.md"
    implement_log = convo_path.read_text() if convo_path.is_file() else "(no conversation log)"

    # Git diff of the latest commit — stripped to a size that fits the
    # review budget (~4000 bytes is plenty for most rounds).
    git_diff = ""
    try:
        import subprocess as _sp
        result = _sp.run(
            ["git", "diff", "HEAD^", "HEAD", "--", "."],
            cwd=str(project_dir),
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            git_diff = result.stdout[:8000]
        else:
            git_diff = f"(git diff failed: {result.stderr[:200]})"
    except (_sp.TimeoutExpired, FileNotFoundError, OSError) as e:
        git_diff = f"(git diff unavailable: {e})"

    sections = [
        template,
        f"\n\n## Spec ({spec or 'README.md'})\n{readme}",
        f"\n\n## Implement-call conversation log\n{implement_log[-6000:]}",
        f"\n\n## Git diff (HEAD^..HEAD)\n```\n{git_diff}\n```\n",
    ]
    return "".join(sections)


async def _run_review_claude_agent(
    prompt: str,
    project_dir: Path,
    run_dir: Path,
    round_num: int,
) -> None:
    """Spawn Zara as a dedicated SDK call.

    Opus (centralized ``MODEL``), ``effort=REVIEW_EFFORT`` (spec-fixed at
    ``"low"`` per SPEC § "Multi-call round architecture"), ``max_turns=MAX_TURNS``.
    Write is allowed (needs to create ``review_round_N.md``); Edit /
    Bash / Task / Agent / Web* are disallowed — review is write-once,
    not iterative editing of other files.
    """
    # Lazy import — keeps leaf invariant AND lets tests that
    # ``patch.object(agent_mod, "_patch_sdk_parser", ...)`` intercept.
    from evolve.agent import _patch_sdk_parser, _summarise_tool_input

    _patch_sdk_parser()
    from claude_agent_sdk import query, ClaudeAgentOptions, AssistantMessage, ResultMessage

    options = ClaudeAgentOptions(
        permission_mode="bypassPermissions",
        model=MODEL,
        max_turns=MAX_TURNS,
        cwd=str(project_dir),
        disallowed_tools=["Edit", "Bash", "Task", "Agent", "WebSearch", "WebFetch"],
        include_partial_messages=True,
        effort=REVIEW_EFFORT,
    )

    log_path = run_dir / f"review_conversation_round_{round_num}.md"
    ui = get_tui()
    with open(log_path, "w", buffering=1) as log:
        log.write(f"# Review Agent — Zara — round {round_num}\n\n")
        seen_tool_ids: set[str] = set()
        try:
            async for message in query(prompt=prompt, options=options):
                msg_type = type(message).__name__
                if msg_type == "AssistantMessage":
                    for block in getattr(message, "content", []):
                        block_type = type(block).__name__
                        if block_type == "TextBlock":
                            text = getattr(block, "text", "") or ""
                            if text.strip():
                                log.write(f"\n{text}\n")
                                ui.agent_text(text)
                        elif block_type == "ToolUseBlock":
                            block_id = getattr(block, "id", None)
                            if block_id and block_id in seen_tool_ids:
                                continue
                            if block_id:
                                seen_tool_ids.add(block_id)
                            tool_name = getattr(block, "name", "?")
                            tool_input = _summarise_tool_input(
                                getattr(block, "input", None)
                            )
                            log.write(f"\n**{tool_name}**: `{tool_input}`\n")
                            ui.agent_tool(tool_name, tool_input)
        except Exception as e:
            log.write(f"\n> SDK error: {e}\n")


def run_review_agent(
    project_dir: Path,
    run_dir: Path,
    round_num: int,
    spec: str | None = None,
    max_retries: int = 3,
) -> None:
    """Drive the review call of a round.

    Invoked by the orchestrator after the implement call's commit
    and post-check.  Writes ``{run_dir}/review_round_{N}.md`` with
    a verdict the orchestrator's ``_check_review_verdict`` parses.

    Args:
        project_dir: Root directory of the project being evolved.
        run_dir: Session directory for logs + review file.
        round_num: Current evolution round number.
        spec: Path to the spec file relative to project_dir.
        max_retries: Maximum SDK call attempts on rate-limit errors.
    """
    # Lazy import — keeps leaf invariant AND lets tests that
    # ``patch("evolve.agent._run_agent_with_retries", ...)`` intercept.
    from evolve.agent import _run_agent_with_retries

    run_dir.mkdir(parents=True, exist_ok=True)
    prompt = _build_review_prompt(project_dir, run_dir, round_num, spec=spec)
    _run_agent_with_retries(
        lambda: _run_review_claude_agent(prompt, project_dir, run_dir, round_num),
        fail_label="Review agent",
        max_retries=max_retries,
    )
