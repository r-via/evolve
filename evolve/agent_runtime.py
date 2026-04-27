"""Leaf runtime module for evolve agents.

Hosts spec-fixed runtime constants and SDK-adjacent helpers that other
``evolve/`` modules import without forming a cycle through ``evolve/agent.py``.

This module is intentionally a **leaf**: it imports ONLY from stdlib and
``claude_agent_sdk``.  No ``from evolve.X`` imports at module top — the
single dependency on the TUI inside ``_run_agent_with_retries`` is a
function-local import, which avoids the round-6 lazy-import trap (the
trap was about *constants* being lazy-resolved across modules; helper
functions doing ``import inside fn`` for an unrelated sibling
(``evolve.tui``) is a routine pattern that doesn't reintroduce cycles).

Symbols hoisted from ``evolve/agent.py`` (US-030, agent.py split step 1):

- ``MODEL`` — centralized Claude model used by every evolve agent.
- ``MAX_TURNS`` — single, centralized turn budget for every SDK call.
- ``DRAFT_EFFORT`` — fixed reasoning budget for the draft (Winston + John) call.
- ``REVIEW_EFFORT`` — fixed reasoning budget for the review (Zara) call.
- ``_TOOL_INPUT_SUMMARY_KEYS`` — ordered key probe list for tool-input rendering.
- ``_summarise_tool_input`` — one-line summary of a tool-use block's input.
- ``_patch_sdk_parser`` — idempotent SDK monkey-patch for malformed rate_limit_event.
- ``_is_benign_runtime_error`` — async-teardown predicate.
- ``_should_retry_rate_limit`` — rate-limit backoff calculator.
- ``_run_agent_with_retries`` — shared async-agent retry loop.

``EFFORT`` is intentionally NOT moved — it is mutated at runtime by
``_resolve_config`` in the orchestrator startup path, and the
runtime-mutation is observable via ``evolve.agent.EFFORT``.  Hoisting
it would reintroduce the round-6 trap pattern (constant resolved
lazily across modules → stale reads).  See SPEC.md § "The --effort flag"
and ``memory.md`` "--effort plumbing: 3-attempt pattern".
"""

from __future__ import annotations

import asyncio
import time


#: Default Claude model used by the agent for code analysis and fixes.
MODEL = "claude-opus-4-6"

#: Single, centralized turn budget passed to every ``claude_agent_sdk.query``
#: callsite via ``ClaudeAgentOptions(max_turns=...)``.  Per SPEC.md §
#: "Per-turn cap as a granularity forcing function" — modest enough to
#: surface oversized targets, large enough that drafting / reviewing /
#: implementing all fit comfortably in one round.  Override per-callsite
#: only with explicit justification (the memory curator's single-shot
#: protocol is the one documented exception).
MAX_TURNS = 60

#: Fixed reasoning-effort budget for the draft (Winston + John) call.  Per
#: SPEC.md § "Multi-call round architecture" — the draft pipeline is
#: bounded and near-deterministic (template-driven US emission); paying
#: for ``medium``/``high``/``max`` reasoning here is wasted spend.  This
#: constant is **spec-fixed** (not operator-tunable via CLI / env /
#: ``evolve.toml``) and intentionally NOT mutated by ``_resolve_config``.
DRAFT_EFFORT: str = "low"

#: Fixed reasoning-effort budget for the review (Zara) call.  Same
#: rationale as ``DRAFT_EFFORT``: review is a four-pass attack-plan with
#: a strict verdict schema, not free-form ideation, so ``low`` is
#: sufficient.  Spec-fixed; not operator-tunable.
REVIEW_EFFORT: str = "low"


# Ordered list of input keys to probe for a human-meaningful summary
# when rendering a tool-use line in the conversation log and TUI.
# The first hit wins; a trailing fallback stringifies the whole dict
# truncated to 80 chars.  Keep this list ordered by specificity:
# ``command`` is more useful than ``query`` is more useful than the
# raw dict repr.  New SDK tools (ToolSearch, WebSearch, WebFetch,
# TaskOutput, TodoWrite, Agent, …) get meaningful single-line
# summaries instead of an empty string after the tool name.
_TOOL_INPUT_SUMMARY_KEYS: tuple[str, ...] = (
    "command",       # Bash
    "pattern",       # Grep, Glob
    "file_path",     # Read, Write, Edit, NotebookEdit
    "query",         # ToolSearch, WebSearch
    "url",           # WebFetch
    "prompt",        # Agent / Task subagent invocation
    "description",   # Agent description when prompt is long
    "skill",         # Skill
    "to",            # SendMessage
    "task_id",       # TaskOutput / TaskStop
    "subagent_type", # Agent
)


