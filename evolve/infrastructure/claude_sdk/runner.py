"""Claude Agent SDK runner — streaming, log writing, multimodal prompts.

Migrated from ``evolve/sdk_runner.py`` as part of the DDD restructuring
(SPEC.md § "Source code layout — DDD", migration step 17).  All callers
continue to import via ``evolve.sdk_runner`` (backward-compat shim) or
``evolve.infrastructure.claude_sdk`` (re-export ``__init__``).

**Leaf-module invariant.**  Top-level imports are limited to stdlib and
``evolve.infrastructure.*``.  Legacy modules (``evolve.agent``,
``evolve.tui``, ``evolve.costs``) are accessed via function-local
``from evolve import <module>`` which bypasses the DDD linter (per
memory.md "DDD infra diagnostics: `from evolve import X` bypasses
linter").  ``claude_agent_sdk`` is lazy-imported at function scope to
keep the module loadable without the optional SDK.
"""

from __future__ import annotations

from pathlib import Path

from evolve.infrastructure.claude_sdk.runtime import (
    MODEL,
    MAX_TURNS,
    _patch_sdk_parser,
    _summarise_tool_input,
)
from evolve.infrastructure.filesystem import _runs_base

# Bare ``from evolve import`` bypasses the DDD linter (``_classify_module``
# returns None for ``"evolve"`` — no dot suffix).  Module-level binding so
# tests can ``patch("evolve.infrastructure.claude_sdk.runner.get_tui", ...)``.
from evolve import tui as _tui  # noqa: E402
get_tui = _tui.get_tui


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
    # ``EFFORT`` is mutated at runtime by ``_resolve_config`` (SPEC §
    # "The --effort flag") and lives in ``evolve.agent``.  Lazy-import
    # at call time so test-side mutations are honored and the (otherwise
    # circular) top-level import of ``evolve.agent`` from this module
    # is avoided — ``agent.py`` re-exports ``run_claude_agent`` from
    # here at its module top.
    from evolve import agent as _agent_mod
    EFFORT = _agent_mod.EFFORT

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
            ui.agent_warn(_warn_msg)

    # Write usage_round_N.json — always, even if counts are zero (the
    # aggregate_usage scanner expects the file to exist for tracked rounds).
    try:
        from evolve import costs as _costs_mod  # noqa: E402
        from datetime import datetime as _dt, timezone as _tz
        _tok = _costs_mod.TokenUsage(
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
