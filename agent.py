"""Claude opus agent — reads README as spec, fixes code, tracks improvements."""

from __future__ import annotations

import asyncio
import re
import shutil
import time
from pathlib import Path

from loop import _is_needs_package
from tui import get_tui


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
    improvements_path = project_dir / "runs" / "improvements.md"
    improvements = improvements_path.read_text() if improvements_path.is_file() else None

    return {"readme": readme, "improvements": improvements}


def build_prompt(
    project_dir: Path,
    check_output: str = "",
    check_cmd: str | None = None,
    allow_installs: bool = False,
    run_dir: Path | None = None,
    spec: str | None = None,
    round_num: int = 1,
    yolo: bool | None = None,
) -> str:
    """Build the system prompt for the opus agent from project context.

    Assembles README, improvements list, memory, check results, and crash
    logs into a single prompt string that guides the agent's behavior.

    Args:
        project_dir: Root directory of the project being evolved.
        check_output: Output from the most recent check command run.
        check_cmd: Shell command used to verify the project (e.g. 'pytest').
        allow_installs: If True, allow improvements tagged [needs-package].
        run_dir: Session run directory containing round artifacts.
        spec: Path to the spec file relative to project_dir (default: README.md).
        round_num: Current evolution round number (used for stuck-loop detection).
        yolo: Deprecated alias for *allow_installs*. Will be removed in a future version.

    Returns:
        The fully interpolated prompt string.
    """
    if yolo is not None:
        allow_installs = yolo
    # Load system prompt
    prompt_path = Path(__file__).parent / "prompts" / "system.md"
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
    memory_path = project_dir / "runs" / "memory.md"
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
a new dependency, add it to runs/improvements.md with the tag [needs-package] and
leave it unchecked. The operator must re-run with --allow-installs to allow it."""

    rdir = str(run_dir or "runs")

    # Interpolate using str.replace() instead of .format() to avoid KeyError
    # when the template (or project-specific override) contains literal curly braces
    # (e.g. JSON examples, Rust code, Go generics).
    from loop import WATCHDOG_TIMEOUT
    system_prompt = system_prompt.replace("{project_dir}", str(project_dir))
    system_prompt = system_prompt.replace("{run_dir}", rdir)
    # Support both old and new placeholder names for backward compatibility
    system_prompt = system_prompt.replace("{yolo_note}", allow_installs_note)
    system_prompt = system_prompt.replace("{allow_installs_note}", allow_installs_note)
    system_prompt = system_prompt.replace("{watchdog_timeout}", str(WATCHDOG_TIMEOUT))
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
    system_prompt = system_prompt.replace("{attempt_marker}", attempt_marker)

    # Build sections
    readme_section = f"## README (specification)\n{readme}" if readme else "## README\n(no README found)"
    improvements_section = f"## runs/improvements.md (current state)\n{improvements}" if improvements else "## runs/improvements.md\n(does not exist yet — you must create it)"
    target_section = f"Current target improvement: {current}" if current else "No improvements yet — create initial runs/improvements.md based on your analysis."
    memory_section = f"\n## Memory (errors from previous rounds — do NOT repeat these)\n{memory}\n" if memory else ""
    prev_check_section = f"\n## Previous round check results\n{prev_check}\n" if prev_check else ""
    if prev_crash:
        if "NO PROGRESS" in prev_crash:
            prev_crash_section = (
                f"\n## CRITICAL — Previous round made NO PROGRESS\n"
                f"The previous round ended without making meaningful changes. "
                f"Start with Edit/Write immediately and defer exploration.\n"
                f"```\n{prev_crash}\n```\n"
            )
        else:
            prev_crash_section = f"\n## CRITICAL — Previous round CRASHED (fix this first!)\n```\n{prev_crash}\n```\n"
    else:
        prev_crash_section = ""

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
            prev_attempt_section = (
                f"\n## Previous attempt log\n"
                f"This is attempt {current_attempt} of round {round_num}. "
                f"The full conversation log of attempt {prior_k} is at:\n\n"
                f"  {prior_log}\n\n"
                f"**Read this file FIRST.** It contains everything the previous "
                f"attempt already discovered — the tool calls, the dead ends, the "
                f"working hypotheses. Do not redo that investigation. Continue "
                f"from where it stopped.\n"
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

    return f"""\
{system_prompt}

{readme_section}

{improvements_section}

{target_section}
{prev_crash_section}
{prev_attempt_section}
{memory_section}
{prev_check_section}
{check_section}"""


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
) -> None:
    """Run Claude Code agent with the given prompt. Logs conversation to run_dir/.

    Streams SDK messages, deduplicates partial updates, and writes a
    Markdown conversation log.  Tool calls are shown live in the TUI.

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
        max_turns=40,
        cwd=str(project_dir),
        disallowed_tools=["Task", "Agent", "WebSearch", "WebFetch"],
        include_partial_messages=True,
    )

    # Log file
    out_dir = run_dir or (project_dir / "runs")
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
                            tool_input = ""
                            if hasattr(block, "input") and block.input:
                                inp = block.input
                                if isinstance(inp, dict):
                                    if "command" in inp:
                                        tool_input = inp["command"]
                                    elif "pattern" in inp:
                                        tool_input = inp["pattern"]
                                    elif "file_path" in inp:
                                        tool_input = inp["file_path"]
                                    elif "old_string" in inp:
                                        tool_input = f'{inp.get("file_path", "?")} (edit)'
                                    elif "content" in inp:
                                        tool_input = f'({len(inp["content"])} chars)'
                                else:
                                    tool_input = str(inp)[:100]
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
        except Exception as e:
            _log(f"\n> SDK error: {e}\n")

        _log(f"\n---\n\n**Done**: {turn} messages, {tools_used} tool calls\n")

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
    prompt = build_prompt(project_dir, check_output, check_cmd, allow_installs, run_dir, spec=spec, round_num=round_num)

    # Per-attempt conversation log filename.  Each orchestrator-level subprocess
    # attempt gets its own file (no overwrite), so a debug retry can read the
    # prior attempt's full transcript and continue from where it stopped.
    # See SPEC.md § "Retry continuity" rule (1).
    current_attempt = _detect_current_attempt(run_dir, round_num)
    attempt_log_fname = f"conversation_loop_{round_num}_attempt_{current_attempt}.md"

    async def _run():
        await run_claude_agent(
            prompt, project_dir,
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

    rdir = str(run_dir or "runs")

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

    rdir = str(run_dir or "runs")

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
        max_turns=40,
        cwd=str(project_dir),
        disallowed_tools=disallowed_tools,
        include_partial_messages=True,
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
    await _run_readonly_claude_agent(
        prompt, project_dir, run_dir,
        log_filename="dry_run_conversation.md",
        log_header="Dry Run Analysis",
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
    rdir = run_dir or (project_dir / "runs")
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
    rdir = run_dir or (project_dir / "runs")
    rdir.mkdir(parents=True, exist_ok=True)

    prompt = build_validate_prompt(project_dir, check_output, check_cmd, rdir, spec=spec)

    _run_agent_with_retries(
        lambda: _run_validate_claude_agent(prompt, project_dir, rdir),
        fail_label="Validate agent",
        max_retries=max_retries,
    )