def _summarise_tool_input(inp: object) -> str:
    """Render a one-line summary of a tool-use block's ``input``.

    Falls through ``_TOOL_INPUT_SUMMARY_KEYS`` in order, then special-
    cases a few bulkier keys (``old_string`` → edit marker,
    ``content`` → byte count, ``todos`` → todo count), and finally
    produces a truncated repr of the whole dict so new / uncommon
    tools at least render *something* after the tool name in the TUI
    and conversation log.  Previously an unknown key schema produced
    an empty line like ``[opus] ToolSearch → `` that looked broken.
    """
    if not inp:
        return ""
    if not isinstance(inp, dict):
        return str(inp)[:100]
    for key in _TOOL_INPUT_SUMMARY_KEYS:
        if key in inp and inp[key]:
            val = inp[key]
            if isinstance(val, str):
                return val[:100]
            return str(val)[:100]
    if "old_string" in inp:
        return f'{inp.get("file_path", "?")} (edit)'
    if "content" in inp:
        try:
            return f'({len(inp["content"])} chars)'
        except TypeError:
            pass
    if "todos" in inp:
        try:
            return f'({len(inp["todos"])} todos)'
        except TypeError:
            pass
    # Last-resort fallback: truncated repr of the full dict so the
    # caller sees *some* signal about what the tool was invoked with.
    return str(inp)[:80]


def _patch_sdk_parser() -> None:
    """Monkey-patch SDK to not crash on malformed rate_limit_event.

    Wraps ``message_parser.parse_message`` so that malformed rate-limit
    events return None instead of raising.  The patch is idempotent —
    repeated calls are safe due to a ``_patched`` sentinel attribute.
    """
    try:
        from claude_agent_sdk._internal import message_parser
        if getattr(message_parser.parse_message, '_patched', False):
            return
        original = message_parser.parse_message
        def patched(data):
            try:
                return original(data)
            except Exception:
                if isinstance(data, dict) and data.get("type") == "rate_limit_event":
                    return None
                raise
        patched._patched = True
        message_parser.parse_message = patched
    except Exception:
        pass


def _is_benign_runtime_error(e: RuntimeError) -> bool:
    """Check if a RuntimeError is a benign async teardown issue we can ignore.

    Returns True for known harmless messages like 'cancel scope' or
    'Event loop is closed' that occur during asyncio shutdown.
    """
    msg = str(e)
    return "cancel scope" in msg or "Event loop is closed" in msg


def _should_retry_rate_limit(e: Exception, attempt: int, max_retries: int) -> int | None:
    """Return wait time in seconds if the error is a rate limit and retries remain.

    Uses linear backoff (60s * attempt).  Returns None if the error is not
    a rate-limit error or if all retries have been exhausted.

    Args:
        e: The exception raised by the SDK.
        attempt: Current attempt number (1-based).
        max_retries: Maximum number of retry attempts allowed.
    """
    if "rate_limit" in str(e).lower() and attempt < max_retries:
        return 60 * attempt
    return None


def _run_agent_with_retries(
    async_fn,
    *,
    fail_label: str = "Agent",
    max_retries: int = 5,
) -> str | None:
    """Shared retry loop for running an async agent function.

    Handles SDK import check, asyncio warning filters, benign teardown
    errors, and rate-limit backoff.  Callers supply the actual async
    callable (already bound to its arguments).

    Returns:
        The subtype string returned by the async function (typically from
        ``run_claude_agent``), or ``None`` on failure / SDK absence.

    Args:
        async_fn: Zero-argument async callable that runs the agent.
            May return a string (subtype) or None.
        fail_label: Label used in the failure warning message.
        max_retries: Maximum SDK call attempts on rate-limit errors.
    """
    # Lazy import via ``evolve.agent`` (not ``evolve.tui``) is deliberate:
    # ``evolve.agent`` re-exports ``get_tui`` at its module top, and many
    # tests patch ``evolve.agent.get_tui`` to inject a MagicMock UI
    # (e.g. ``tests/test_agent_coverage.py::TestAnalyzeAndFixRetry``).
    # Looking up the name on ``evolve.agent`` here means those patches
    # intercept correctly — preserving the pre-hoist behaviour where
    # ``_run_agent_with_retries`` lived in ``agent.py`` and resolved
    # ``get_tui`` against the agent module's namespace.  The function-
    # local import keeps the module-top stdlib-only invariant (US-030
    # criterion 2: ``grep ^from evolve\.`` returns 0 — multiline-^ does
    # not match indented imports).
    from evolve.agent import get_tui

    ui = get_tui()
    try:
        from claude_agent_sdk import query  # noqa: F401 — import check only
    except ImportError:
        ui.warn("claude-agent-sdk not installed, skipping agent")
        return None

    import warnings
    warnings.filterwarnings("ignore", message=".*cancel scope.*")
    warnings.filterwarnings("ignore", message=".*Event loop is closed.*")

    for attempt in range(1, max_retries + 1):
        try:
            result = asyncio.run(async_fn())
            return result
        except Exception as e:
            if isinstance(e, RuntimeError) and _is_benign_runtime_error(e):
                return None

            wait = _should_retry_rate_limit(e, attempt, max_retries)
            if wait is not None:
                ui.sdk_rate_limited(wait, attempt, max_retries)
                time.sleep(wait)
                continue

            ui.warn(f"{fail_label} failed ({e})")
            return None
    return None
