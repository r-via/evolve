"""Claude opus agent — reads README as spec, fixes code, tracks improvements."""

from __future__ import annotations

import asyncio  # noqa: F401 — kept for ``patch("evolve.agent.asyncio.run")`` test targets
import re
import shutil
import time  # noqa: F401 — kept for ``patch("evolve.agent.time.sleep")`` test targets
from collections import namedtuple
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


# Historical note: an earlier implementation tried to wire prompt
# caching explicitly via ``ClaudeAgentOptions(system_prompt=[dict, dict])``
# with ``cache_control={"type": "ephemeral"}`` on the first block.
# That's the Anthropic API's native shape, but ``claude-agent-sdk``
# 0.1.50's ``ClaudeAgentOptions.system_prompt`` signature is
# ``str | SystemPromptPreset | None`` — passing a list silently
# produces an empty-system-prompt API call and the model returns
# with zero tool calls.  The underlying Claude Code CLI applies
# prompt caching natively on stable leading prefixes of the string
# system prompt, so explicit wiring is unnecessary and harmful.
# ``PromptBlocks`` and ``build_prompt_blocks`` remain in the codebase
# (below) as a structured way to keep the static/dynamic ordering
# correct — callers concatenate ``.cached + .uncached`` themselves,
# never hand a list to the SDK.  See SPEC § "Prompt caching".
PromptBlocks = namedtuple("PromptBlocks", ["cached", "uncached"])


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


# Prompt section emitted on a debug retry to hand the agent the previous
# attempt's full conversation log — SPEC.md § "Retry continuity" rule (2).
# Kept as a module-level format constant so the single call site in
# ``build_prompt`` and any future test / helper share the same wording.
# Format placeholders: ``{current}`` (current attempt number, int),
# ``{round}`` (round number, int), ``{prior}`` (prior attempt number, int),
# ``{log_path}`` (absolute path to the prior attempt's log file).
_PREV_ATTEMPT_LOG_FMT = (
    "\n## Previous attempt log\n"
    "This is attempt {current} of round {round}. "
    "The full conversation log of attempt {prior} is at:\n\n"
    "  {log_path}\n\n"
    "**Read this file FIRST.** It contains everything the previous "
    "attempt already discovered — the tool calls, the dead ends, the "
    "working hypotheses. Do not redo that investigation. Continue "
    "from where it stopped.\n"
)


# Diagnostic section emitted to the agent when the orchestrator detects a
# >50% memory.md shrink without the ``memory: compaction`` marker in the
# commit message — SPEC.md § "Byte-size sanity gate".  Kept as a
# module-level format constant so the header wording cannot silently drift
# between the prompt builder, the orchestrator's detection path
# (``loop._MEMORY_COMPACTION_MARKER`` / ``_MEMORY_WIPE_THRESHOLD``), and
# any future test / helper.  Single format placeholder: ``{diagnostic}``
# (the raw diagnostic text from ``subprocess_error_round_N.txt``).
_MEMORY_WIPED_HEADER_FMT = (
    "\n## CRITICAL — Previous round silently wiped memory.md\n"
    "The previous round shrank memory.md by more than 50% "
    "without declaring `memory: compaction` in its commit "
    "message. Memory is append-only below ~500 lines; "
    "compaction requires the explicit COMMIT_MSG marker. "
    "Do NOT repeat this — preserve existing entries and "
    "append, do not rewrite or wipe sections.\n"
    "```\n{diagnostic}\n```\n"
)


# NOTE: ``_TOOL_INPUT_SUMMARY_KEYS`` and ``_summarise_tool_input`` were
# hoisted into ``evolve/agent_runtime.py`` (US-030, agent.py split step 1)
# and are re-exported at module top.  See the import block at the top
# of this file.


def _load_project_context(project_dir: Path, spec: str | None = None) -> dict[str, str]:
    """Load shared project context: spec file (README) and improvements.

    Centralises the file-loading logic used by all prompt builders so that
    adding a new file or changing search order only needs to happen once.

    Args:
        project_dir: Root directory of the project.
        spec: Path to the spec file relative to project_dir (e.g. ``"SPEC.md"``
              or ``"docs/specification.md"``).  Defaults to ``README.md``.

    Returns:
        Dictionary with ``readme`` (may be empty) and ``improvements``
        (``None`` when the file does not exist, otherwise its text content).
    """
    # Load spec file
    readme = ""
    if spec:
        p = project_dir / spec
        if p.is_file():
            readme = p.read_text()
    else:
        # Default: try common filenames in order
        for name in ("README.md", "README.rst", "README.txt", "README"):
            p = project_dir / name
            if p.is_file():
                readme = p.read_text()
                break

    # Load improvements
    improvements_path = _runs_base(project_dir) / "improvements.md"
    improvements = improvements_path.read_text() if improvements_path.is_file() else None

    return {"readme": readme, "improvements": improvements}


# Signature patterns for programmatic anomaly detection in the previous
# round's conversation log.  Kept as module-level constants so the list
# is easy to extend and the detection + prompt rendering stay aligned.
# See SPEC.md § "Prior round audit".
_PRIOR_ROUND_ANOMALY_PATTERNS: tuple[tuple[str, str], ...] = (
    ("watchdog stall / SIGKILL", r"stalled \(\d+s without output\) — killing subprocess"),
    ("subprocess killed by signal", r"Round \d+ failed \(exit -\d+\)"),
    ("pre-check TIMEOUT", r"pre-check TIMEOUT after \d+s"),
    ("frame capture error", r"Frame capture failed for [\w_]+: not well-formed"),
    ("circuit breaker tripped (exit 4)", r"deterministic loop detected"),
)


def _detect_prior_round_anomalies(
    run_dir: Path | None, round_num: int
) -> list[str]:
    """Scan artifacts of round ``round_num - 1`` for anomaly signals.

    Returns a list of short human-readable tags ("watchdog stall /
    SIGKILL", "post-fix check FAIL", etc.) describing any abnormal
    behaviour in the prior round.  Used by ``build_prompt`` to render a
    dedicated ``## Prior round audit`` section that forces the agent to
    investigate before proceeding with the current target — SPEC.md §
    "Prior round audit".

    Non-anomalous prior rounds return an empty list and the section is
    omitted from the prompt entirely.
    """
    if not run_dir or round_num <= 1:
        return []
    rdir = Path(run_dir)
    prev = round_num - 1
    anomalies: list[str] = []

    # Signal 1 — orchestrator-level diagnostic exists.  Already surfaced
    # via ``prev_crash_section``; tallied here so the audit shows the
    # full picture instead of silently overlapping with prev_crash.
    if (rdir / f"subprocess_error_round_{prev}.txt").is_file():
        anomalies.append("orchestrator diagnostic present")

    # Signal 2 — post-fix check reported FAIL.
    check_file = rdir / f"check_round_{prev}.txt"
    if check_file.is_file():
        try:
            text = check_file.read_text()
            if "post-fix check: FAIL" in text:
                anomalies.append("post-fix check FAIL")
        except OSError:
            pass

    # Signals 3..N — regex matches in the prior round's conversation log.
    convo_file = rdir / f"conversation_loop_{prev}.md"
    if convo_file.is_file():
        try:
            text = convo_file.read_text()
            for label, pattern in _PRIOR_ROUND_ANOMALY_PATTERNS:
                if re.search(pattern, text):
                    anomalies.append(label)
        except OSError:
            pass

    return anomalies


