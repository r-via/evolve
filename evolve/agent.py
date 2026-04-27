"""Claude opus agent — reads README as spec, fixes code, tracks improvements."""

from __future__ import annotations

import asyncio  # noqa: F401 — kept for ``patch("evolve.agent.asyncio.run")`` test targets
import re
import shutil
import time  # noqa: F401 — kept for ``patch("evolve.agent.time.sleep")`` test targets
from pathlib import Path

from evolve.state import _is_needs_package, _runs_base
from evolve.tui import get_tui

# US-030 re-exports: spec-fixed runtime constants and SDK helpers live in
# the leaf module ``evolve.agent_runtime`` to break the agent.py monolith
# (SPEC § "Hard rule: source files MUST NOT exceed 500 lines").  These
# names are re-bound at module top so existing patch targets like
# ``patch("evolve.agent.MODEL")`` and ``monkeypatch.setattr(agent_mod,
# "_patch_sdk_parser", ...)`` continue to work, and so internal call
# sites in this file (``MODEL`` / ``MAX_TURNS`` / ``EFFORT`` /
# ``_summarise_tool_input(...)`` / ``_patch_sdk_parser()`` /
# ``_run_agent_with_retries(...)``) bind the re-exported name rather
# than going through ``agent_runtime.X`` indirection (which would
# bypass the test-side monkeypatches).  ``EFFORT`` deliberately stays
# defined locally below because it is mutated at runtime by the
# orchestrator's ``_resolve_config`` chain — see SPEC § "The --effort
# flag" and ``memory.md`` "--effort plumbing: 3-attempt pattern".
from evolve.agent_runtime import (  # noqa: F401 — re-exports for back-compat
    MODEL,
    MAX_TURNS,
    DRAFT_EFFORT,
    REVIEW_EFFORT,
    _TOOL_INPUT_SUMMARY_KEYS,
    _summarise_tool_input,
    _patch_sdk_parser,
    _is_benign_runtime_error,
    _should_retry_rate_limit,
    _run_agent_with_retries,
)

# US-035 re-exports: prompt-building helpers live in the leaf module
# ``evolve.prompt_builder`` (SPEC § "Hard rule" — agent.py was 1226
# lines, 2.45× the cap).  These names are re-bound at module top so
# existing patch targets like ``patch("evolve.agent.build_prompt", ...)``,
# ``patch("evolve.agent._load_project_context", ...)``, and
# ``agent_mod._PREV_ATTEMPT_LOG_FMT`` continue to work, and so the
# internal call site ``build_prompt(...)`` inside ``analyze_and_fix``
# binds the re-exported name (not the original source module's
# binding — same lesson as US-028's ``_diag.`` de-aliasing).
# ``_detect_current_attempt`` deliberately STAYS in this file because
# both ``analyze_and_fix`` and ``build_prompt_blocks`` need it; the
# extracted module imports it lazily via ``from evolve.agent import
# _detect_current_attempt`` to preserve patch-surface semantics.
from evolve.prompt_builder import (  # noqa: F401 — re-exports for back-compat
    PromptBlocks,
    _PREV_ATTEMPT_LOG_FMT,
    _MEMORY_WIPED_HEADER_FMT,
    _PRIOR_ROUND_ANOMALY_PATTERNS,
    _load_project_context,
    _detect_prior_round_anomalies,
    build_prompt_blocks,
    build_prompt,
    # Round-3 audit-fix helpers extracted further into
    # ``evolve/prompt_diagnostics.py`` — re-export through the 3-link
    # chain (``agent`` → ``prompt_builder`` → ``prompt_diagnostics``)
    # so ``patch("evolve.agent.<X>")`` continues to intercept by
    # ``is``-identity.
    build_prev_crash_section,
    build_prior_round_audit_section,
    build_prev_attempt_section,
)


