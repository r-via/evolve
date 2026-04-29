"""evolve.infrastructure.claude_sdk — SDK client, prompt builder, retries."""

from evolve.infrastructure.claude_sdk.runtime import (
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

from evolve.infrastructure.claude_sdk.runner import (
    _build_multimodal_prompt,
    run_claude_agent,
)

from evolve.infrastructure.claude_sdk.prompt_builder import (
    PromptBlocks,
    _load_project_context,
    build_prompt_blocks,
    build_prompt,
)

from evolve.infrastructure.claude_sdk.memory_curation import (
    CURATION_LINE_THRESHOLD,
    CURATION_ROUND_INTERVAL,
    _CURATION_MAX_SHRINK,
    _should_run_curation,
    build_memory_curation_prompt,
    _run_memory_curation_claude_agent,
    run_memory_curation,
)

from evolve.infrastructure.claude_sdk.prompt_diagnostics import (
    _PREV_ATTEMPT_LOG_FMT,
    _MEMORY_WIPED_HEADER_FMT,
    _PRIOR_ROUND_ANOMALY_PATTERNS,
    _detect_prior_round_anomalies,
    build_prev_crash_section,
    build_prior_round_audit_section,
    build_prev_attempt_section,
)

from evolve.infrastructure.claude_sdk.spec_archival import (
    ARCHIVAL_LINE_THRESHOLD,
    ARCHIVAL_ROUND_INTERVAL,
    _ARCHIVAL_MAX_SHRINK,
    _should_run_spec_archival,
    build_spec_archival_prompt,
    _run_spec_archival_claude_agent,
    run_spec_archival,
)

__all__ = [  # noqa: E501
    "CURATION_LINE_THRESHOLD",
    "CURATION_ROUND_INTERVAL",
    "_CURATION_MAX_SHRINK",
    "_should_run_curation",
    "build_memory_curation_prompt",
    "_run_memory_curation_claude_agent",
    "run_memory_curation",
    "MODEL",
    "MAX_TURNS",
    "DRAFT_EFFORT",
    "REVIEW_EFFORT",
    "_TOOL_INPUT_SUMMARY_KEYS",
    "_summarise_tool_input",
    "_patch_sdk_parser",
    "_is_benign_runtime_error",
    "_should_retry_rate_limit",
    "_run_agent_with_retries",
    "_build_multimodal_prompt",
    "run_claude_agent",
    "PromptBlocks",
    "_load_project_context",
    "build_prompt_blocks",
    "build_prompt",
    "_PREV_ATTEMPT_LOG_FMT",
    "_MEMORY_WIPED_HEADER_FMT",
    "_PRIOR_ROUND_ANOMALY_PATTERNS",
    "_detect_prior_round_anomalies",
    "build_prev_crash_section",
    "build_prior_round_audit_section",
    "build_prev_attempt_section",
    "ARCHIVAL_LINE_THRESHOLD",
    "ARCHIVAL_ROUND_INTERVAL",
    "_ARCHIVAL_MAX_SHRINK",
    "_should_run_spec_archival",
    "build_spec_archival_prompt",
    "_run_spec_archival_claude_agent",
    "run_spec_archival",
]