def build_prompt_blocks(
    project_dir: Path,
    check_output: str = "",
    check_cmd: str | None = None,
    allow_installs: bool = False,
    run_dir: Path | None = None,
    spec: str | None = None,
    round_num: int = 1,
    yolo: bool | None = None,
    check_timeout: int = 20,
) -> PromptBlocks:
    """Build a two-block prompt for the opus agent (prompt caching).

    Returns a :class:`PromptBlocks` with ``cached`` (static per session
    — system template + SPEC/README) and ``uncached`` (per-round
    variable — check results, memory, attempt marker, prior audit,
    crash logs).  Callers concatenate ``cached + uncached`` and pass
    the result as a single-string ``system_prompt`` to the SDK; the
    Claude Code CLI handles prompt caching natively on the stable
    leading prefix across calls.  Passing a list-of-dicts with
    ``cache_control`` to the SDK is not accepted (signature is
    ``str | SystemPromptPreset | None``) and would silently produce
    an empty-system-prompt call — see SPEC.md § "Prompt caching".

    Keeping the static portion first (and identical across rounds)
    is what makes the native cache hit reliably.

    Args:
        project_dir: Root directory of the project being evolved.
        check_output: Output from the most recent check command run.
        check_cmd: Shell command used to verify the project (e.g. 'pytest').
        allow_installs: If True, allow improvements tagged [needs-package].
        run_dir: Session run directory containing round artifacts.
        spec: Path to the spec file relative to project_dir (default: README.md).
        round_num: Current evolution round number (used for stuck-loop detection).
        yolo: Deprecated alias for *allow_installs*. Will be removed in a future version.
        check_timeout: Maximum seconds for the check command.

    Returns:
        A PromptBlocks(cached, uncached) named tuple.
    """
    if yolo is not None:
        allow_installs = yolo
    # Load system prompt — prompts/ lives at project root (two levels up
    # from evolve/agent.py: evolve/ → project root).
    prompt_path = Path(__file__).resolve().parent.parent / "prompts" / "system.md"
    # Project can override with its own prompts/evolve-system.md
    project_prompt = project_dir / "prompts" / "evolve-system.md"
    if project_prompt.is_file():
        prompt_path = project_prompt

    system_prompt = prompt_path.read_text() if prompt_path.is_file() else ""

    ctx = _load_project_context(project_dir, spec=spec)
    readme = ctx["readme"]
    improvements = ctx["improvements"]

    # Current target — skip [needs-package] items unless --allow-installs
    current = None
    if improvements:
        for line in improvements.splitlines():
            m = re.match(r"^- \[ \] (.+)$", line.strip())
            if m:
                text = m.group(1)
                if not allow_installs and _is_needs_package(text):
                    continue
                current = text
                break

    # Memory
    memory_path = _runs_base(project_dir) / "memory.md"
    memory = memory_path.read_text().strip() if memory_path.is_file() else ""

    # Previous check results
    prev_check = ""
    if run_dir:
        for f in sorted(Path(run_dir).glob("check_round_*.txt"), key=lambda p: int(re.search(r'_(\d+)\.txt$', p.name).group(1)), reverse=True):
            prev_check = f.read_text()
            break

    # Previous round subprocess crash logs (orchestrator-level errors)
    prev_crash = ""
    prev_crash_file = None
    if run_dir:
        for f in sorted(Path(run_dir).glob("subprocess_error_round_*.txt"), key=lambda p: int(re.search(r'_(\d+)\.txt$', p.name).group(1)), reverse=True):
            prev_crash = f.read_text()
            prev_crash_file = f
            break

    # Determine the current attempt number for this run.  Uses the same
    # helper as ``analyze_and_fix`` so per-attempt log naming and the Phase 1
    # escape-hatch banner agree on which attempt this is.
    current_attempt = _detect_current_attempt(run_dir, round_num)

    allow_installs_note = ""
    if not allow_installs:
        allow_installs_note = """
CONSTRAINT: Do NOT add new binaries or pip/npm packages. If an improvement requires
a new dependency, add it to .evolve/runs/improvements.md with the tag [needs-package] and
leave it unchecked. The operator must re-run with --allow-installs to allow it."""

    rdir = str(run_dir or ".evolve/runs")

    # Interpolate using str.replace() instead of .format() to avoid KeyError
    # when the template (or project-specific override) contains literal curly braces
    # (e.g. JSON examples, Rust code, Go generics).
    from evolve.orchestrator import WATCHDOG_TIMEOUT
    # Shared cross-round files (``improvements.md``, ``memory.md``)
    # live under ``_runs_base(project_dir)`` — canonical location is
    # ``.evolve/runs/`` per SPEC § "The .evolve/ directory" with a
    # legacy ``runs/`` fallback during migration.  Session-local
    # files (``COMMIT_MSG``, ``CONVERGED``, ``conversation_loop_N.md``,
    # ``RESTART_REQUIRED``, etc.) live under ``{run_dir}``.  The
    # placeholders below let the prompt reference both
    # unambiguously so the agent cannot accidentally write
    # ``improvements.md`` inside a session directory.
    runs_base_str = str(_runs_base(project_dir))
    system_prompt = system_prompt.replace("{project_dir}", str(project_dir))
    system_prompt = system_prompt.replace("{run_dir}", rdir)
    system_prompt = system_prompt.replace("{runs_base}", runs_base_str)
    # Support both old and new placeholder names for backward compatibility
    system_prompt = system_prompt.replace("{yolo_note}", allow_installs_note)
    system_prompt = system_prompt.replace("{allow_installs_note}", allow_installs_note)
    system_prompt = system_prompt.replace("{watchdog_timeout}", str(WATCHDOG_TIMEOUT))
    system_prompt = system_prompt.replace("{check_timeout}", str(check_timeout))
    system_prompt = system_prompt.replace("{round_num}", str(round_num))
    system_prompt = system_prompt.replace("{prev_round_1}", str(round_num - 1))
    system_prompt = system_prompt.replace("{prev_round_2}", str(round_num - 2))

    # Phase 1 escape hatch: attempt-marker banner. Injected into system.md at
    # the `{attempt_marker}` placeholder so the agent knows which attempt it
    # is on and whether the Phase 1 escape hatch is currently permitted.
    if current_attempt >= 3:
        attempt_marker = (
            "**>>> CURRENT ATTEMPT: 3 of 3 (FINAL RETRY) <<<**\n"
            "The Phase 1 escape hatch is NOW PERMITTED if the three guard\n"
            "conditions above all hold. Evaluate the guard honestly:\n"
            "  (1) You are on attempt 3 — CONFIRMED by this banner.\n"
            "  (2) Are Phase 1 errors still present?\n"
            "  (3) Do the failing tests touch NONE of the files named in\n"
            "      your current improvement target?\n"
            "If and only if all three hold, apply the four actions (a-d)\n"
            "and proceed with your Phase 3 target. Otherwise, continue\n"
            "normal Phase 1 debugging.\n"
        )
    elif current_attempt == 2:
        attempt_marker = (
            "**CURRENT ATTEMPT: 2 of 3** — Standard Phase 1 applies. The\n"
            "Phase 1 escape hatch is NOT permitted on attempt 2; it unlocks\n"
            "only on the final retry (attempt 3).\n"
        )
    else:
        attempt_marker = (
            "**CURRENT ATTEMPT: 1 of 3** — Standard Phase 1 applies. The\n"
            "Phase 1 escape hatch is NOT permitted on the first attempt.\n"
        )
    # Substitute attempt_marker with empty string in the template so the
    # cached block is deterministic per session.  The real attempt_marker
    # text goes into the uncached block.  See SPEC.md § "Prompt caching".
    system_prompt = system_prompt.replace("{attempt_marker}", "")

    # Build sections
    readme_section = f"## README (specification)\n{readme}" if readme else "## README\n(no README found)"
    improvements_section = f"## runs/improvements.md (current state)\n{improvements}" if improvements else "## runs/improvements.md\n(does not exist yet — you must create it)"
    target_section = f"Current target improvement: {current}" if current else "No improvements yet — create initial runs/improvements.md based on your analysis."
    memory_section = f"\n## Memory (cumulative learning log — read, then append during your turn)\n{memory}\n" if memory else ""
    prev_check_section = f"\n## Previous round check results\n{prev_check}\n" if prev_check else ""
    if prev_crash:
        if "MEMORY WIPED" in prev_crash:
            prev_crash_section = _MEMORY_WIPED_HEADER_FMT.format(diagnostic=prev_crash)
        elif "SCOPE CREEP" in prev_crash:
            # Rebuild + implement in the same round — the Phase 2
            # rebuild touched improvements.md AND the commit also
            # modified non-improvements files.  The retry must split
            # the work: commit ONLY the improvements.md rebuild this
            # round; any implementation happens in the NEXT round's
            # fresh agent.  See SPEC § "improvements.md" + Phase 2
            # gate in prompts/system.md.
            prev_crash_section = (
                f"\n## CRITICAL — Scope creep: rebuild mixed with implementation\n"
                f"The previous round added one or more new ``[ ]`` items "
                f"to ``improvements.md`` AND modified non-improvements "
                f"files (code / tests / docs) in the same commit.  That "
                f"mixes two round kinds: Phase 2 backlog rebuild vs "
                f"Phase 3 implementation.  Each is a round by itself.\n\n"
                f"**This attempt MUST split the work:**\n\n"
                f"1. ``git reset HEAD~1`` (if the commit went through; "
                f"otherwise skip).\n"
                f"2. Stage ONLY the ``improvements.md`` change (the "
                f"rebuild / new US item).\n"
                f"3. Discard the code / test / doc edits from the "
                f"working tree — the NEXT round's fresh agent will "
                f"re-derive them from the rebuilt backlog.\n"
                f"4. Write ``COMMIT_MSG`` with ``chore(spec): rebuild "
                f"backlog after spec change`` (or similar) and stop.\n\n"
                f"Do NOT attempt to implement the new US item in this "
                f"retry.  The rebuild round's purpose is to produce a "
                f"clean commit boundary between planning and coding so "
                f"the audit trail shows them as distinct actions.\n"
                f"```\n{prev_crash}\n```\n"
            )
        elif "BACKLOG DRAINED" in prev_crash:
            # The previous round legitimately had nothing to implement
            # (every ``[ ]`` item already checked off) but the agent
            # stopped short of Phase 4 — no CONVERGED written, no
            # commit body, no edits.  The retry must NOT go fishing
            # for something to do; the correct next step is Phase 4
            # (verify README claims, then write CONVERGED).
            prev_crash_section = (
                f"\n## CRITICAL — Backlog drained, CONVERGED skipped\n"
                f"The previous round's improvements.md has zero unchecked "
                f"``[ ]`` items, yet you did not write ``CONVERGED``.  The "
                f"round was not a failure — you had nothing to implement — "
                f"but stopping without writing ``CONVERGED`` triggers the "
                f"zero-progress retry loop.\n\n"
                f"**This attempt MUST go straight to Phase 4:**\n\n"
                f"1. Re-read the spec (README.md or ``--spec``) line by line.\n"
                f"2. For EACH section / claim, confirm the implementation "
                f"   actually exists and works.  Do NOT trust the ``[x]`` "
                f"   checkboxes alone — walk the spec.\n"
                f"3. If every claim checks out → write "
                f"   ``{{run_dir}}/CONVERGED`` with a one-line justification "
                f"   per documented gate.\n"
                f"4. If ONE claim is not yet implemented → add exactly one "
                f"   new ``[ ]`` US item for it (Winston → John → final-draft "
                f"   pipeline per SPEC § 'Item format'), leave the backlog "
                f"   non-empty, and skip CONVERGED (the next round picks it "
                f"   up).\n\n"
                f"Do NOT fabricate a filler improvement just to make the "
                f"round look productive — that is worse than not converging.\n"
                f"```\n{prev_crash}\n```\n"
            )
        elif "BACKLOG VIOLATION" in prev_crash:
            # Backlog discipline rule 1 (empty-queue gate) — see SPEC.md §
            # "Backlog discipline".  The previous attempt added a new `- [ ]`
            # item to improvements.md while at least one other `- [ ]` item
            # was still pending.  Tell the agent to remove the freshly added
            # item(s) and let the queue drain before adding anything new.
            prev_crash_section = (
                f"\n## CRITICAL — Backlog discipline violation: "
                f"new item added while queue non-empty\n"
                f"The previous attempt added one or more new `- [ ]` items "
                f"to runs/improvements.md while at least one other `- [ ]` "
                f"item was still pending.  Per SPEC.md § 'Backlog discipline' "
                f"rule 1 (empty-queue gate), new items may ONLY be added when "
                f"the queue is genuinely empty.  This attempt MUST: (1) "
                f"remove the freshly added unchecked item(s) from "
                f"runs/improvements.md, (2) keep working the existing "
                f"current target, and (3) NOT add any replacement item until "
                f"every other `- [ ]` line is checked off.\n"
                f"```\n{prev_crash}\n```\n"
            )
        elif "MAX_TURNS" in prev_crash:
            # SDK subtype=error_max_turns — the agent hit the turn cap
            # before finishing.  Per SPEC § "Authoritative termination
            # signal from the SDK": retry with "fix-only, defer
            # investigation" header.
            prev_crash_section = (
                f"\n## CRITICAL — Agent hit max_turns cap (error_max_turns)\n"
                f"The previous attempt exhausted the SDK turn budget without "
                f"finishing.  This means the target is too large for a single "
                f"round or the agent spent too many turns on reconnaissance.\n\n"
                f"**This attempt MUST:**\n\n"
                f"1. Start with Edit/Write immediately — no Read/Grep exploration.\n"
                f"2. Fix only the most critical remaining issue, then commit.\n"
                f"3. If the target cannot be finished in one pass, commit a "
                f"partial fix with a clear COMMIT_MSG describing what was done "
                f"and what remains.\n"
                f"4. Do NOT defer to the next round by doing nothing — make at "
                f"least one meaningful edit.\n"
                f"```\n{prev_crash}\n```\n"
            )
        elif "SDK ERROR" in prev_crash:
            # SDK subtype=error_during_execution — the agent hit an SDK
            # error.  Surface it verbatim per SPEC § "Authoritative
            # termination signal from the SDK".
            prev_crash_section = (
                f"\n## CRITICAL — Agent stopped with SDK execution error\n"
                f"The previous attempt's Claude Agent SDK session ended with "
                f"``subtype=error_during_execution``.  The SDK error is in the "
                f"diagnostic below.  Diagnose and work around it — typically "
                f"a tool permission issue or a malformed tool input.\n"
                f"```\n{prev_crash}\n```\n"
            )
        elif "NO PROGRESS" in prev_crash:
            prev_crash_section = (
                f"\n## CRITICAL — Previous round made NO PROGRESS\n"
                f"The previous round ended without making meaningful changes. "
                f"Start with Edit/Write immediately and defer exploration.\n"
                f"```\n{prev_crash}\n```\n"
            )
        elif "REVIEW:" in prev_crash:
            # Adversarial review verdict routing (SPEC § "Adversarial round
            # review (Phase 3.6)").  The previous attempt's adversarial
            # review produced HIGH findings that must be addressed before
            # the round can proceed.
            prev_crash_section = (
                f"\n## CRITICAL — Previous attempt failed adversarial review\n"
                f"The adversarial reviewer (Zara) found HIGH-severity findings "
                f"in the previous attempt's work. You MUST address each HIGH "
                f"finding listed below before re-committing. Read the review "
                f"file (`review_round_N.md` in the run directory) for full "
                f"context, then fix each finding and re-run the adversarial "
                f"review.\n"
                f"```\n{prev_crash}\n```\n"
            )
        elif "FILE TOO LARGE" in prev_crash:
            prev_crash_section = (
                f"\n## CRITICAL — File too large\n"
                f"The previous round left one or more ``evolve/*.py`` or "
                f"``tests/*.py`` files over the 500-line hard limit "
                f"(SPEC.md § 'Hard rule: source files MUST NOT exceed "
                f"500 lines').  Your **primary task this round** is to "
                f"split the largest offending file into smaller modules "
                f"(each ≤ 500 lines).  Extract a coherent sub-"
                f"responsibility into its own module, update imports, and "
                f"verify tests still pass.\n"
                f"```\n{prev_crash}\n```\n"
            )
        elif "PREMATURE CONVERGED" in prev_crash:
            # Convergence-gate orchestrator backstop (SPEC.md § "Convergence").
            # The previous round wrote CONVERGED but the orchestrator's
            # independent re-verification of the two documented gates
            # rejected it. The agent MUST address the listed gate
            # violations (rebuild stale backlog, or resolve unresolved
            # `- [ ]` items) before attempting to write CONVERGED again.
            prev_crash_section = (
                f"\n## CRITICAL — Premature CONVERGED\n"
                f"The previous round wrote CONVERGED but the orchestrator's "
                f"convergence-gate backstop found unresolved gates "
                f"(SPEC.md § 'Convergence'). Do NOT write CONVERGED again "
                f"until the listed gate violation(s) below are resolved: "
                f"rebuild any ``[stale: spec changed]`` items from the "
                f"current spec, and close every unchecked ``- [ ]`` item "
                f"(or tag it with ``[needs-package]`` or "
                f"``[blocked: ...]``).\n"
                f"```\n{prev_crash}\n```\n"
            )
        else:
            prev_crash_section = f"\n## CRITICAL — Previous round CRASHED (fix this first!)\n```\n{prev_crash}\n```\n"
    else:
        prev_crash_section = ""

    # Prior round audit — scan the previous round's artifacts for
    # anomaly signals (watchdog stalls, SIGKILL, pre-check timeouts,
    # frame capture errors, circuit-breaker trips, post-fix FAIL).  When
    # any signal is present, surface a dedicated section at the top of
    # the prompt instructing the agent to investigate those anomalies
    # before touching the backlog.  See SPEC.md § "Prior round audit".
    prior_anomalies = _detect_prior_round_anomalies(run_dir, round_num)
    if prior_anomalies:
        prev = round_num - 1
        prior_round_audit_section = (
            f"\n## Prior round audit — Round {prev} showed anomalies\n"
            f"Before doing ANY backlog work this round, investigate and "
            f"resolve the anomalies listed below.  They are programmatic "
            f"signals from round {prev}'s artifacts "
            f"(``subprocess_error_round_{prev}.txt``, "
            f"``check_round_{prev}.txt``, "
            f"``conversation_loop_{prev}.md``).\n\n"
            f"**Anomalies detected ({len(prior_anomalies)}):**\n"
            + "\n".join(f"- {a}" for a in prior_anomalies)
            + "\n\n"
            f"**Action required (in order):**\n"
            f"1. Open the three artifacts above and read the relevant "
            f"excerpts.\n"
            f"2. Identify the root cause of each anomaly.  Examples: a "
            f"flaky test hanging pytest, a bad import breaking a "
            f"subprocess, a non-deterministic fixture exceeding the "
            f"watchdog, a malformed subprocess output corrupting frame "
            f"capture.\n"
            f"3. Apply the fix NOW — edit the offending code/test/"
            f"config before touching the current improvement target.  "
            f"Commit the audit fix with a ``fix(audit):`` prefix in "
            f"COMMIT_MSG so the round history shows that the round's "
            f"primary work was prior-round remediation.\n"
            f"4. Only after the audit fix is committed and verified may "
            f"you proceed with the current target.\n"
            f"5. If an anomaly is genuinely unfixable (e.g. a known "
            f"flaky external service) and does not block progress, "
            f"document it in ``runs/memory.md`` under a new "
            f"``## Known anomalies`` section with the signature and "
            f"why it is being deferred — so future rounds don't "
            f"re-investigate the same known-benign signal.\n"
        )
    else:
        prior_round_audit_section = ""

    # Retry continuity: when this run is a debug retry (attempt > 1), surface
    # the previous attempt's full conversation log so the agent can continue
    # from where it stopped instead of restarting the investigation.  The
    # diagnostic in `prev_crash_section` is only the last 3000 chars of
    # output; the full per-attempt log holds every tool call, dead end, and
    # working hypothesis.  See SPEC.md § "Retry continuity" rule (2).
    prev_attempt_section = ""
    if current_attempt > 1 and run_dir:
        prior_k = current_attempt - 1
        prior_log = Path(run_dir) / f"conversation_loop_{round_num}_attempt_{prior_k}.md"
        if prior_log.is_file():
            # Skip the prior-attempt retry-continuity section when the
            # prior log is trivially empty.  The ``_PREV_ATTEMPT_LOG_FMT``
            # template tells the agent to "Read this file FIRST" — a
            # dutiful instruction that turns into noise when the file
            # contains only a header or no tool calls at all (e.g. the
            # prior attempt was killed by scope-creep detection or the
            # circuit breaker before it produced any reusable trace).
            # The user-visible symptom was every round starting with
            # "Prior attempt log is empty (1 line). No useful context
            # to reuse." — pure prompt overhead.
            #
            # Heuristic: a log under 500 bytes OR with no tool-call
            # markers (``**Read**:``, ``**Edit**:``, ``**Bash**:`` …)
            # is trivially empty and skipped.
            try:
                content = prior_log.read_text()
            except OSError:
                content = ""
            has_tool_calls = any(
                marker in content for marker in (
                    "**Read**:", "**Edit**:", "**Write**:",
                    "**Bash**:", "**Grep**:", "**Glob**:",
                )
            )
            if len(content) >= 500 and has_tool_calls:
                prev_attempt_section = _PREV_ATTEMPT_LOG_FMT.format(
                    current=current_attempt,
                    round=round_num,
                    prior=prior_k,
                    log_path=prior_log,
                )

    check_section = ""
    if check_cmd and check_output:
        check_section = (
            f"\n## Check command: `{check_cmd}`\n"
            f"Run this command after every change to verify your fixes work.\n"
            f"\n### Latest check output:\n```\n{check_output}\n```\n"
        )
    elif check_cmd:
        check_section = (
            f"\n## Check command: `{check_cmd}`\n"
            f"Run this command after every change to verify your fixes work.\n"
        )
    else:
        check_section = (
            f"\n## No check command configured\n"
            f"Run the project's main commands manually after each fix to verify they work.\n"
        )

    # --- Cached block: static per session (system template + SPEC/README) ---
    cached = f"""\
{system_prompt}

{readme_section}"""

    # --- Uncached block: per-round variable content ---
    uncached = f"""\
{attempt_marker}

{improvements_section}

{target_section}
{prior_round_audit_section}
{prev_crash_section}
{prev_attempt_section}
{memory_section}
{prev_check_section}
{check_section}"""

    return PromptBlocks(cached=cached, uncached=uncached)


