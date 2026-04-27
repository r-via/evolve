"""Prompt diagnostic-section builders — extracted from
``evolve/prompt_builder.py`` (round 3 of session 20260427_203955 audit
fix; addresses Zara's HIGH-1 review finding that the post-US-035
``prompt_builder.py`` was 723 lines, 1.45× the SPEC § "Hard rule:
source files MUST NOT exceed 500 lines" cap).

This module hosts the diagnostic-section helpers that previously
dominated ``build_prompt_blocks``: the prev-crash dispatch chain, the
prior-round audit section builder, and the previous-attempt log
section builder, plus their supporting constants and the
``_detect_prior_round_anomalies`` regex scan.

Public surface (re-exported from ``evolve.prompt_builder`` and onward
from ``evolve.agent`` for backward compat with existing patch targets):

    _PREV_ATTEMPT_LOG_FMT          — retry continuity section template
    _MEMORY_WIPED_HEADER_FMT       — memory-wipe diagnostic template
    _PRIOR_ROUND_ANOMALY_PATTERNS  — regex table for prior round audit
    _detect_prior_round_anomalies  — anomaly scan over prior round artifacts
    build_prev_crash_section       — dispatch on diagnostic prefix
    build_prior_round_audit_section— audit section emitter
    build_prev_attempt_section     — retry-continuity section emitter

Leaf-module invariant: imports ONLY from stdlib.  No ``evolve.*``
top-level imports — same invariant as the other leaf modules under
``evolve/`` (``agent_runtime``, ``memory_curation``, ``draft_review``,
``oneshot_agents``, ``sync_readme``, ``prompt_builder``).  The
extraction does not introduce any circular dependency because every
helper here is pure: it consumes simple primitive arguments
(``run_dir: Path | None``, ``round_num: int``, ``prev_crash: str``,
``current_attempt: int``) and returns a string.

Why a separate module rather than inlined dispatch dict.  The
prev-crash branch is a chain of ~9 prefix tests, each emitting a
substantially different markdown section (different headings, different
remediation lists, different references to SPEC sections).  A dispatch
dict would still need ~150 lines of section literals and would obscure
the section text behind keyed lookups; the function form keeps the
human-readable narrative grouped per branch.  The dominant size win
comes from moving the literal section text out of
``build_prompt_blocks`` body.
"""

from __future__ import annotations

import re
from collections import namedtuple  # noqa: F401 — re-exported via prompt_builder
from pathlib import Path


# Prompt section emitted on a debug retry to hand the agent the previous
# attempt's full conversation log — SPEC.md § "Retry continuity" rule (2).
# Kept as a module-level format constant so the single call site in
# ``build_prev_attempt_section`` and any future test / helper share the
# same wording.  Format placeholders: ``{current}`` (current attempt
# number, int), ``{round}`` (round number, int), ``{prior}`` (prior
# attempt number, int), ``{log_path}`` (absolute path to the prior
# attempt's log file).
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


