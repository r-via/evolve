"""Prompt-building helpers — extracted from ``evolve/agent.py`` (US-035).

SPEC § "Hard rule: source files MUST NOT exceed 500 lines" — agent.py
was 1226 lines (2.45× the cap).  This module hosts the four
prompt-building symbols that dominated the file:

    _load_project_context          — shared spec + improvements loader
    _detect_prior_round_anomalies  — anomaly scan over prior round artifacts
    build_prompt_blocks            — two-block (cached + uncached) prompt
    build_prompt                   — back-compat single-string wrapper

Plus the supporting module-level constants:

    PromptBlocks                   — namedtuple(cached, uncached)
    _PREV_ATTEMPT_LOG_FMT          — retry continuity section template
    _MEMORY_WIPED_HEADER_FMT       — memory-wipe diagnostic template
    _PRIOR_ROUND_ANOMALY_PATTERNS  — regex table for prior round audit

Public symbols are re-exported from ``evolve.agent`` for backward
compatibility with the existing test suite (``patch("evolve.agent.
build_prompt", ...)``, ``from evolve.agent import build_prompt``,
``agent_mod._PREV_ATTEMPT_LOG_FMT``) and with the orchestrator's
late-binding import (``from evolve.agent import build_prompt`` inside
``_run_rounds``).

Leaf-module invariant: this file imports ONLY from stdlib,
``evolve.state`` (``_runs_base``, ``_is_needs_package``), and lazily
``evolve.agent`` (for ``_detect_current_attempt`` only) and
``evolve.orchestrator`` (for ``WATCHDOG_TIMEOUT`` only).  The lazy
``evolve.agent`` import inside ``build_prompt_blocks`` preserves
``patch("evolve.agent._detect_current_attempt", ...)`` test
interception (memory.md round-7 lesson: "the extracted function
must look X up via ``evolve.agent``, NOT the original source
module"); indented imports do NOT trip the leaf-invariant regex
``^from evolve\\.``.

This module follows the same extraction pattern as US-027
(diagnostics), US-030 (agent_runtime), US-031 (memory_curation),
US-032 (draft_review), US-033 (oneshot_agents), US-034
(sync_readme).
"""

from __future__ import annotations

import re
from collections import namedtuple
from pathlib import Path

from evolve.state import _is_needs_package, _runs_base


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


# Prompt section emitted on a debug retry to hand the agent the previous
# attempt's full conversation log — SPEC.md § "Retry continuity" rule (2).
# Kept as a module-level format constant so the single call site in
# ``build_prompt_blocks`` and any future test / helper share the same
# wording.  Format placeholders: ``{current}`` (current attempt number,
# int), ``{round}`` (round number, int), ``{prior}`` (prior attempt
# number, int), ``{log_path}`` (absolute path to the prior attempt's
# log file).
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
    # from evolve/prompt_builder.py: evolve/ → project root).
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
    #
    # Lazy import via ``evolve.agent`` (NOT ``evolve.agent_runtime`` or any
    # other module) so that ``patch("evolve.agent._detect_current_attempt",
    # ...)`` test calls intercept this call site.  Memory.md round-7
    # lesson: "the extracted function must look X up via ``evolve.agent``,
    # NOT the original source module".  Indented imports don't trip the
    # leaf-invariant regex ``^from evolve\\.``.
    from evolve.agent import _detect_current_attempt
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