def build_prompt(
    project_dir: Path,
    check_output: str = "",
    check_cmd: str | None = None,
    allow_installs: bool = False,
    run_dir: Path | None = None,
    spec: str | None = None,
    round_num: int = 1,
    yolo: bool | None = None,
    check_timeout: int = 20,
) -> str:
    """Build the system prompt for the opus agent from project context.

    Backward-compatible wrapper around :func:`build_prompt_blocks` that
    returns a single concatenated string.

    Args:
        project_dir: Root directory of the project being evolved.
        check_output: Output from the most recent check command run.
        check_cmd: Shell command used to verify the project (e.g. 'pytest').
        allow_installs: If True, allow improvements tagged [needs-package].
        run_dir: Session run directory containing round artifacts.
        spec: Path to the spec file relative to project_dir (default: README.md).
        round_num: Current evolution round number (used for stuck-loop detection).
        yolo: Deprecated alias for *allow_installs*. Will be removed in a future version.
        check_timeout: Maximum seconds for the check command.

    Returns:
        The fully interpolated prompt string.
    """
    blocks = build_prompt_blocks(
        project_dir, check_output, check_cmd, allow_installs, run_dir,
        spec=spec, round_num=round_num, yolo=yolo, check_timeout=check_timeout,
    )
    return f"{blocks.cached}\n\n{blocks.uncached}"


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


