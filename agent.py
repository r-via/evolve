"""Claude opus agent — reads README as spec, fixes code, tracks improvements."""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

MODEL = "claude-opus-4-6"


def build_prompt(
    project_dir: Path,
    check_output: str = "",
    check_cmd: str | None = None,
    yolo: bool = False,
    run_dir: Path | None = None,
) -> str:
    """Build the prompt for the opus agent."""
    # Load system prompt
    prompt_path = Path(__file__).parent / "prompts" / "system.md"
    # Project can override with its own prompts/evolve-system.md
    project_prompt = project_dir / "prompts" / "evolve-system.md"
    if project_prompt.is_file():
        prompt_path = project_prompt

    system_prompt = prompt_path.read_text() if prompt_path.is_file() else ""

    # Load README
    readme = ""
    for name in ("README.md", "README.rst", "README.txt", "README"):
        p = project_dir / name
        if p.is_file():
            readme = p.read_text()
            break

    # Load improvements
    improvements_path = project_dir / "runs" / "improvements.md"
    improvements = improvements_path.read_text() if improvements_path.is_file() else None

    # Current target
    current = None
    if improvements:
        for line in improvements.splitlines():
            m = re.match(r"^- \[ \] (.+)$", line.strip())
            if m:
                current = m.group(1)
                break

    # Memory
    memory_path = project_dir / "runs" / "memory.md"
    memory = memory_path.read_text().strip() if memory_path.is_file() else ""

    # Previous check results
    prev_check = ""
    if run_dir:
        for f in sorted(Path(run_dir).glob("check_round_*.txt"), reverse=True):
            prev_check = f.read_text()
            break

    yolo_note = ""
    if not yolo:
        yolo_note = """
CONSTRAINT: Do NOT add new binaries or pip/npm packages. If an improvement requires
a new dependency, add it to runs/improvements.md with the tag [needs-package] and
leave it unchecked. The operator must re-run with --yolo to allow it."""

    rdir = str(run_dir or "runs")

    # Interpolate
    system_prompt = system_prompt.format(
        project_dir=project_dir,
        run_dir=rdir,
        yolo_note=yolo_note,
    )

    # Build sections
    readme_section = f"## README (specification)\n{readme}" if readme else "## README\n(no README found)"
    improvements_section = f"## runs/improvements.md (current state)\n{improvements}" if improvements else "## runs/improvements.md\n(does not exist yet — you must create it)"
    target_section = f"Current target improvement: {current}" if current else "No improvements yet — create initial runs/improvements.md based on your analysis."
    memory_section = f"\n## Memory (errors from previous rounds — do NOT repeat these)\n{memory}\n" if memory else ""
    prev_check_section = f"\n## Previous round check results\n{prev_check}\n" if prev_check else ""

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
{memory_section}
{prev_check_section}
{check_section}"""


def _patch_sdk_parser():
    """Monkey-patch SDK to not crash on malformed rate_limit_event."""
    try:
        from claude_agent_sdk._internal import message_parser
        original = message_parser.parse_message
        def patched(data):
            try:
                return original(data)
            except Exception:
                if isinstance(data, dict) and data.get("type") == "rate_limit_event":
                    return None
                raise
        message_parser.parse_message = patched
    except Exception:
        pass


async def run_claude_agent(
    prompt: str,
    project_dir: Path,
    round_num: int = 1,
    run_dir: Path | None = None,
    log_filename: str | None = None,
) -> None:
    """Run Claude Code agent with the given prompt. Logs conversation to run_dir/."""
    _patch_sdk_parser()
    from claude_agent_sdk import query, ClaudeAgentOptions, AssistantMessage, ResultMessage

    options = ClaudeAgentOptions(
        max_turns=20,
        permission_mode="bypassPermissions",
        model=MODEL,
        cwd=str(project_dir),
        disallowed_tools=["Task", "Agent", "WebSearch", "WebFetch"],
        include_partial_messages=True,
    )

    # Log file
    out_dir = run_dir or (project_dir / "runs")
    out_dir.mkdir(parents=True, exist_ok=True)
    fname = log_filename or f"conversation_loop_{round_num}.md"
    log_path = out_dir / fname

    with open(log_path, "w") as log:
        log.write(f"# Evolution Round {round_num}\n\n")

        def _log(line: str, console: bool = False):
            log.write(line + "\n")
            if console:
                print(line)

        turn = 0
        tools_used = 0
        stream = query(prompt=prompt, options=options)
        ait = stream.__aiter__()
        while True:
            try:
                message = await ait.__anext__()
            except StopAsyncIteration:
                break
            except Exception as e:
                _log(f"> SDK error: {e}")
                continue

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
                        _log(f"\n### Thinking\n\n{block.thinking}\n")

                    elif hasattr(block, "text") and block.text.strip():
                        _log(f"\n{block.text}\n", console=True)

                    elif hasattr(block, "name"):
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
                        print(f"  [opus] {tool_name} → {tool_input[:80]}")

                    elif block_type == "ToolResultBlock":
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

        _log(f"\n---\n\n**Done**: {turn} messages, {tools_used} tool calls\n")

    print(f"  [opus] done ({tools_used} tool calls) → {log_path}")


def analyze_and_fix(
    project_dir: Path,
    check_output: str = "",
    check_cmd: str | None = None,
    yolo: bool = False,
    max_retries: int = 5,
    round_num: int = 1,
    run_dir: Path | None = None,
) -> None:
    """Run Claude opus agent to analyze and fix code."""
    try:
        from claude_agent_sdk import query
    except ImportError:
        print("WARN: claude-agent-sdk not installed, skipping agent")
        return

    prompt = build_prompt(project_dir, check_output, check_cmd, yolo, run_dir)

    import warnings
    warnings.filterwarnings("ignore", message=".*cancel scope.*")
    warnings.filterwarnings("ignore", message=".*Event loop is closed.*")

    for attempt in range(1, max_retries + 1):
        try:
            asyncio.run(run_claude_agent(prompt, project_dir, round_num=round_num, run_dir=run_dir))
            return
        except RuntimeError as e:
            if "cancel scope" in str(e) or "Event loop is closed" in str(e):
                return
            if "rate_limit" in str(e).lower() and attempt < max_retries:
                wait = 60 * attempt
                print(f"  [sdk] rate limited — waiting {wait}s (attempt {attempt}/{max_retries})...")
                import time
                time.sleep(wait)
            else:
                print(f"WARN: Claude Code agent failed ({e})")
                return
        except Exception as e:
            if "rate_limit" in str(e).lower() and attempt < max_retries:
                wait = 60 * attempt
                print(f"  [sdk] rate limited — waiting {wait}s (attempt {attempt}/{max_retries})...")
                import time
                time.sleep(wait)
            else:
                print(f"WARN: Claude Code agent failed ({e})")
                return
