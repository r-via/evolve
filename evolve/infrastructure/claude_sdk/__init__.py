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

from evolve.infrastructure.claude_sdk.draft_review import (
    _build_draft_prompt,
    _run_draft_claude_agent,
    run_draft_agent,
    _build_review_prompt,
    _run_review_claude_agent,
    run_review_agent,
)

from evolve.infrastructure.claude_sdk.party import (
    _run_party_mode,
    _forever_restart,
)

from evolve.infrastructure.claude_sdk.oneshot_agents import (
    _build_check_section,
    build_validate_prompt,
    build_dry_run_prompt,
    _run_readonly_claude_agent,
    _run_dry_run_claude_agent,
    run_dry_run_agent,
    _run_validate_claude_agent,
    run_validate_agent,
)

# Diff-agent and sync-readme re-exports are LAZY to break the circular
# import: agent_runtime → __init__ → oneshot_agents → sync_readme →
# agent_runtime.  They are resolved on first attribute access below.
_LAZY_REEXPORTS = {
    "build_diff_prompt": ("evolve.diff_agent", "build_diff_prompt"),
    "_run_diff_claude_agent": ("evolve.diff_agent", "_run_diff_claude_agent"),
    "run_diff_agent": ("evolve.diff_agent", "run_diff_agent"),
    "SYNC_README_NO_CHANGES_SENTINEL": (
        "evolve.infrastructure.claude_sdk.sync_readme", "SYNC_README_NO_CHANGES_SENTINEL",
    ),
    "build_sync_readme_prompt": (
        "evolve.infrastructure.claude_sdk.sync_readme", "build_sync_readme_prompt",
    ),
    "_run_sync_readme_claude_agent": (
        "evolve.infrastructure.claude_sdk.sync_readme", "_run_sync_readme_claude_agent",
    ),
    "run_sync_readme_agent": (
        "evolve.infrastructure.claude_sdk.sync_readme", "run_sync_readme_agent",
    ),
}


def __getattr__(name: str):  # noqa: N807 — module-level __getattr__
    if name in _LAZY_REEXPORTS:
        mod_path, attr = _LAZY_REEXPORTS[name]
        import importlib
        mod = importlib.import_module(mod_path)
        value = getattr(mod, attr)
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

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
    "_build_draft_prompt",
    "_run_draft_claude_agent",
    "run_draft_agent",
    "_build_review_prompt",
    "_run_review_claude_agent",
    "run_review_agent",
    "_run_party_mode",
    "_forever_restart",
    "_build_check_section",
    "build_validate_prompt",
    "build_dry_run_prompt",
    "_run_readonly_claude_agent",
    "_run_dry_run_claude_agent",
    "run_dry_run_agent",
    "_run_validate_claude_agent",
    "run_validate_agent",
    "build_diff_prompt",
    "_run_diff_claude_agent",
    "run_diff_agent",
    "SYNC_README_NO_CHANGES_SENTINEL",
    "build_sync_readme_prompt",
    "_run_sync_readme_claude_agent",
    "run_sync_readme_agent",
]