def _build_check_section(check_cmd: str | None, check_output: str) -> str:
    """Build the check command section used by read-only prompt builders.

    Shared by :func:`build_validate_prompt` and :func:`build_dry_run_prompt`
    to eliminate duplicated conditional logic for rendering check command
    output.

    Args:
        check_cmd: Shell command used to verify the project (e.g. 'pytest').
        check_output: Output from the most recent check command run.

    Returns:
        A Markdown section string (may be empty if no check command).
    """
    if check_cmd and check_output:
        return (
            f"\n## Check command: `{check_cmd}`\n"
            f"\n### Latest check output:\n```\n{check_output}\n```\n"
        )
    elif check_cmd:
        return f"\n## Check command: `{check_cmd}` (not yet run)\n"
    return ""


def build_validate_prompt(
    project_dir: Path,
    check_output: str = "",
    check_cmd: str | None = None,
    run_dir: Path | None = None,
    spec: str | None = None,
) -> str:
    """Build the prompt for validation (spec compliance) mode.

    The agent is instructed to check every README claim against the codebase
    and produce a ``validate_report.md`` with pass/fail per claim and an
    overall compliance percentage.

    Args:
        project_dir: Root directory of the project being validated.
        check_output: Output from the most recent check command run.
        check_cmd: Shell command used to verify the project.
        run_dir: Session run directory where the report will be written.
        spec: Path to the spec file relative to project_dir (default: README.md).

    Returns:
        The fully assembled prompt string.
    """
    ctx = _load_project_context(project_dir, spec=spec)
    readme = ctx["readme"]
    improvements = ctx["improvements"] or "(none)"

    rdir = str(run_dir or ".evolve/runs")

    check_section = _build_check_section(check_cmd, check_output)

    return f"""\
You are a spec compliance validation agent. You are running in VALIDATE mode.
You MUST NOT modify any project files. Your only writable action is to
create `{rdir}/validate_report.md`.

Your task: systematically verify every claim in the README specification
against the actual codebase. For each claim, determine if it is implemented
and functional.

Use Read, Grep, and Glob tools to examine the codebase. Do NOT use Edit, Write, or Bash.

At the end, write `{rdir}/validate_report.md` with the following format:

# Validation Report

## Claims

For EACH distinct claim, feature, or requirement in the README, write one line:
- ✅ **Claim description** — verified in `file.py` (brief evidence)
- ❌ **Claim description** — not implemented / broken (brief explanation)

## Summary

- **Total claims**: N
- **Passed**: N (✅)
- **Failed**: N (❌)
- **Compliance**: XX%

## Gaps

For each ❌ item, describe what is missing with file references.

IMPORTANT: Be thorough. Check every section of the README. A claim passes
only if you can find concrete evidence in the code. Do not assume — verify.

## README (specification)
{readme if readme else "(no README found)"}

## Current improvements.md
{improvements}
{check_section}"""


