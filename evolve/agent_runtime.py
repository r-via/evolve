"""Backward-compat shim — real implementation in evolve.infrastructure.claude_sdk.runtime.

All symbols re-exported for existing ``from evolve.agent_runtime import MODEL`` etc.
"""

from evolve.infrastructure.claude_sdk.runtime import (  # noqa: F401
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