def build_prev_crash_section(prev_crash: str) -> str:
    """Render the appropriate ``## CRITICAL`` diagnostic section.

    Dispatches on the diagnostic prefix in ``prev_crash`` (the contents
    of ``subprocess_error_round_N.txt``).  Each prefix corresponds to a
    distinct orchestrator-detected failure mode and gets a tailored
    instruction block.  See SPEC.md § "Zero progress detection" and
    sibling carve-outs.

    Returns an empty string when ``prev_crash`` is falsy.
    """
    if not prev_crash:
        return ""
    if "MEMORY WIPED" in prev_crash:
        return _MEMORY_WIPED_HEADER_FMT.format(diagnostic=prev_crash)
    if "SCOPE CREEP" in prev_crash:
        # Rebuild + implement in the same round — the Phase 2
        # rebuild touched improvements.md AND the commit also
        # modified non-improvements files.  The retry must split
        # the work: commit ONLY the improvements.md rebuild this
        # round; any implementation happens in the NEXT round's
        # fresh agent.  See SPEC § "improvements.md" + Phase 2
        # gate in prompts/system.md.
        return (
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
    if "BACKLOG DRAINED" in prev_crash:
        # The previous round legitimately had nothing to implement
        # (every ``[ ]`` item already checked off) but the agent
        # stopped short of Phase 4 — no CONVERGED written, no
        # commit body, no edits.  The retry must NOT go fishing
        # for something to do; the correct next step is Phase 4
        # (verify README claims, then write CONVERGED).
        return (
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
    if "BACKLOG VIOLATION" in prev_crash:
        # Backlog discipline rule 1 (empty-queue gate) — see SPEC.md §
        # "Backlog discipline".  The previous attempt added a new `- [ ]`
        # item to improvements.md while at least one other `- [ ]` item
        # was still pending.  Tell the agent to remove the freshly added
        # item(s) and let the queue drain before adding anything new.
        return (
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
    if "MAX_TURNS" in prev_crash:
        # SDK subtype=error_max_turns — the agent hit the turn cap
        # before finishing.  Per SPEC § "Authoritative termination
        # signal from the SDK": retry with "fix-only, defer
        # investigation" header.
        return (
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
    if "SDK ERROR" in prev_crash:
        # SDK subtype=error_during_execution — the agent hit an SDK
        # error.  Surface it verbatim per SPEC § "Authoritative
        # termination signal from the SDK".
        return (
            f"\n## CRITICAL — Agent stopped with SDK execution error\n"
            f"The previous attempt's Claude Agent SDK session ended with "
            f"``subtype=error_during_execution``.  The SDK error is in the "
            f"diagnostic below.  Diagnose and work around it — typically "
            f"a tool permission issue or a malformed tool input.\n"
            f"```\n{prev_crash}\n```\n"
        )
    if "NO PROGRESS" in prev_crash:
        return (
            f"\n## CRITICAL — Previous round made NO PROGRESS\n"
            f"The previous round ended without making meaningful changes. "
            f"Start with Edit/Write immediately and defer exploration.\n"
            f"```\n{prev_crash}\n```\n"
        )
    if "REVIEW:" in prev_crash:
        # Adversarial review verdict routing (SPEC § "Adversarial round
        # review (Phase 3.6)").  The previous attempt's adversarial
        # review produced HIGH findings that must be addressed before
        # the round can proceed.
        return (
            f"\n## CRITICAL — Previous attempt failed adversarial review\n"
            f"The adversarial reviewer (Zara) found HIGH-severity findings "
            f"in the previous attempt's work. You MUST address each HIGH "
            f"finding listed below before re-committing. Read the review "
            f"file (`review_round_N.md` in the run directory) for full "
            f"context, then fix each finding and re-run the adversarial "
            f"review.\n"
            f"```\n{prev_crash}\n```\n"
        )
    if "FILE TOO LARGE" in prev_crash:
        return (
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
    if "PREMATURE CONVERGED" in prev_crash:
        # Convergence-gate orchestrator backstop (SPEC.md § "Convergence").
        # The previous round wrote CONVERGED but the orchestrator's
        # independent re-verification of the two documented gates
        # rejected it. The agent MUST address the listed gate
        # violations (rebuild stale backlog, or resolve unresolved
        # `- [ ]` items) before attempting to write CONVERGED again.
        return (
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
    return f"\n## CRITICAL — Previous round CRASHED (fix this first!)\n```\n{prev_crash}\n```\n"


def build_prior_round_audit_section(
    run_dir: Path | None, round_num: int
) -> str:
    """Render the ``## Prior round audit`` section when anomalies exist.

    Scans the previous round's artifacts for anomaly signals (watchdog
    stalls, SIGKILL, pre-check timeouts, frame capture errors,
    circuit-breaker trips, post-fix FAIL).  When any signal is present,
    returns a markdown section instructing the agent to investigate
    those anomalies before touching the backlog.  See SPEC.md §
    "Prior round audit".

    Returns an empty string when no anomalies are detected.
    """
    prior_anomalies = _detect_prior_round_anomalies(run_dir, round_num)
    if not prior_anomalies:
        return ""
    prev = round_num - 1
    return (
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


def build_prev_attempt_section(
    run_dir: Path | None, round_num: int, current_attempt: int
) -> str:
    """Render the prior-attempt-log retry-continuity section.

    The rendered header is the one defined by ``_PREV_ATTEMPT_LOG_FMT``
    (the only literal occurrence of that heading in the source — the
    drift test in ``tests/test_constant_drift.py`` enforces this).

    When this run is a debug retry (attempt > 1), surface the previous
    attempt's full conversation log so the agent can continue from
    where it stopped instead of restarting the investigation.  The
    diagnostic in ``prev_crash_section`` is only the last 3000 chars
    of output; the full per-attempt log holds every tool call, dead
    end, and working hypothesis.  See SPEC.md § "Retry continuity"
    rule (2).

    Heuristic: a prior log under 500 bytes OR with no tool-call markers
    (``**Read**:``, ``**Edit**:``, ``**Bash**:`` …) is trivially empty
    and skipped — the user-visible symptom otherwise was every round
    starting with "Prior attempt log is empty (1 line). No useful
    context to reuse." pure prompt overhead.

    Returns an empty string when no useful prior-attempt log is
    available.
    """
    if current_attempt <= 1 or not run_dir:
        return ""
    prior_k = current_attempt - 1
    prior_log = Path(run_dir) / f"conversation_loop_{round_num}_attempt_{prior_k}.md"
    if not prior_log.is_file():
        return ""
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
    if len(content) < 500 or not has_tool_calls:
        return ""
    return _PREV_ATTEMPT_LOG_FMT.format(
        current=current_attempt,
        round=round_num,
        prior=prior_k,
        log_path=prior_log,
    )