def build_dry_run_prompt(
    project_dir: Path,
    check_output: str = "",
    check_cmd: str | None = None,
    run_dir: Path | None = None,
    spec: str | None = None,
) -> str:
    """Build the prompt for dry-run (read-only) analysis mode.

    The agent is instructed to analyse the project without modifying any
    files and to write a ``dry_run_report.md`` summarising identified gaps,
    proposed improvements, and estimated rounds to convergence.

    Args:
        project_dir: Root directory of the project being analysed.
        check_output: Output from the most recent check command run.
        check_cmd: Shell command used to verify the project.
        run_dir: Session run directory where the report will be written.
        spec: Path to the spec file relative to project_dir (default: README.md).

    Returns:
        The fully assembled prompt string.
    """
    ctx = _load_project_context(project_dir, spec=spec)
    readme = ctx["readme"]
    improvements = ctx["improvements"] or "(none)"

    rdir = str(run_dir or ".evolve/runs")

    check_section = _build_check_section(check_cmd, check_output)

    return f"""\
You are a read-only analysis agent. You are running in DRY RUN mode.
You MUST NOT modify any project files. Your only writable action is to
create `{rdir}/dry_run_report.md`.

Analyse the project against its README specification. Use Read, Grep, and
Glob tools to examine the codebase. Do NOT use Edit, Write, or Bash.

At the end, write `{rdir}/dry_run_report.md` with the following sections:

# Dry Run Report

## Identified Gaps
List every gap between the README specification and the current implementation.

## Proposed Improvements
For each gap, describe what improvement would be added to `improvements.md`.
Use the same format: `- [ ] [functional] description` or `- [ ] [performance] description`.

## Estimated Rounds
Estimate how many evolution rounds would be needed to reach convergence.

## README (specification)
{readme if readme else "(no README found)"}

## Current improvements.md
{improvements}
{check_section}"""


