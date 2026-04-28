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


# NOTE: ``_build_multimodal_prompt`` and ``run_claude_agent`` were
# extracted into ``evolve/sdk_runner.py`` (agent.py split step 6) per
# SPEC § "Hard rule: source files MUST NOT exceed 500 lines" — agent.py
# was 601 lines before the extraction.  Mirrors the leaf-module pattern
# of US-027 / US-030 / US-031 / US-032 / US-033 / US-034 / US-035.  The
# names are re-exported below at module top so existing test patch
# targets (``patch("evolve.agent.run_claude_agent")``,
# ``from evolve.agent import _build_multimodal_prompt``) and the
# ``analyze_and_fix`` internal call site (``return await
# run_claude_agent(...)`` inside ``_run``) continue to bind the
# re-exported name — same lesson as US-028's ``_diag.`` de-aliasing.
from evolve.sdk_runner import (  # noqa: F401 — re-exports for back-compat
    _build_multimodal_prompt,
    run_claude_agent,
)


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