def _detect_current_attempt(run_dir: Path | None, round_num: int) -> int:
    """Return the current attempt number (1-based) for *round_num*.

    Inspects ``subprocess_error_round_{round_num}.txt`` left by the
    orchestrator after a failed attempt.  Each diagnostic header ends in
    ``(attempt K)`` — if K=2 just failed, the next run is attempt 3.

    Returns 1 when no diagnostic for the current round exists (first attempt).
    """
    if not run_dir:
        return 1
    rdir = Path(run_dir)
    candidates = sorted(
        rdir.glob("subprocess_error_round_*.txt"),
        key=lambda p: int(re.search(r'_(\d+)\.txt$', p.name).group(1)),
        reverse=True,
    )
    if not candidates:
        return 1
    f = candidates[0]
    m_round = re.search(r"subprocess_error_round_(\d+)\.txt$", str(f))
    if not m_round or int(m_round.group(1)) != round_num:
        return 1
    try:
        text = f.read_text()
    except OSError:
        return 1
    m_att = re.search(r"\(attempt (\d+)\)", text)
    if m_att:
        return int(m_att.group(1)) + 1
    return 1

#: Reasoning effort level passed to ``ClaudeAgentOptions(effort=...)``.
#: Accepted values: ``"low" | "medium" | "high" | "max"`` (or ``None`` to
#: fall back to the SDK default).  Default is ``"medium"`` per SPEC.md §
#: "The --effort flag" — medium gives the best cost/quality/latency
#: ratio for the typical evolve round (small fixes, tests, incremental
#: refactors).  Bump to ``high``/``max`` per session when the backlog
#: contains hard architectural work.  The value is overwritten by
#: ``loop.py`` (and the sync-readme / dry-run / validate entry points)
#: at the start of each session based on the resolved CLI → env →
#: config → default chain.
#:
#: ``EFFORT`` stays defined here (NOT in ``evolve/agent_runtime.py``)
#: because it is mutated at runtime by ``_resolve_config`` — hoisting
#: it would reintroduce the round-6 lazy-import trap pattern (constant
#: resolved across modules → stale reads in callers that bind the
#: name lazily).  ``MODEL`` / ``MAX_TURNS`` / ``DRAFT_EFFORT`` /
#: ``REVIEW_EFFORT`` ARE in ``agent_runtime`` because they are
#: spec-fixed and never mutated.
EFFORT: str | None = "medium"


# NOTE: ``_PREV_ATTEMPT_LOG_FMT``, ``_MEMORY_WIPED_HEADER_FMT``,
# ``_PRIOR_ROUND_ANOMALY_PATTERNS``, ``PromptBlocks``,
# ``_load_project_context``, ``_detect_prior_round_anomalies``,
# ``build_prompt_blocks``, and ``build_prompt`` were extracted into
# ``evolve/prompt_builder.py`` (US-035, agent.py split step 5) and are
# re-exported at module top.  See the import block at the top of this
# file.
#
# NOTE: ``_TOOL_INPUT_SUMMARY_KEYS`` and ``_summarise_tool_input`` were
# hoisted into ``evolve/agent_runtime.py`` (US-030, agent.py split step 1)
# and are re-exported at module top.  See the import block at the top
# of this file.



# NOTE: ``_patch_sdk_parser`` was hoisted into ``evolve/agent_runtime.py``
# (US-030, agent.py split step 1) and is re-exported at module top.


def _build_multimodal_prompt(text: str, images: list[Path]) -> object:
    """Build an async iterable prompt with text and image content blocks.

    Constructs a multimodal message for the Claude Agent SDK's ``query()``
    function, combining the text prompt with base64-encoded PNG images.

    Args:
        text: The text prompt.
        images: List of paths to PNG image files to attach.

    Returns:
        An async iterable yielding a single user message dict with
        multimodal content blocks.
    """
    import base64

    content: list[dict] = [{"type": "text", "text": text}]
    for img_path in images:
        if not img_path.is_file():
            continue
        try:
            data = base64.standard_b64encode(img_path.read_bytes()).decode("ascii")
            content.append({
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/png",
                    "data": data,
                },
            })
        except (OSError, ValueError):
            continue

    async def _gen():
        yield {
            "type": "user",
            "message": {"role": "user", "content": content},
            "parent_tool_use_id": None,
            "session_id": "party-mode",
        }

    return _gen()