async def _run_readonly_claude_agent(
    prompt: str,
    project_dir: Path,
    run_dir: Path,
    *,
    log_filename: str,
    log_header: str,
    disallowed_tools: list[str] | None = None,
) -> None:
    """Shared helper for running the Claude agent in read-only modes.

    Handles SDK streaming, message deduplication, tool-call logging, and TUI
    updates.  Used by both dry-run and validate modes.

    Args:
        prompt: The assembled prompt for the agent.
        project_dir: Root directory of the project (used as cwd).
        run_dir: Session directory for the conversation log and report.
        log_filename: Name of the conversation log file (e.g. ``dry_run_conversation.md``).
        log_header: Markdown header written at the top of the log file.
        disallowed_tools: Tools to block.  Defaults to read-only set
            (Edit, Bash, Task, Agent, WebSearch, WebFetch).
    """
    _patch_sdk_parser()
    from claude_agent_sdk import query, ClaudeAgentOptions, AssistantMessage, ResultMessage

    if disallowed_tools is None:
        disallowed_tools = ["Edit", "Bash", "Task", "Agent", "WebSearch", "WebFetch"]

    options = ClaudeAgentOptions(
        permission_mode="bypassPermissions",
        model=MODEL,
        max_turns=MAX_TURNS,
        cwd=str(project_dir),
        disallowed_tools=disallowed_tools,
        include_partial_messages=True,
        effort=EFFORT,
    )

    log_path = run_dir / log_filename
    ui = get_tui()

    with open(log_path, "w", buffering=1) as log:
        log.write(f"# {log_header}\n\n")

        seen_tool_ids: set[str] = set()
        seen_text_hashes: set[int] = set()
        tools_used = 0

        try:
            async for message in query(prompt=prompt, options=options):
                if message is None:
                    continue
                if isinstance(message, (AssistantMessage, ResultMessage)):
                    if not hasattr(message, "content") or not message.content:
                        continue
                    for block in message.content:
                        if hasattr(block, "text") and block.text.strip():
                            h = hash(block.text)
                            if h in seen_text_hashes:
                                continue
                            seen_text_hashes.add(h)
                            log.write(f"\n{block.text}\n")
                            ui.agent_text(block.text)
                        elif hasattr(block, "name"):
                            block_id = getattr(block, "id", None)
                            if block_id and block_id in seen_tool_ids:
                                continue
                            if block_id:
                                seen_tool_ids.add(block_id)
                            tools_used += 1
                            tool_name = block.name
                            tool_input = ""
                            if hasattr(block, "input") and block.input:
                                inp = block.input
                                if isinstance(inp, dict):
                                    tool_input = inp.get("file_path", inp.get("pattern", str(inp)[:100]))
                                else:
                                    tool_input = str(inp)[:100]
                            log.write(f"\n**{tool_name}**: `{tool_input}`\n")
                            ui.agent_tool(tool_name, tool_input)
        except Exception as e:
            log.write(f"\n> SDK error: {e}\n")

        log.write(f"\n---\n\n**Done**: {tools_used} tool calls\n")

    ui.agent_done(tools_used, str(log_path))


async def _run_dry_run_claude_agent(
    prompt: str,
    project_dir: Path,
    run_dir: Path,
) -> None:
    """Run the Claude agent in dry-run mode with restricted tools.

    Thin wrapper around :func:`_run_readonly_claude_agent` for backward
    compatibility.

    Args:
        prompt: The dry-run analysis prompt.
        project_dir: Root directory of the project (used as cwd).
        run_dir: Session directory for the conversation log and report.
    """
    await _run_readonly_claude_agent(
        prompt, project_dir, run_dir,
        log_filename="dry_run_conversation.md",
        log_header="Dry Run Analysis",
    )


# NOTE: ``_run_agent_with_retries`` was hoisted into
# ``evolve/agent_runtime.py`` (US-030, agent.py split step 1) and is
# re-exported at module top.


def run_dry_run_agent(
    project_dir: Path,
    check_output: str = "",
    check_cmd: str | None = None,
    run_dir: Path | None = None,
    max_retries: int = 5,
    spec: str | None = None,
) -> None:
    """Run the agent in dry-run (read-only) analysis mode.

    Builds a dry-run prompt and invokes the agent with write-related tools
    disabled.  Includes the same retry logic as ``analyze_and_fix``.

    Args:
        project_dir: Root directory of the project being analysed.
        check_output: Output from the most recent check command.
        check_cmd: Shell command used to verify the project.
        run_dir: Session run directory for conversation logs and report.
        max_retries: Maximum SDK call attempts on rate-limit errors.
        spec: Path to the spec file relative to project_dir (default: README.md).
    """
    rdir = run_dir or _runs_base(project_dir)
    rdir.mkdir(parents=True, exist_ok=True)

    prompt = build_dry_run_prompt(project_dir, check_output, check_cmd, rdir, spec=spec)

    _run_agent_with_retries(
        lambda: _run_dry_run_claude_agent(prompt, project_dir, rdir),
        fail_label="Dry-run agent",
        max_retries=max_retries,
    )


async def _run_validate_claude_agent(
    prompt: str,
    project_dir: Path,
    run_dir: Path,
) -> None:
    """Run the Claude agent in validation mode with restricted tools.

    Thin wrapper around :func:`_run_readonly_claude_agent` for backward
    compatibility.

    Args:
        prompt: The validation prompt.
        project_dir: Root directory of the project (used as cwd).
        run_dir: Session directory for the conversation log and report.
    """
    await _run_readonly_claude_agent(
        prompt, project_dir, run_dir,
        log_filename="validate_conversation.md",
        log_header="Validation Analysis",
    )


def run_validate_agent(
    project_dir: Path,
    check_output: str = "",
    check_cmd: str | None = None,
    run_dir: Path | None = None,
    max_retries: int = 5,
    spec: str | None = None,
) -> None:
    """Run the agent in validation (spec compliance) mode.

    Builds a validation prompt and invokes the agent with write-related tools
    disabled.  Includes the same retry logic as ``analyze_and_fix``.

    Args:
        project_dir: Root directory of the project being validated.
        check_output: Output from the most recent check command.
        check_cmd: Shell command used to verify the project.
        run_dir: Session run directory for conversation logs and report.
        max_retries: Maximum SDK call attempts on rate-limit errors.
        spec: Path to the spec file relative to project_dir (default: README.md).
    """
    rdir = run_dir or _runs_base(project_dir)
    rdir.mkdir(parents=True, exist_ok=True)

    prompt = build_validate_prompt(project_dir, check_output, check_cmd, rdir, spec=spec)

    _run_agent_with_retries(
        lambda: _run_validate_claude_agent(prompt, project_dir, rdir),
        fail_label="Validate agent",
        max_retries=max_retries,
    )


# ---------------------------------------------------------------------------
# evolve diff — lightweight spec-vs-implementation gap detection
# ---------------------------------------------------------------------------
#
# SPEC.md § "evolve diff" — one-shot subcommand that shows the delta between
# the spec and the implementation.  Lighter-weight than --validate: uses
# --effort low, does not run the check command, checks for presence/absence
# of major features rather than exhaustive claim-by-claim verification.
# Exit codes: 0 = compliant, 1 = gaps found, 2 = error.


