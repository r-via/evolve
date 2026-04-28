"""Prompt-building helpers — extracted from ``evolve/agent.py`` (US-035).

SPEC § "Hard rule: source files MUST NOT exceed 500 lines" — agent.py
was 1226 lines (2.45× the cap).  This module hosts the four
prompt-building symbols that dominated the file:

    _load_project_context          — shared spec + improvements loader
    _detect_prior_round_anomalies  — anomaly scan over prior round artifacts (re-exported from prompt_diagnostics)
    build_prompt_blocks            — two-block (cached + uncached) prompt
    build_prompt                   — back-compat single-string wrapper

Plus the supporting module-level constants:

    PromptBlocks                   — namedtuple(cached, uncached)
    _PREV_ATTEMPT_LOG_FMT          — retry continuity section template (re-exported from prompt_diagnostics)
    _MEMORY_WIPED_HEADER_FMT       — memory-wipe diagnostic template (re-exported from prompt_diagnostics)
    _PRIOR_ROUND_ANOMALY_PATTERNS  — regex table for prior round audit (re-exported from prompt_diagnostics)

Round 3 of session 20260427_203955 audit fix (HIGH-1 from Zara):
``prompt_builder.py`` was itself 723 lines — 1.45× the SPEC § "Hard
rule" cap that motivated US-035.  The diagnostic-section helpers and
their constants are now re-exported from
``evolve/prompt_diagnostics.py`` so that ``patch("evolve.agent.X")``
test targets and the ``from evolve.agent import X`` imports continue
to intercept (3-link re-export chain: ``agent`` →
``prompt_builder`` → ``prompt_diagnostics``, mirroring the
``agent`` → ``oneshot_agents`` → ``sync_readme`` chain established
in US-034).

Public symbols are re-exported from ``evolve.agent`` for backward
compatibility with the existing test suite (``patch("evolve.agent.
build_prompt", ...)``, ``from evolve.agent import build_prompt``,
``agent_mod._PREV_ATTEMPT_LOG_FMT``) and with the orchestrator's
late-binding import (``from evolve.agent import build_prompt`` inside
``_run_rounds``).

Leaf-module invariant: this file imports ONLY from stdlib,
``evolve.state`` (``_runs_base``, ``_is_needs_package``),
``evolve.prompt_diagnostics`` (sibling leaf module, no cycle), and
lazily ``evolve.agent`` (for ``_detect_current_attempt`` only) and
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

# Re-exports from the diagnostic-section sibling module.  These names
# carry through to ``evolve.agent`` via the existing
# ``from evolve.prompt_builder import (...)`` block in agent.py — keeping
# the 3-link chain (``agent`` → ``prompt_builder`` →
# ``prompt_diagnostics``) so existing test patches like
# ``patch("evolve.agent._PREV_ATTEMPT_LOG_FMT")`` and
# ``patch("evolve.agent._detect_prior_round_anomalies")`` continue to
# intercept by ``is``-identity.  See sibling chain ``agent`` →
# ``oneshot_agents`` → ``sync_readme`` (US-034) for the same
# established pattern.
from evolve.prompt_diagnostics import (  # noqa: F401 — re-exports
    _PREV_ATTEMPT_LOG_FMT,
    _MEMORY_WIPED_HEADER_FMT,
    _PRIOR_ROUND_ANOMALY_PATTERNS,
    _detect_prior_round_anomalies,
    build_prev_crash_section,
    build_prior_round_audit_section,
    build_prev_attempt_section,
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
    if run_dir:
        for f in sorted(Path(run_dir).glob("subprocess_error_round_*.txt"), key=lambda p: int(re.search(r'_(\d+)\.txt$', p.name).group(1)), reverse=True):
            prev_crash = f.read_text()
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

    # Diagnostic-section dispatch — extracted to evolve/prompt_diagnostics.py
    # (round 3 of 20260427_203955 audit fix; addresses Zara HIGH-1 review
    # finding that this file was 723 lines, > 500-line SPEC cap).
    prev_crash_section = build_prev_crash_section(prev_crash, run_dir)

    # Prior round audit section — moved to prompt_diagnostics.py for the
    # same audit-fix split.  Internal scan is preserved verbatim.
    prior_round_audit_section = build_prior_round_audit_section(run_dir, round_num)

    # Retry continuity: when this run is a debug retry (attempt > 1), the
    # helper surfaces the previous attempt's full conversation log when
    # one exists and contains useful tool-call traces.  See SPEC.md §
    # "Retry continuity" rule (2).
    prev_attempt_section = build_prev_attempt_section(run_dir, round_num, current_attempt)

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
