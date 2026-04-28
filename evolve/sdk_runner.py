"""Backward-compat shim — real code lives in evolve.infrastructure.claude_sdk.runner.

Migrated to ``evolve/infrastructure/claude_sdk/runner.py`` per SPEC § DDD
(migration step 17, US-071).  All symbols re-exported here so existing
``from evolve.sdk_runner import run_claude_agent`` call sites and test
patch targets (``patch("evolve.sdk_runner.get_tui", ...)``) continue to
work unchanged.
"""

from evolve.infrastructure.claude_sdk.runner import (  # noqa: F401
    _build_multimodal_prompt,
    run_claude_agent,
    get_tui,
)

# Legacy re-exports — tests also patch these names on ``evolve.sdk_runner``
from evolve.infrastructure.claude_sdk.runtime import (  # noqa: F401
    MODEL,
    MAX_TURNS,
    _patch_sdk_parser,
    _summarise_tool_input,
)
from evolve.infrastructure.filesystem import _runs_base  # noqa: F401

__all__ = [
    "_build_multimodal_prompt",
    "run_claude_agent",
    "get_tui",
    "MODEL",
    "MAX_TURNS",
    "_patch_sdk_parser",
    "_summarise_tool_input",
    "_runs_base",
]