def build_diff_prompt(
    project_dir: Path,
    run_dir: Path | None = None,
    spec: str | None = None,
) -> str:
    """Build the prompt for the ``evolve diff`` one-shot subcommand.

    The agent scans the spec for major features/architectural claims and
    checks whether each is present in the codebase.  Produces a
    ``diff_report.md`` with per-section compliance and overall percentage.

    Args:
        project_dir: Root directory of the project.
        run_dir: Session directory where the report will be written.
        spec: Path to the spec file relative to project_dir (default: README.md).

    Returns:
        The fully assembled prompt string.
    """
    ctx = _load_project_context(project_dir, spec=spec)
    readme = ctx["readme"]
    improvements = ctx["improvements"] or "(none)"

    rdir = str(run_dir or ".evolve/runs")

    return f"""\
You are a lightweight spec compliance agent. You are running in DIFF mode.
You MUST NOT modify any project files. Your only writable action is to
create `{rdir}/diff_report.md`.

Your task: scan the spec for major features and architectural claims. For
each one, check whether it is present in the codebase. Report gaps — do
NOT verify exhaustively (that is what --validate is for). Focus on
presence/absence of major capabilities, not line-by-line correctness.

Use Read, Grep, and Glob tools to examine the codebase. Do NOT use Edit, Write, or Bash.

At the end, write `{rdir}/diff_report.md` with the following format:

# Diff Report

## Sections

For EACH major section or feature area in the spec, write one line:
- ✅ **Section/feature name** — present (brief evidence)
- ❌ **Section/feature name** — missing (brief description of gap)

## Summary

- **Total sections**: N
- **Present**: N (✅)
- **Missing**: N (❌)
- **Compliance**: XX%

## Gaps

For each ❌ item, briefly describe what is missing.

IMPORTANT: Keep it concise. This is a quick gap-detection pass, not an
exhaustive audit. Check for the presence of major features, modules, CLI
flags, and architectural patterns described in the spec.

## Spec (specification)
{readme if readme else "(no spec found)"}

## Current improvements.md
{improvements}"""


async def _run_diff_claude_agent(
    prompt: str,
    project_dir: Path,
    run_dir: Path,
) -> None:
    """Run the Claude agent in diff mode with restricted tools.

    Thin wrapper around :func:`_run_readonly_claude_agent`.

    Args:
        prompt: The diff prompt.
        project_dir: Root directory of the project (used as cwd).
        run_dir: Session directory for the conversation log and report.
    """
    await _run_readonly_claude_agent(
        prompt, project_dir, run_dir,
        log_filename="diff_conversation.md",
        log_header="Diff Analysis",
    )


def run_diff_agent(
    project_dir: Path,
    run_dir: Path | None = None,
    max_retries: int = 5,
    spec: str | None = None,
) -> None:
    """Run the agent for the ``evolve diff`` one-shot subcommand.

    Builds a diff prompt and invokes the agent with write-related tools
    disabled.  Includes the same retry logic as the other agents.

    Args:
        project_dir: Root directory of the project.
        run_dir: Session run directory for conversation logs and report.
        max_retries: Maximum SDK call attempts on rate-limit errors.
        spec: Path to the spec file relative to project_dir (default: README.md).
    """
    rdir = run_dir or _runs_base(project_dir)
    rdir.mkdir(parents=True, exist_ok=True)

    prompt = build_diff_prompt(project_dir, rdir, spec=spec)

    _run_agent_with_retries(
        lambda: _run_diff_claude_agent(prompt, project_dir, rdir),
        fail_label="Diff agent",
        max_retries=max_retries,
    )


# ---------------------------------------------------------------------------
# evolve sync-readme — refresh README.md to reflect the current spec
# ---------------------------------------------------------------------------
#
# SPEC.md § "evolve sync-readme" — one-shot subcommand that refreshes
# README.md to reflect the current spec.  Never runs as part of the
# evolution loop.  Exit codes: 0 = proposal written / applied,
# 1 = README already in sync (agent writes the sentinel below), 2 = error.

# Sentinel file the agent writes inside ``run_dir`` when README is already
# in sync with the spec.  The orchestrator checks for this file to map the
# agent's "no changes needed" signal onto exit code 1.
SYNC_README_NO_CHANGES_SENTINEL = "NO_SYNC_NEEDED"


def build_sync_readme_prompt(
    project_dir: Path,
    run_dir: Path,
    spec: str | None = None,
    apply: bool = False,
) -> str:
    """Build the prompt for the ``evolve sync-readme`` one-shot subcommand.

    The agent is asked to refresh README.md so it reflects the current
    spec while preserving the README's tutorial voice.  The agent has
    exactly two valid outputs:

    - Write the new README content to ``output_path`` (project-root
      ``README_proposal.md`` in default mode, ``README.md`` in apply mode).
    - Write the sentinel file ``run_dir/NO_SYNC_NEEDED`` when the README
      is already in sync with the spec — this maps to exit code 1.

    Args:
        project_dir: Root directory of the project.
        run_dir: Session directory where the agent's conversation log and
            the optional ``NO_SYNC_NEEDED`` sentinel are written.
        spec: Path to the spec file relative to ``project_dir`` (defaults
            to ``README.md``).
        apply: When ``True``, the agent writes directly to ``README.md``;
            otherwise it writes to ``README_proposal.md``.

    Returns:
        The fully assembled prompt string.
    """
    ctx = _load_project_context(project_dir, spec=spec)
    spec_text = ctx["readme"]  # _load_project_context returns spec as 'readme'
    spec_name = spec or "README.md"

    readme_path = project_dir / "README.md"
    readme_text = readme_path.read_text() if readme_path.is_file() else ""

    output_name = "README.md" if apply else "README_proposal.md"
    output_path = project_dir / output_name
    sentinel_path = run_dir / SYNC_README_NO_CHANGES_SENTINEL

    return f"""\
You are the evolve sync-readme agent. Your single task: refresh
README.md so it reflects the current spec ({spec_name}), while
preserving the README's tutorial voice.

Voice constraints (MANDATORY):
- Brevity — README is a user-level summary, NOT an exhaustive copy of
  the spec. Keep examples short and link to {spec_name} for internals.
- Do NOT copy the spec verbatim. Synthesize.
- Do NOT invent features that aren't in the spec.
- Preserve the README's structure where it still fits the spec.

You have exactly two valid outputs:

1. If the README is already in sync with the spec (no user-visible
   drift), write the sentinel file:
       {sentinel_path}
   with any short rationale as its content. Do NOT write to
   {output_path}. The orchestrator will exit with code 1 ("README
   already in sync — no changes proposed").

2. Otherwise, write the new README content to:
       {output_path}
   Use the Write tool. Do NOT modify any other file in the project.
   The orchestrator will exit with code 0 ("proposal written" /
   "applied").

Mode: {"APPLY (writing directly to README.md, will be committed)" if apply else "PROPOSAL (writing to README_proposal.md for human review)"}

## Spec ({spec_name})

{spec_text if spec_text else "(spec file empty or missing)"}

## Current README.md

{readme_text if readme_text else "(no README.md found — write a fresh one based on the spec)"}
"""


