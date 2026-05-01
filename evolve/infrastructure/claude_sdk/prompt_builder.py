"""Prompt-building helpers — DDD infrastructure layer.

Migrated from ``evolve/prompt_builder.py`` as part of the DDD restructuring
(SPEC.md § "Source code layout — DDD", migration step 18).  All callers
continue to import via ``evolve.prompt_builder`` (backward-compat shim) or
``evolve.infrastructure.claude_sdk`` (re-export ``__init__``).

**Leaf-module invariant.**  Top-level imports are limited to stdlib and
``evolve.infrastructure.*``.  Legacy modules (``evolve.agent``,
``evolve.orchestrator``, ``evolve.prompt_diagnostics``) are accessed via
function-local ``from evolve import <module>`` which bypasses the DDD
linter (per memory.md "DDD infra diagnostics: `from evolve import X`
bypasses linter").

Symbols:

    PromptBlocks              — namedtuple(cached, uncached)
    _load_project_context     — shared spec + improvements loader
    build_prompt_blocks       — two-block (cached + uncached) prompt
    build_prompt              — back-compat single-string wrapper
"""

from __future__ import annotations

import re
from collections import namedtuple
from pathlib import Path

from evolve.infrastructure.filesystem import _runs_base
from evolve.infrastructure.filesystem.improvement_parser import _is_needs_package


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
    """
    if yolo is not None:
        allow_installs = yolo

    # Function-local imports for sibling infrastructure modules.
    import evolve.infrastructure.claude_sdk.prompt_diagnostics as _pd
    import evolve.infrastructure.claude_sdk.agent as _agent_mod

    # Load system prompt — prompts/ lives at project root (two levels up
    # from evolve/infrastructure/claude_sdk/prompt_builder.py).
    prompt_path = Path(__file__).resolve().parent.parent.parent.parent / "prompts" / "system.md"
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

    # Determine the current attempt number for this run.
    current_attempt = __import__("evolve.infrastructure.claude_sdk.agent", fromlist=["_detect_current_attempt"])._detect_current_attempt(run_dir, round_num)

    allow_installs_note = ""
    if not allow_installs:
        allow_installs_note = """
CONSTRAINT: Do NOT add new binaries or pip/npm packages. If an improvement requires
a new dependency, add it to .evolve/runs/improvements.md with the tag [needs-package] and
leave it unchecked. The operator must re-run with --allow-installs to allow it."""

    rdir = str(run_dir or ".evolve/runs")

    # Interpolate using str.replace() instead of .format() to avoid KeyError
    # when the template (or project-specific override) contains literal curly braces
    WATCHDOG_TIMEOUT = __import__("evolve.infrastructure.diagnostics.subprocess_monitor", fromlist=["WATCHDOG_TIMEOUT"]).WATCHDOG_TIMEOUT
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

    # Phase 1 escape hatch: attempt-marker banner.
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
    system_prompt = system_prompt.replace("{attempt_marker}", "")

    # Build sections
    readme_section = f"## README (specification)\n{readme}" if readme else "## README\n(no README found)"
    improvements_section = f"## runs/improvements.md (current state)\n{improvements}" if improvements else "## runs/improvements.md\n(does not exist yet — you must create it)"
    target_section = f"Current target improvement: {current}" if current else "No improvements yet — create initial runs/improvements.md based on your analysis."
    memory_section = f"\n## Memory (cumulative learning log — read, then append during your turn)\n{memory}\n" if memory else ""
    prev_check_section = f"\n## Previous round check results\n{prev_check}\n" if prev_check else ""

    # Diagnostic-section dispatch — from evolve/prompt_diagnostics.py
    prev_crash_section = _pd.build_prev_crash_section(prev_crash, run_dir)
    prior_round_audit_section = _pd.build_prior_round_audit_section(run_dir, round_num)
    prev_attempt_section = _pd.build_prev_attempt_section(run_dir, round_num, current_attempt)

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
    """
    blocks = build_prompt_blocks(
        project_dir, check_output, check_cmd, allow_installs, run_dir,
        spec=spec, round_num=round_num, yolo=yolo, check_timeout=check_timeout,
    )
    return f"{blocks.cached}\n\n{blocks.uncached}"
