"""Claude opus agent — reads README as spec, fixes code, tracks improvements."""

from __future__ import annotations

import asyncio
import re
import shutil
import time
from collections import namedtuple
from pathlib import Path

from evolve.state import _is_needs_package, _runs_base
from evolve.tui import get_tui


# Two-block prompt structure for prompt caching — SPEC.md § "Prompt caching".
# The cached block contains static-per-session content (system template +
# SPEC/README) marked with ``cache_control={"type": "ephemeral"}``.  The
# uncached block contains per-round variable content (check results, memory,
# attempt marker, prior audit, crash diagnostics).
PromptBlocks = namedtuple("PromptBlocks", ["cached", "uncached"])


def _build_system_prompt_blocks(blocks: PromptBlocks) -> list[dict]:
    """Convert :class:`PromptBlocks` into the SDK's two-block system_prompt list.

    The first block (cached) carries ``cache_control={"type": "ephemeral"}``
    so the Anthropic API caches it across calls within the TTL window.

    Returns a list of two dicts suitable for ``ClaudeAgentOptions(system_prompt=...)``.
    """
    return [
        {
            "type": "text",
            "text": blocks.cached,
            "cache_control": {"type": "ephemeral"},
        },
        {
            "type": "text",
            "text": blocks.uncached,
        },
    ]


def _oneshot_system_prompt_blocks(prompt: str) -> list[dict]:
    """Build two-block system prompt for one-shot agents (dry-run, validate, diff, sync-readme, curation).

    One-shot agents run once per session, so caching is less impactful, but
    the SPEC requires every SDK call site to use the two-block format with
    ``cache_control``.  The full prompt is placed in the cached block and the
    uncached block is a minimal instruction.
    """
    return _build_system_prompt_blocks(PromptBlocks(
        cached=prompt,
        uncached="Proceed with the analysis.",
    ))


def _build_system_prompt_from_text(text: str) -> list[dict]:
    """Build a two-block system_prompt from a single text string.

    Used by read-only agents (dry-run, validate, diff, sync-readme,
    curation) where the prompt is built as a single string rather than
    via :func:`build_prompt_blocks`.  The entire text is placed in a
    single cached block.
    """
    return [
        {
            "type": "text",
            "text": text,
            "cache_control": {"type": "ephemeral"},
        },
    ]


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