async def run_claude_agent(
    prompt: str,
    project_dir: Path,
    round_num: int = 1,
    run_dir: Path | None = None,
    log_filename: str | None = None,
    images: list[Path] | None = None,
) -> str | None:
    """Run Claude Code agent with the given prompt. Logs conversation to run_dir/.

    Streams SDK messages, deduplicates partial updates, and writes a
    Markdown conversation log.  Tool calls are shown live in the TUI.

    The prompt is passed as a **single string** — never a list-of-dicts.
    The underlying Claude Code CLI applies prompt caching natively on
    stable leading prefixes; see SPEC.md § "Prompt caching".

    Returns:
        The ``ResultMessage.subtype`` string (``"success"``,
        ``"error_max_turns"``, ``"error_during_execution"``) from the
        final ``ResultMessage`` emitted by the SDK, or ``None`` when no
        ``ResultMessage`` was observed (e.g. SDK error before completion).
        See SPEC.md § "Authoritative termination signal from the SDK".

    Args:
        prompt: The assembled system prompt for the agent.
        project_dir: Root directory of the project (used as cwd).
        round_num: Current evolution round number (for log naming).
        run_dir: Directory to write the conversation log into.
        log_filename: Override the default log filename.
        images: Optional list of image file paths to attach as multimodal
            content blocks alongside the text prompt.
    """
    _patch_sdk_parser()
    from claude_agent_sdk import query, ClaudeAgentOptions, AssistantMessage, ResultMessage

    options = ClaudeAgentOptions(
        permission_mode="bypassPermissions",
        model=MODEL,
        max_turns=MAX_TURNS,
        cwd=str(project_dir),
        disallowed_tools=["Task", "Agent", "WebSearch", "WebFetch"],
        include_partial_messages=True,
        effort=EFFORT,
    )

    # Log file
    out_dir = run_dir or _runs_base(project_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    fname = log_filename or f"conversation_loop_{round_num}.md"
    log_path = out_dir / fname

    ui = get_tui()

    with open(log_path, "w", buffering=1) as log:
        log.write(f"# Evolution Round {round_num}\n\n")

        def _log(line: str, console: bool = False):
            log.write(line + "\n")
            if console:
                ui.agent_text(line)

        turn = 0
        tools_used = 0
        # Token usage tracking — updated from messages with a ``usage`` attr.
        # The final ResultMessage typically carries cumulative totals.
        _usage_input = 0
        _usage_output = 0
        _usage_cache_create = 0
        _usage_cache_read = 0
        # Track the final ResultMessage's subtype — the authoritative
        # termination signal per SPEC.md § "Authoritative termination
        # signal from the SDK".  Updated on every ResultMessage so the
        # last one wins.
        final_subtype: str | None = None
        final_num_turns: int | None = None
        # Track already-logged block IDs to skip duplicate partial messages.
        # With include_partial_messages=True, the same AssistantMessage is
        # re-emitted with progressively more content.  We keep the option
        # enabled so tool calls appear in the TUI as soon as they start,
        # but we deduplicate by tracking seen tool-use block IDs and
        # seen text content hashes.
        seen_tool_ids: set[str] = set()
        seen_text_hashes: set[int] = set()

        try:
            # Build multimodal prompt when images are provided
            effective_prompt: str | object = prompt
            if images:
                effective_prompt = _build_multimodal_prompt(prompt, images)

            async for message in query(prompt=effective_prompt, options=options):
                if message is None:
                    continue

                msg_type = type(message).__name__
                turn += 1

                if msg_type == "StreamEvent":
                    continue

                if isinstance(message, (AssistantMessage, ResultMessage)):
                    # Capture ResultMessage termination signal — the
                    # authoritative source per SPEC § "Authoritative
                    # termination signal from the SDK".
                    if isinstance(message, ResultMessage):
                        final_subtype = getattr(message, "subtype", None)
                        final_num_turns = getattr(message, "num_turns", None)
                    if not hasattr(message, "content") or not message.content:
                        continue
                    for block in message.content:
                        block_type = type(block).__name__

                        if hasattr(block, "thinking"):
                            # Thinking blocks may be streamed incrementally;
                            # deduplicate by content hash.
                            h = hash(block.thinking)
                            if h in seen_text_hashes:
                                continue
                            seen_text_hashes.add(h)
                            _log(f"\n### Thinking\n\n{block.thinking}\n")

                        elif hasattr(block, "text") and block.text.strip():
                            h = hash(block.text)
                            if h in seen_text_hashes:
                                continue
                            seen_text_hashes.add(h)
                            _log(f"\n{block.text}\n", console=True)

                        elif hasattr(block, "name"):
                            # ToolUseBlock — deduplicate by block id so
                            # partial updates don't log the same call twice.
                            block_id = getattr(block, "id", None)
                            if block_id and block_id in seen_tool_ids:
                                continue
                            if block_id:
                                seen_tool_ids.add(block_id)

                            tools_used += 1
                            tool_name = block.name
                            tool_input = _summarise_tool_input(
                                getattr(block, "input", None)
                            )
                            _log(f"\n**{tool_name}**: `{tool_input}`\n")
                            ui.agent_tool(tool_name, tool_input)

                        elif block_type == "ToolResultBlock":
                            # Tool results are not partial — log normally.
                            content_str = str(block.content)[:500] if hasattr(block, "content") and block.content else ""
                            is_error = getattr(block, "is_error", False)
                            if is_error:
                                _log(f"\n> Error:\n> {content_str}\n")
                            else:
                                _log(f"\n```\n{content_str}\n```\n")
                else:
                    if msg_type == "RateLimitEvent":
                        _log(f"\n> Rate limited\n")
                    elif msg_type == "SystemMessage":
                        _log(f"\n---\n*Session initialized*\n---\n")

                # Extract token usage from any message that carries it.
                # The SDK's ResultMessage and AssistantMessage may include a
                # ``usage`` object; we always keep the latest values (the
                # final ResultMessage has cumulative totals).
                _mu = getattr(message, "usage", None)
                if _mu is not None:
                    _usage_input = getattr(_mu, "input_tokens", 0) or 0
                    _usage_output = getattr(_mu, "output_tokens", 0) or 0
                    _usage_cache_create = getattr(_mu, "cache_creation_input_tokens", 0) or 0
                    _usage_cache_read = getattr(_mu, "cache_read_input_tokens", 0) or 0

        except Exception as e:
            _log(f"\n> SDK error: {e}\n")

        # Extended Done line with subtype per SPEC § "Authoritative
        # termination signal from the SDK".
        _done_parts = f"**Done**: {turn} messages, {tools_used} tool calls"
        if final_subtype is not None:
            _done_parts += f", subtype={final_subtype}"
        if final_num_turns is not None:
            _done_parts += f", num_turns={final_num_turns}"
        _log(f"\n---\n\n{_done_parts}\n")

        # Warn the operator in real time when the SDK signals an error
        # termination (e.g. error_max_turns, error_during_execution).
        _is_err = final_subtype is not None and final_subtype.startswith("error")
        if _is_err:
            _warn_msg = f"Agent stopped: {final_subtype}"
            if final_num_turns is not None:
                _warn_msg += f" after {final_num_turns} turns"
            ui.warn(f"⚠ {_warn_msg}")

    # Write usage_round_N.json — always, even if counts are zero (the
    # aggregate_usage scanner expects the file to exist for tracked rounds).
    try:
        from evolve.costs import TokenUsage
        from datetime import datetime as _dt, timezone as _tz
        _tok = TokenUsage(
            input_tokens=_usage_input,
            output_tokens=_usage_output,
            cache_creation_tokens=_usage_cache_create,
            cache_read_tokens=_usage_cache_read,
            round=round_num,
            model=MODEL,
            timestamp=_dt.now(_tz.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        )
        _tok.save(out_dir / f"usage_round_{round_num}.json")
    except Exception:
        pass  # Non-fatal — usage tracking is observability, not control flow

    ui.agent_done(tools_used, str(log_path))
    return final_subtype


# NOTE: ``_is_benign_runtime_error`` and ``_should_retry_rate_limit``
# were hoisted into ``evolve/agent_runtime.py`` (US-030, agent.py split
# step 1) and are re-exported at module top.


def analyze_and_fix(
    project_dir: Path,
    check_output: str = "",
    check_cmd: str | None = None,
    allow_installs: bool = False,
    max_retries: int = 5,
    round_num: int = 1,
    run_dir: Path | None = None,
    spec: str | None = None,
    yolo: bool | None = None,
    check_timeout: int = 20,
) -> str | None:
    """Run Claude opus agent to analyze and fix code.

    Builds a prompt, then invokes the agent with retry logic for rate limits
    and graceful handling of benign async teardown errors.

    Returns:
        The ``ResultMessage.subtype`` string from the SDK (``"success"``,
        ``"error_max_turns"``, ``"error_during_execution"``) or ``None``.
        See SPEC.md § "Authoritative termination signal from the SDK".

    Args:
        project_dir: Root directory of the project being evolved.
        check_output: Output from the most recent check command.
        check_cmd: Shell command used to verify the project.
        allow_installs: If True, allow improvements tagged [needs-package].
        max_retries: Maximum SDK call attempts on rate-limit errors.
        round_num: Current evolution round number.
        run_dir: Session run directory for conversation logs.
        spec: Path to the spec file relative to project_dir (default: README.md).
        yolo: Deprecated alias for *allow_installs*. Will be removed in a future version.
    """
    if yolo is not None:
        allow_installs = yolo
    # Prompt caching: the underlying Claude Code CLI handles caching
    # natively on stable system prompts across calls — no explicit
    # ``cache_control`` wiring needed.  The SDK's
    # ``ClaudeAgentOptions.system_prompt`` signature is ``str |
    # SystemPromptPreset | None`` (verified against
    # ``claude-agent-sdk`` 0.1.50) and does NOT accept the
    # Anthropic-API ``list[dict]`` shape; passing a list silently
    # produces an empty-system-prompt call (the symptom was the
    # agent returning with zero tool calls on every round).  See
    # SPEC.md § "Prompt caching" for the rationale and for the
    # prefix-ordering contract that keeps cache hits reliable.
    full_prompt = build_prompt(
        project_dir, check_output, check_cmd, allow_installs, run_dir,
        spec=spec, round_num=round_num, check_timeout=check_timeout,
    )

    # Per-attempt conversation log filename.  Each orchestrator-level subprocess
    # attempt gets its own file (no overwrite), so a debug retry can read the
    # prior attempt's full transcript and continue from where it stopped.
    # See SPEC.md § "Retry continuity" rule (1).
    current_attempt = _detect_current_attempt(run_dir, round_num)
    attempt_log_fname = f"conversation_loop_{round_num}_attempt_{current_attempt}.md"

    async def _run():
        return await run_claude_agent(
            full_prompt, project_dir,
            round_num=round_num, run_dir=run_dir, log_filename=attempt_log_fname,
        )

    subtype = _run_agent_with_retries(
        _run,
        fail_label="Claude Code agent",
        max_retries=max_retries,
    )

    # Copy the successful attempt's log to the canonical
    # ``conversation_loop_{round_num}.md`` for backward compatibility with
    # report generation, party mode, and the agent's own stuck-loop self-
    # monitoring (which globs the canonical name from prior rounds).
    if run_dir is not None:
        attempt_log = Path(run_dir) / attempt_log_fname
        canonical_log = Path(run_dir) / f"conversation_loop_{round_num}.md"
        if attempt_log.is_file():
            try:
                shutil.copyfile(attempt_log, canonical_log)
            except OSError:
                # Cross-filesystem or permission issues are non-fatal — the
                # per-attempt log is the source of truth; the copy is just
                # convenience for downstream consumers.
                pass

    return subtype



# ---------------------------------------------------------------------------
# One-shot agents (dry-run / validate / diff / sync-readme) — extracted to
# evolve/oneshot_agents.py per US-033 (agent.py split step 4)
# ---------------------------------------------------------------------------
#
# SPEC § "The --dry-run flag", "The --validate flag", "evolve diff",
# "evolve sync-readme" — the read-only / single-shot agent invocations.
# The implementations live in ``evolve.oneshot_agents`` (US-033 split,
# mirrors US-027/US-030/US-031/US-032/spec_archival pattern) so this
# file moves toward the SPEC § "Hard rule: source files MUST NOT exceed
# 500 lines" cap.  The names are re-exported below at module top so
# existing test patch targets (``patch("evolve.agent.run_dry_run_agent")``,
# ``from evolve.agent import _run_validate_claude_agent``,
# ``from evolve.agent import SYNC_README_NO_CHANGES_SENTINEL``) and the
# orchestrator's late-binding imports (``from evolve.agent import
# run_dry_run_agent, run_validate_agent, run_diff_agent,
# run_sync_readme_agent`` inside ``evolve/orchestrator.py``) continue to
# work.
from evolve.oneshot_agents import (  # noqa: E402  (intentional late import)
    SYNC_README_NO_CHANGES_SENTINEL,
    _build_check_section,
    build_validate_prompt,
    build_dry_run_prompt,
    _run_readonly_claude_agent,
    _run_dry_run_claude_agent,
    run_dry_run_agent,
    _run_validate_claude_agent,
    run_validate_agent,
    build_diff_prompt,
    _run_diff_claude_agent,
    run_diff_agent,
    build_sync_readme_prompt,
    _run_sync_readme_claude_agent,
    run_sync_readme_agent,
)


# ---------------------------------------------------------------------------
# Memory curation (Mira) — dedicated curator agent
# ---------------------------------------------------------------------------
#
# Implementation lives in ``evolve/memory_curation.py`` to satisfy SPEC §
# "Hard rule: source files MUST NOT exceed 500 lines" (US-031, mirrors
# US-027/US-030 extraction pattern).  Public symbols are re-exported here
# for backward compatibility — existing tests do
# ``patch("evolve.agent.run_memory_curation", ...)`` and
# ``patch("evolve.agent._run_agent_with_retries")``; the orchestrator's
# ``from evolve.agent import run_memory_curation`` callsite continues to
# work via these re-exports.

from evolve.memory_curation import (  # noqa: E402  (intentional late import)
    CURATION_LINE_THRESHOLD,
    CURATION_ROUND_INTERVAL,
    _CURATION_MAX_SHRINK,
    _should_run_curation,
    build_memory_curation_prompt,
    _run_memory_curation_claude_agent,
    run_memory_curation,
)


# ---------------------------------------------------------------------------
# spec_archival — Sid (SPEC Archivist), extracts stable sections to archive
# ---------------------------------------------------------------------------
#
# Implementation lives in ``evolve/spec_archival.py`` to satisfy SPEC § "Hard
# rule: source files MUST NOT exceed 500 lines".  Public symbols are
# re-exported here for backward compatibility — existing tests do
# ``patch("evolve.agent.run_spec_archival", ...)`` and
# ``from evolve.agent import _should_run_spec_archival``; both forms keep
# working through these re-exports.

from evolve.spec_archival import (  # noqa: E402  (intentional late import)
    ARCHIVAL_LINE_THRESHOLD,
    ARCHIVAL_ROUND_INTERVAL,
    _ARCHIVAL_MAX_SHRINK,
    _should_run_spec_archival,
    build_spec_archival_prompt,
    _run_spec_archival_claude_agent,
    run_spec_archival,
)


# ---------------------------------------------------------------------------
# draft_agent + review_agent — extracted to evolve/draft_review.py per US-032
# ---------------------------------------------------------------------------
#
# SPEC § "Multi-call round architecture" — the drafting and review calls.
# The implementations live in ``evolve.draft_review`` (US-032 split, mirrors
# the US-027/US-030/US-031/spec_archival pattern) so this file stays under
# the SPEC § "Hard rule: source files MUST NOT exceed 500 lines" cap.  The
# names are re-exported below at module top so existing test patch targets
# (``patch("evolve.agent.run_draft_agent")``, ``monkeypatch.setattr(agent_mod,
# "run_review_agent", ...)``) and the orchestrator's late-binding import
# (``from evolve.agent import run_draft_agent``) continue to work.
from evolve.draft_review import (  # noqa: E402  (intentional late import)
    _build_draft_prompt,
    _run_draft_claude_agent,
    run_draft_agent,
    _build_review_prompt,
    _run_review_claude_agent,
    run_review_agent,
)
