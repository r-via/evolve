import asyncio
import time
import subprocess
from pathlib import Path

"""Backward-compat shim — real code lives in evolve.infrastructure.claude_sdk.

All symbols re-exported so existing ``from evolve.agent import analyze_and_fix``
etc. continue to work unchanged.
"""

from evolve.infrastructure.claude_sdk.agent import (
    _detect_current_attempt,
    analyze_and_fix,
)
from evolve.infrastructure.claude_sdk.draft_review import (
    _build_draft_prompt,
    _build_review_prompt,
    _run_draft_claude_agent,
    _run_review_claude_agent,
    run_draft_agent,
    run_review_agent,
)
from evolve.infrastructure.claude_sdk.memory_curation import (
    CURATION_LINE_THRESHOLD,
    CURATION_ROUND_INTERVAL,
    _CURATION_MAX_SHRINK,
    _should_run_curation,
    _run_memory_curation_claude_agent,
    build_memory_curation_prompt,
    run_memory_curation,
)
from evolve.infrastructure.claude_sdk.oneshot_agents import (
    SYNC_README_NO_CHANGES_SENTINEL,
    _build_check_section,
    _run_diff_claude_agent,
    _run_dry_run_claude_agent,
    _run_readonly_claude_agent,
    _run_sync_readme_claude_agent,
    _run_validate_claude_agent,
    build_diff_prompt,
    build_dry_run_prompt,
    build_sync_readme_prompt,
    build_validate_prompt,
    run_diff_agent,
    run_dry_run_agent,
    run_sync_readme_agent,
    run_validate_agent,
)
from evolve.infrastructure.claude_sdk.prompt_builder import (
    PromptBlocks,
    _load_project_context,
    build_prompt,
    build_prompt_blocks,
)
from evolve.infrastructure.claude_sdk.prompt_diagnostics import (
    _MEMORY_WIPED_HEADER_FMT,
    _PREV_ATTEMPT_LOG_FMT,
    _PRIOR_ROUND_ANOMALY_PATTERNS,
    _detect_prior_round_anomalies,
    build_prev_attempt_section,
    build_prev_crash_section,
    build_prior_round_audit_section,
)
from evolve.infrastructure.claude_sdk.runner import (
    _build_multimodal_prompt,
    run_claude_agent,
)
from evolve.infrastructure.claude_sdk.runtime import (
    DRAFT_EFFORT,
    EFFORT,
    MAX_TURNS,
    MODEL,
    REVIEW_EFFORT,
    _TOOL_INPUT_SUMMARY_KEYS,
    _is_benign_runtime_error,
    _patch_sdk_parser,
    _run_agent_with_retries,
    _should_retry_rate_limit,
    _summarise_tool_input,
)
from evolve.infrastructure.claude_sdk.spec_archival import (
    ARCHIVAL_LINE_THRESHOLD,
    ARCHIVAL_ROUND_INTERVAL,
    _ARCHIVAL_MAX_SHRINK,
    _should_run_spec_archival,
    _run_spec_archival_claude_agent,
    build_spec_archival_prompt,
    run_spec_archival,
)
from evolve.tui import get_tui

__all__ = [
    "ARCHIVAL_LINE_THRESHOLD",
    "ARCHIVAL_ROUND_INTERVAL",
    "CURATION_LINE_THRESHOLD",
    "CURATION_ROUND_INTERVAL",
    "DRAFT_EFFORT",
    "EFFORT",
    "MAX_TURNS",
    "MODEL",
    "PromptBlocks",
    "REVIEW_EFFORT",
    "SYNC_README_NO_CHANGES_SENTINEL",
    "_ARCHIVAL_MAX_SHRINK",
    "_CURATION_MAX_SHRINK",
    "_MEMORY_WIPED_HEADER_FMT",
    "_PREV_ATTEMPT_LOG_FMT",
    "_PRIOR_ROUND_ANOMALY_PATTERNS",
    "_TOOL_INPUT_SUMMARY_KEYS",
    "_build_check_section",
    "_build_draft_prompt",
    "_build_multimodal_prompt",
    "_build_review_prompt",
    "_detect_current_attempt",
    "_detect_prior_round_anomalies",
    "_is_benign_runtime_error",
    "_load_project_context",
    "_patch_sdk_parser",
    "_run_agent_with_retries",
    "_run_diff_claude_agent",
    "_run_draft_claude_agent",
    "_run_dry_run_claude_agent",
    "_run_memory_curation_claude_agent",
    "_run_readonly_claude_agent",
    "_run_review_claude_agent",
    "_run_spec_archival_claude_agent",
    "_run_sync_readme_claude_agent",
    "_run_validate_claude_agent",
    "_should_retry_rate_limit",
    "_should_run_curation",
    "_should_run_spec_archival",
    "_summarise_tool_input",
    "analyze_and_fix",
    "build_diff_prompt",
    "build_dry_run_prompt",
    "build_memory_curation_prompt",
    "build_prev_attempt_section",
    "build_prev_crash_section",
    "build_prior_round_audit_section",
    "build_prompt",
    "build_prompt_blocks",
    "build_spec_archival_prompt",
    "build_sync_readme_prompt",
    "build_validate_prompt",
    "get_tui",
    "run_claude_agent",
    "run_diff_agent",
    "run_draft_agent",
    "run_dry_run_agent",
    "run_memory_curation",
    "run_review_agent",
    "run_spec_archival",
    "run_sync_readme_agent",
    "run_validate_agent",
]