#: Default Claude model used by the agent for code analysis and fixes.
MODEL = "claude-opus-4-6"

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

    Returns a :class:`PromptBlocks` with ``cached`` (static per session —
    system template + SPEC/README) and ``uncached`` (per-round variable —
    check results, memory, attempt marker, prior audit, crash logs).  The
    cached block is deterministic for the session so that the SDK's
    ``cache_control={"type": "ephemeral"}`` achieves cache hits on
    subsequent rounds.

    See SPEC.md § "Prompt caching" for the contract.

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
    system_prompt_blocks: list[dict] | None = None,
) -> None:
    """Run Claude Code agent with the given prompt. Logs conversation to run_dir/.

    Streams SDK messages, deduplicates partial updates, and writes a
    Markdown conversation log.  Tool calls are shown live in the TUI.

    Args:
        prompt: The assembled system prompt for the agent (used as the
            user message when *system_prompt_blocks* is provided, or as
            the sole prompt when it is ``None``).
        project_dir: Root directory of the project (used as cwd).
        round_num: Current evolution round number (for log naming).
        run_dir: Directory to write the conversation log into.
        log_filename: Override the default log filename.
        images: Optional list of image file paths to attach as multimodal
            content blocks alongside the text prompt.
        system_prompt_blocks: Two-block system prompt list with
            ``cache_control`` for prompt caching (SPEC.md § "Prompt
            caching").  When provided, this is set on
            ``ClaudeAgentOptions(system_prompt=...)`` and *prompt*
            becomes the initial user message.
    """
    _patch_sdk_parser()
    from claude_agent_sdk import query, ClaudeAgentOptions, AssistantMessage, ResultMessage

    options = ClaudeAgentOptions(
        permission_mode="bypassPermissions",
        model=MODEL,
        max_turns=40,
        cwd=str(project_dir),
        disallowed_tools=["Task", "Agent", "WebSearch", "WebFetch"],
        include_partial_messages=True,
        effort=EFFORT,
        **({"system_prompt": system_prompt_blocks} if system_prompt_blocks else {}),
    )

    # Log file
    out_dir = run_dir or _runs_base(project_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    fname = log_filename or f"conversation_loop_{round_num}.md"
    log_path = out_dir / fname

    ui = get_tui()

    with open(log_path, "w") as log:
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

        _log(f"\n---\n\n**Done**: {turn} messages, {tools_used} tool calls\n")

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
) -> None:
    """Run Claude opus agent to analyze and fix code.

    Builds a prompt, then invokes the agent with retry logic for rate limits
    and graceful handling of benign async teardown errors.

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
    # Prompt caching rollback: the ``list[dict]`` ``system_prompt``
    # format with ``cache_control`` is the Claude API native shape for
    # prompt caching, but the currently-installed
    # ``claude_agent_sdk.ClaudeAgentOptions`` does not accept that list
    # shape — passing it silently produces an API call without the
    # system prompt, and the model returns with zero tool calls (the
    # symptom reported by the operator: two attempts in a row each
    # logged ``[opus] done (0 tool calls)`` with no edits).
    #
    # Until an SDK path for caching is verified, concatenate the
    # cached + uncached blocks into a single string and pass it as
    # the user prompt (the legacy shape).  The caching-oriented
    # ``build_prompt_blocks`` / ``_build_system_prompt_blocks``
    # helpers stay in the codebase for the follow-up US; they just
    # aren't called from the live code path right now.
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
        await run_claude_agent(
            full_prompt, project_dir,
            round_num=round_num, run_dir=run_dir, log_filename=attempt_log_fname,
        )

    _run_agent_with_retries(
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
    system_prompt_blocks: list[dict] | None = None,
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
        system_prompt_blocks: Two-block system prompt list with
            ``cache_control`` for prompt caching.
    """
    _patch_sdk_parser()
    from claude_agent_sdk import query, ClaudeAgentOptions, AssistantMessage, ResultMessage

    if disallowed_tools is None:
        disallowed_tools = ["Edit", "Bash", "Task", "Agent", "WebSearch", "WebFetch"]

    options = ClaudeAgentOptions(
        permission_mode="bypassPermissions",
        model=MODEL,
        max_turns=40,
        cwd=str(project_dir),
        disallowed_tools=disallowed_tools,
        include_partial_messages=True,
        effort=EFFORT,
        **({"system_prompt": system_prompt_blocks} if system_prompt_blocks else {}),
    )

    log_path = run_dir / log_filename
    ui = get_tui()

    with open(log_path, "w") as log:
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
    blocks = _oneshot_system_prompt_blocks(prompt)
    await _run_readonly_claude_agent(
        "Proceed with the dry-run analysis.", project_dir, run_dir,
        log_filename="dry_run_conversation.md",
        log_header="Dry Run Analysis",
        system_prompt_blocks=blocks,
    )