async def _run_sync_readme_claude_agent(
    prompt: str,
    project_dir: Path,
    run_dir: Path,
) -> None:
    """Run the Claude agent for the ``sync-readme`` one-shot subcommand.

    Allows ``Write`` (the agent must produce ``README.md`` /
    ``README_proposal.md`` / ``NO_SYNC_NEEDED``) and ``Read`` / ``Grep``
    / ``Glob`` for context, but disallows ``Edit``, ``Bash``, ``Task``,
    ``Agent``, ``WebSearch``, and ``WebFetch`` so the agent cannot
    sprawl beyond its one-shot mandate.
    """
    _patch_sdk_parser()
    from claude_agent_sdk import query, ClaudeAgentOptions, AssistantMessage, ResultMessage

    options = ClaudeAgentOptions(
        permission_mode="bypassPermissions",
        model=MODEL,
        max_turns=MAX_TURNS,
        cwd=str(project_dir),
        disallowed_tools=["Edit", "Bash", "Task", "Agent", "WebSearch", "WebFetch"],
        include_partial_messages=True,
        effort=EFFORT,
    )

    log_path = run_dir / "sync_readme_conversation.md"
    ui = get_tui()

    with open(log_path, "w", buffering=1) as log:
        log.write("# Sync README\n\n")

        seen_tool_ids: set[str] = set()
        seen_text_hashes: set[int] = set()
        tools_used = 0

        try:
            async for message in query(prompt=prompt, options=options):
                if message is None:
                    continue
                if isinstance(message, (AssistantMessage, ResultMessage)):
                    if not hasattr(message, "content") or not message.content:
                        continue
                    for block in message.content:
                        if hasattr(block, "text") and block.text.strip():
                            h = hash(block.text)
                            if h in seen_text_hashes:
                                continue
                            seen_text_hashes.add(h)
                            log.write(f"\n{block.text}\n")
                            ui.agent_text(block.text)
                        elif hasattr(block, "name"):
                            block_id = getattr(block, "id", None)
                            if block_id and block_id in seen_tool_ids:
                                continue
                            if block_id:
                                seen_tool_ids.add(block_id)
                            tools_used += 1
                            tool_name = block.name
                            tool_input = ""
                            if hasattr(block, "input") and block.input:
                                inp = block.input
                                if isinstance(inp, dict):
                                    tool_input = inp.get("file_path", inp.get("pattern", str(inp)[:100]))
                                else:
                                    tool_input = str(inp)[:100]
                            log.write(f"\n**{tool_name}**: `{tool_input}`\n")
                            ui.agent_tool(tool_name, tool_input)
        except Exception as e:
            log.write(f"\n> SDK error: {e}\n")

        log.write(f"\n---\n\n**Done**: {tools_used} tool calls\n")

    ui.agent_done(tools_used, str(log_path))


def run_sync_readme_agent(
    project_dir: Path,
    run_dir: Path,
    spec: str | None = None,
    apply: bool = False,
    max_retries: int = 5,
) -> None:
    """Run the agent for the ``evolve sync-readme`` one-shot subcommand.

    Builds the sync-readme prompt and invokes the agent with the same
    retry shell as the other one-shot agents.  The orchestrator
    (``loop.run_sync_readme``) interprets the resulting filesystem
    state to compute the exit code.

    Args:
        project_dir: Root directory of the project.
        run_dir: Session directory for conversation log and sentinel.
        spec: Path to the spec file relative to project_dir.
        apply: When True, agent writes directly to README.md.
        max_retries: Maximum SDK call attempts on rate-limit errors.
    """
    run_dir.mkdir(parents=True, exist_ok=True)

    prompt = build_sync_readme_prompt(project_dir, run_dir, spec=spec, apply=apply)

    _run_agent_with_retries(
        lambda: _run_sync_readme_claude_agent(
            prompt, project_dir, run_dir,
        ),
        fail_label="Sync-readme agent",
        max_retries=max_retries,
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
# draft_agent — Winston + John pipeline, one US per call
# ---------------------------------------------------------------------------
#
# SPEC § "Multi-call round architecture" — the drafting call.  Runs as a
# dedicated SDK session when the backlog is drained, produces exactly one
# new US item in improvements.md, and returns.  Uses the centralized
# ``MODEL`` (Opus) — see SPEC § "Single model: Opus everywhere" for the
# rationale.  ``effort=EFFORT`` + ``max_turns=MAX_TURNS``.


def _build_draft_prompt(
    project_dir: Path,
    run_dir: Path,
    spec: str | None = None,
) -> str:
    """Build the system prompt for the draft agent.

    Loads ``prompts/draft.md``, substitutes placeholders
    (``{project_dir}``, ``{run_dir}``, ``{runs_base}``), and appends the
    project context (spec + current improvements.md + memory.md) so the
    agent has everything it needs to find the first unimplemented spec
    claim and draft a US for it.
    """
    prompt_path = Path(__file__).resolve().parent.parent / "prompts" / "draft.md"
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
    """Spawn the draft agent as a dedicated SDK call.

    Opus (centralized ``MODEL``), ``effort=DRAFT_EFFORT`` (spec-fixed at
    ``"low"`` per SPEC § "Multi-call round architecture"), ``max_turns=MAX_TURNS``.
    Edit is allowed (needs to modify ``improvements.md``); Bash / Task /
    Agent / Web* are disallowed.
    """
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
    """Drive the draft call of a round.

    Invoked by the orchestrator when ``improvements.md`` has zero
    unchecked ``[ ]`` items.  Produces exactly one new US item (or
    writes nothing if every spec claim is implemented — Phase 4
    convergence handles the latter case).

    Args:
        project_dir: Root directory of the project being evolved.
        run_dir: Session directory for logs.
        spec: Path to the spec file relative to project_dir
            (default: README.md).
        max_retries: Maximum SDK call attempts on rate-limit errors.
    """
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
#
# SPEC § "Multi-call round architecture" — the review call.  Runs after
# the implement call's commit + post-check.  Writes review_round_N.md
# with a verdict the orchestrator routes via _check_review_verdict.


def _build_review_prompt(
    project_dir: Path,
    run_dir: Path,
    round_num: int,
    spec: str | None = None,
) -> str:
    """Build the system prompt for the review agent."""
    prompt_path = Path(__file__).resolve().parent.parent / "prompts" / "review.md"
    template = prompt_path.read_text() if prompt_path.is_file() else ""
    rdir = str(run_dir)
    runs_base_str = str(_runs_base(project_dir))
    template = template.replace("{project_dir}", str(project_dir))
    template = template.replace("{run_dir}", rdir)
    template = template.replace("{runs_base}", runs_base_str)
    template = template.replace("{round_num}", str(round_num))

    # Load the artifacts Zara needs: the implement conversation log for
    # this round, the git diff of the last commit, and the spec.
    ctx = _load_project_context(project_dir, spec=spec)
    readme = ctx["readme"] or "(no spec file found)"

    # Implement-call conversation log.  Prefer the per-attempt variant
    # if present; fall back to the canonical.
    convo_path = run_dir / f"conversation_loop_{round_num}.md"
    implement_log = convo_path.read_text() if convo_path.is_file() else "(no conversation log)"

    # Git diff of the latest commit — stripped to a size that fits the
    # review budget (~4000 bytes is plenty for most rounds).
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
    """Spawn Zara as a dedicated SDK call.

    Opus (centralized ``MODEL``), ``effort=REVIEW_EFFORT`` (spec-fixed at
    ``"low"`` per SPEC § "Multi-call round architecture"), ``max_turns=MAX_TURNS``.
    Write is allowed (needs to create ``review_round_N.md``); Edit /
    Bash / Task / Agent / Web* are disallowed — review is write-once,
    not iterative editing of other files.
    """
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
    """Drive the review call of a round.

    Invoked by the orchestrator after the implement call's commit
    and post-check.  Writes ``{run_dir}/review_round_{N}.md`` with
    a verdict the orchestrator's ``_check_review_verdict`` parses.

    Args:
        project_dir: Root directory of the project being evolved.
        run_dir: Session directory for logs + review file.
        round_num: Current evolution round number.
        spec: Path to the spec file relative to project_dir.
        max_retries: Maximum SDK call attempts on rate-limit errors.
    """
    run_dir.mkdir(parents=True, exist_ok=True)
    prompt = _build_review_prompt(project_dir, run_dir, round_num, spec=spec)
    _run_agent_with_retries(
        lambda: _run_review_claude_agent(prompt, project_dir, run_dir, round_num),
        fail_label="Review agent",
        max_retries=max_retries,
    )
