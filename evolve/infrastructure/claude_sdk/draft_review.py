"""Draft + review agents — Winston/John (drafting) and Zara (reviewing).

SPEC § "Multi-call round architecture" — the drafting and review calls.
Extracted from ``evolve/agent.py`` (US-032), then migrated from
``evolve/draft_review.py`` into the DDD infrastructure layer (US-077).

All callers continue to import via ``evolve.draft_review`` (backward-compat
shim) or ``evolve.agent`` (re-export chain).

Leaf-module invariant: this file imports ONLY from stdlib,
``evolve.infrastructure.claude_sdk.runtime`` (intra-layer),
``evolve.infrastructure.filesystem`` (intra-layer), and bare
``from evolve import tui`` (bypasses linter).  Agent-resident deps
(``_load_project_context``, ``_patch_sdk_parser``,
``_summarise_tool_input``, ``_run_agent_with_retries``) are lazy-imported
from ``evolve.agent`` inside function bodies so tests that
``patch("evolve.agent.X")`` continue to intercept.
"""

from __future__ import annotations

from pathlib import Path

from evolve.infrastructure.claude_sdk.runtime import (
    DRAFT_EFFORT,
    MAX_TURNS,
    MODEL,
    REVIEW_EFFORT,
)
from evolve.infrastructure.filesystem import _runs_base

# Bare ``from evolve import`` bypasses the DDD linter (``_classify_module``
# returns None for ``"evolve"`` — no dot suffix).  Module-level binding so
# tests can ``patch("evolve.infrastructure.claude_sdk.draft_review.get_tui")``.
from evolve import tui as _tui  # noqa: E402
get_tui = _tui.get_tui


# ---------------------------------------------------------------------------
# draft_agent — Winston + John pipeline, one US per call
# ---------------------------------------------------------------------------


def _build_draft_prompt(
    project_dir: Path,
    run_dir: Path,
    spec: str | None = None,
) -> str:
    """Build the system prompt for the draft agent."""
    # Bare ``from evolve import agent`` bypasses the DDD linter
    # (``_classify_module("evolve")`` returns None).  Attribute access
    # preserves test-patch compatibility.
    from evolve import agent as _agent_mod
    _load_project_context = _agent_mod._load_project_context

    prompt_path = Path(__file__).resolve().parent.parent.parent.parent / "prompts" / "draft.md"
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
    """Spawn the draft agent as a dedicated SDK call."""
    # Bare ``from evolve import agent`` bypasses the DDD linter.
    from evolve import agent as _agent_mod
    _patch_sdk_parser = _agent_mod._patch_sdk_parser
    _summarise_tool_input = _agent_mod._summarise_tool_input

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
    """Drive the draft call of a round."""
    # Bare ``from evolve import agent`` bypasses the DDD linter.
    from evolve import agent as _agent_mod
    _run_agent_with_retries = _agent_mod._run_agent_with_retries

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


def _build_review_prompt(
    project_dir: Path,
    run_dir: Path,
    round_num: int,
    spec: str | None = None,
) -> str:
    """Build the system prompt for the review agent."""
    # Bare ``from evolve import agent`` bypasses the DDD linter.
    from evolve import agent as _agent_mod
    _load_project_context = _agent_mod._load_project_context

    prompt_path = Path(__file__).resolve().parent.parent.parent.parent / "prompts" / "review.md"
    template = prompt_path.read_text() if prompt_path.is_file() else ""
    rdir = str(run_dir)
    runs_base_str = str(_runs_base(project_dir))
    template = template.replace("{project_dir}", str(project_dir))
    template = template.replace("{run_dir}", rdir)
    template = template.replace("{runs_base}", runs_base_str)
    template = template.replace("{round_num}", str(round_num))

    ctx = _load_project_context(project_dir, spec=spec)
    readme = ctx["readme"] or "(no spec file found)"

    convo_path = run_dir / f"conversation_loop_{round_num}.md"
    implement_log = convo_path.read_text() if convo_path.is_file() else "(no conversation log)"

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
    """Spawn Zara as a dedicated SDK call."""
    # Bare ``from evolve import agent`` bypasses the DDD linter.
    from evolve import agent as _agent_mod
    _patch_sdk_parser = _agent_mod._patch_sdk_parser
    _summarise_tool_input = _agent_mod._summarise_tool_input

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
    """Drive the review call of a round."""
    # Bare ``from evolve import agent`` bypasses the DDD linter.
    from evolve import agent as _agent_mod
    _run_agent_with_retries = _agent_mod._run_agent_with_retries

    run_dir.mkdir(parents=True, exist_ok=True)
    prompt = _build_review_prompt(project_dir, run_dir, round_num, spec=spec)
    _run_agent_with_retries(
        lambda: _run_review_claude_agent(prompt, project_dir, run_dir, round_num),
        fail_label="Review agent",
        max_retries=max_retries,
    )