def _run_agent_with_retries(
    async_fn,
    *,
    fail_label: str = "Agent",
    max_retries: int = 5,
) -> None:
    """Shared retry loop for running an async agent function.

    Handles SDK import check, asyncio warning filters, benign teardown
    errors, and rate-limit backoff.  Callers supply the actual async
    callable (already bound to its arguments).

    Args:
        async_fn: Zero-argument async callable that runs the agent.
        fail_label: Label used in the failure warning message.
        max_retries: Maximum SDK call attempts on rate-limit errors.
    """
    ui = get_tui()
    try:
        from claude_agent_sdk import query  # noqa: F401 — import check only
    except ImportError:
        ui.warn("claude-agent-sdk not installed, skipping agent")
        return

    import warnings
    warnings.filterwarnings("ignore", message=".*cancel scope.*")
    warnings.filterwarnings("ignore", message=".*Event loop is closed.*")

    for attempt in range(1, max_retries + 1):
        try:
            asyncio.run(async_fn())
            return
        except Exception as e:
            if isinstance(e, RuntimeError) and _is_benign_runtime_error(e):
                return

            wait = _should_retry_rate_limit(e, attempt, max_retries)
            if wait is not None:
                ui.sdk_rate_limited(wait, attempt, max_retries)
                time.sleep(wait)
                continue

            ui.warn(f"{fail_label} failed ({e})")
            return


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
    blocks = _oneshot_system_prompt_blocks(prompt)
    await _run_readonly_claude_agent(
        "Proceed with the validation analysis.", project_dir, run_dir,
        log_filename="validate_conversation.md",
        log_header="Validation Analysis",
        system_prompt_blocks=blocks,
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
    blocks = _oneshot_system_prompt_blocks(prompt)
    await _run_readonly_claude_agent(
        "Proceed with the diff analysis.", project_dir, run_dir,
        log_filename="diff_conversation.md",
        log_header="Diff Analysis",
        system_prompt_blocks=blocks,
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
    *,
    system_prompt_blocks: list[dict] | None = None,
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
        max_turns=40,
        cwd=str(project_dir),
        disallowed_tools=["Edit", "Bash", "Task", "Agent", "WebSearch", "WebFetch"],
        include_partial_messages=True,
        effort=EFFORT,
        **({"system_prompt": system_prompt_blocks} if system_prompt_blocks else {}),
    )

    log_path = run_dir / "sync_readme_conversation.md"
    ui = get_tui()

    with open(log_path, "w") as log:
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
    blocks = _oneshot_system_prompt_blocks(prompt)

    _run_agent_with_retries(
        lambda: _run_sync_readme_claude_agent(
            "Proceed with the README sync.", project_dir, run_dir,
            system_prompt_blocks=blocks,
        ),
        fail_label="Sync-readme agent",
        max_retries=max_retries,
    )


# ---------------------------------------------------------------------------
# Memory curation (Mira) — dedicated curator agent
# ---------------------------------------------------------------------------
#
# SPEC.md § "Dedicated memory curation (Mira)" — between-rounds agent that
# triages memory.md into KEEP / ARCHIVE / DELETE.  Uses Sonnet (cheap),
# effort=low, max_turns=1.  See tasks/memory-curation.md for full protocol.

#: Sonnet model used by the curation agent (cheaper than Opus).
_CURATION_MODEL = "claude-sonnet-4-20250514"

#: Curation is triggered when memory.md exceeds this many lines.
CURATION_LINE_THRESHOLD = 300

#: Curation is triggered every N rounds as a periodic safety net.
CURATION_ROUND_INTERVAL = 10

#: Maximum allowed shrinkage (fraction).  If curation would shrink
#: memory.md by more than this, the curation is ABORTED.
_CURATION_MAX_SHRINK = 0.80


def _should_run_curation(memory_path: Path, round_num: int) -> bool:
    """Return True when memory curation should run this round.

    Triggers: memory.md > CURATION_LINE_THRESHOLD lines OR
    round_num is a multiple of CURATION_ROUND_INTERVAL.
    """
    if round_num > 0 and round_num % CURATION_ROUND_INTERVAL == 0:
        return True
    if memory_path.is_file():
        try:
            line_count = len(memory_path.read_text().splitlines())
            return line_count > CURATION_LINE_THRESHOLD
        except OSError:
            pass
    return False


def build_memory_curation_prompt(
    memory_text: str,
    spec_memory_section: str,
    conversation_titles: list[str],
    git_log: str,
    round_num: int,
    run_dir: Path,
    memory_path: Path,
) -> str:
    """Build the prompt for the Mira memory curation agent.

    Args:
        memory_text: Current content of memory.md.
        spec_memory_section: Excerpt from SPEC.md § "memory.md".
        conversation_titles: Title lines from the last 5 conversation logs.
        git_log: Output of ``git log --oneline -30``.
        round_num: Current round number.
        run_dir: Session run directory for audit log output.
        memory_path: Absolute path to memory.md (for the agent to write).

    Returns:
        The fully assembled curation prompt string.
    """
    titles_block = "\n".join(f"- {t}" for t in conversation_titles) if conversation_titles else "(none)"
    audit_path = run_dir / f"memory_curation_round_{round_num}.md"

    return f"""\
You are Mira, the Memory Curator (see agents/curator.md).

Your single task: triage the current memory.md into KEEP / ARCHIVE / DELETE
decisions, then rewrite memory.md in-place and write an audit log.

## Rules (from SPEC.md § "memory.md")

{spec_memory_section}

## Current memory.md

{memory_text}

## Last 5 conversation log titles

{titles_block}

## Recent git log (last 30 commits)

{git_log}

## Instructions

Run four passes in order:

1. **Duplicate detection** — within each section, find entries with overlapping
   subject matter.  True duplicates → DELETE the older, merge detail into
   canonical.  Near-duplicates → merge if both verbose, keep both if telegraphic.

2. **Rediscoverability audit** — for each entry, ask: "Could a future agent
   rediscover this by reading SPEC.md, the code, or the commit?"
   If yes → ARCHIVE.  If still non-obvious → KEEP.

3. **Historical archival** — entries reading as "round X did Y because Z" where
   the round is > 20 rounds old AND no subsequent entry references it AND the
   fact is documented in SPEC.md or obvious from the commit → ARCHIVE.

4. **Section hygiene** — empty sections stay as stubs.  Section order is
   SPEC-defined (Errors, Decisions, Patterns, Insights).  ## Archive is
   append-only at the bottom.

## Output

You MUST produce exactly two files:

1. **Rewritten memory.md** — write the updated content to:
       {memory_path}
   Rules:
   - Archived entries go to a `## Archive` section at the bottom
   - Empty sections keep their headers as stubs
   - Do NOT invent new entries — only reorganise existing ones
   - Do NOT reorder the main sections

2. **Audit log** — write the curation ledger to:
       {audit_path}
   Format:
   ```
   # Round {round_num} — Memory Curation (Mira)

   **memory.md before:** <line count> lines / <byte count> bytes
   **memory.md after:**  <line count> lines / <byte count> bytes
   **Decisions:** X KEEP, Y ARCHIVE, Z DELETE

   ## Ledger

   | Section | Title | Decision | Reason |
   |---------|-------|----------|--------|
   | ... | ... | ... | ... |

   ## Narrative
   <What changed, ≤ 5 sentences>
   ```

Write both files using the Write tool, then stop.
"""


async def _run_memory_curation_claude_agent(
    prompt: str,
    project_dir: Path,
    run_dir: Path,
    *,
    system_prompt_blocks: list[dict] | None = None,
) -> None:
    """Run the Mira curation agent via the Claude SDK.

    Uses Sonnet, effort=low, max_turns=1.  Only allows Write, Read, Grep,
    Glob tools — no Edit, Bash, or Agent.
    """
    _patch_sdk_parser()
    from claude_agent_sdk import query, ClaudeAgentOptions, AssistantMessage, ResultMessage

    options = ClaudeAgentOptions(
        permission_mode="bypassPermissions",
        model=_CURATION_MODEL,
        max_turns=1,
        cwd=str(project_dir),
        disallowed_tools=["Edit", "Bash", "Task", "Agent", "WebSearch", "WebFetch"],
        include_partial_messages=True,
        effort="low",
        **({"system_prompt": system_prompt_blocks} if system_prompt_blocks else {}),
    )

    log_path = run_dir / "curation_conversation.md"
    ui = get_tui()

    with open(log_path, "w") as log:
        log.write("# Memory Curation (Mira)\n\n")

        try:
            async for message in query(prompt=prompt, options=options):
                if message is None:
                    continue
                if isinstance(message, (AssistantMessage, ResultMessage)):
                    if not hasattr(message, "content") or not message.content:
                        continue
                    for block in message.content:
                        if hasattr(block, "text") and block.text.strip():
                            log.write(f"\n{block.text}\n")
                        elif hasattr(block, "name"):
                            tool_name = block.name
                            tool_input = _summarise_tool_input(
                                getattr(block, "input", None)
                            )
                            log.write(f"\n**{tool_name}**: `{tool_input}`\n")
                            ui.agent_tool(tool_name, tool_input)
        except Exception as e:
            log.write(f"\n> SDK error: {e}\n")

        log.write("\n---\n\n**Done**\n")


def run_memory_curation(
    project_dir: Path,
    run_dir: Path,
    round_num: int,
    memory_path: Path,
    spec_path: Path | None = None,
) -> str:
    """Run the Mira memory curation agent and return the verdict.

    Returns one of: ``"CURATED"``, ``"ABORTED"``, ``"SDK_FAIL"``, ``"SKIPPED"``.

    Args:
        project_dir: Root directory of the project.
        run_dir: Session run directory.
        round_num: Current round number.
        memory_path: Path to memory.md.
        spec_path: Path to the spec file (for extracting the memory section).
    """
    import subprocess as _sp

    ui = get_tui()

    if not _should_run_curation(memory_path, round_num):
        return "SKIPPED"

    # Snapshot original memory.md for abort recovery
    if not memory_path.is_file():
        return "SKIPPED"
    original_text = memory_path.read_text()
    original_size = len(original_text.encode("utf-8"))

    # Gather inputs for the prompt
    # 1. Spec memory section
    spec_memory_section = ""
    if spec_path and spec_path.is_file():
        spec_text = spec_path.read_text()
        # Extract the memory.md section from spec
        m = re.search(
            r"(## memory\.md.*?)(?=\n## [A-Z]|\n---|\Z)",
            spec_text,
            re.DOTALL,
        )
        if m:
            spec_memory_section = m.group(1).strip()
    if not spec_memory_section:
        spec_memory_section = (
            "Entries MUST be ≤ 5 lines or ≤ 400 chars. "
            "Telegraphic style. Non-obvious gate: don't log what's "
            "rediscoverable from SPEC/code/commit."
        )

    # 2. Conversation log titles (last 5)
    conversation_titles: list[str] = []
    for i in range(max(1, round_num - 4), round_num + 1):
        log_path = run_dir / f"conversation_loop_{i}.md"
        if log_path.is_file():
            try:
                first_line = log_path.read_text().split("\n", 1)[0].strip()
                conversation_titles.append(f"Round {i}: {first_line}")
            except OSError:
                pass

    # 3. Git log
    git_log = ""
    try:
        result = _sp.run(
            ["git", "log", "--oneline", "-30"],
            capture_output=True,
            text=True,
            cwd=str(project_dir),
            timeout=10,
        )
        git_log = result.stdout.strip() if result.returncode == 0 else "(git log failed)"
    except Exception:
        git_log = "(git log unavailable)"

    # Build prompt
    prompt = build_memory_curation_prompt(
        memory_text=original_text,
        spec_memory_section=spec_memory_section,
        conversation_titles=conversation_titles,
        git_log=git_log,
        round_num=round_num,
        run_dir=run_dir,
        memory_path=memory_path,
    )

    # Run the agent
    blocks = _oneshot_system_prompt_blocks(prompt)
    try:
        _run_agent_with_retries(
            lambda: _run_memory_curation_claude_agent(
                "Proceed with memory curation.", project_dir, run_dir,
                system_prompt_blocks=blocks,
            ),
            fail_label="Memory curation (Mira)",
            max_retries=2,
        )
    except Exception as e:
        ui.warn(f"Memory curation SDK failed: {e}")
        # Restore original
        memory_path.write_text(original_text)
        return "SDK_FAIL"

    # Check audit log exists
    audit_path = run_dir / f"memory_curation_round_{round_num}.md"
    if not audit_path.is_file():
        ui.warn("Memory curation: no audit log produced — restoring original")
        memory_path.write_text(original_text)
        return "SDK_FAIL"

    # Check shrinkage
    if memory_path.is_file():
        new_text = memory_path.read_text()
        new_size = len(new_text.encode("utf-8"))
    else:
        # Agent deleted memory.md — treat as >80% shrink
        new_size = 0

    if original_size > 0:
        shrink_ratio = 1.0 - (new_size / original_size)
    else:
        shrink_ratio = 0.0

    if shrink_ratio > _CURATION_MAX_SHRINK:
        ui.warn(
            f"Memory curation ABORTED: would shrink by {shrink_ratio:.0%} "
            f"(>{_CURATION_MAX_SHRINK:.0%} threshold) — restoring original"
        )
        memory_path.write_text(original_text)
        # Update audit log with ABORTED verdict
        try:
            audit_text = audit_path.read_text()
            audit_path.write_text(
                f"**verdict: ABORTED** (shrink {shrink_ratio:.0%} > "
                f"{_CURATION_MAX_SHRINK:.0%} threshold)\n\n{audit_text}"
            )
        except OSError:
            pass
        return "ABORTED"

    return "CURATED"
