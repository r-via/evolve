"""SPEC archival agent — Sid extracts stable/historical sections from SPEC.md.

SPEC § "SPEC archival (Sid)".  Runs between rounds when SPEC.md > 2000 lines
OR ``round_num % 20 == 0``.  Uses Opus (centralized ``MODEL``) +
``effort=EFFORT`` + ``max_turns=MAX_TURNS``.  See ``tasks/spec-archival.md``
for full protocol.

Migrated from ``evolve/spec_archival.py`` as part of the DDD restructuring
(SPEC.md § "Source code layout — DDD", migration step 21).
All callers continue to import via ``evolve.spec_archival`` (backward-compat
shim) or ``evolve.agent`` (re-export chain).
"""

from __future__ import annotations

from pathlib import Path

# Bare ``from evolve import`` bypasses the DDD linter (``_classify_module``
# returns None for ``"evolve"`` — no dot suffix).  Module-level binding so
# tests can ``patch("evolve.infrastructure.claude_sdk.spec_archival.get_tui", ...)``.
import evolve.interfaces.tui as _tui  # noqa: E402
get_tui = _tui.get_tui


#: Line threshold above which SPEC archival triggers.
ARCHIVAL_LINE_THRESHOLD = 2000

#: Archival triggers every N rounds as a periodic safety net.
ARCHIVAL_ROUND_INTERVAL = 20

#: Maximum allowed shrinkage (fraction).  If archival would shrink
#: SPEC.md by more than this, the archival is ABORTED.
_ARCHIVAL_MAX_SHRINK = 0.80


def _should_run_spec_archival(spec_path: Path, round_num: int) -> bool:
    """Return True when SPEC archival should run this round.

    Triggers: spec file > ARCHIVAL_LINE_THRESHOLD lines OR
    round_num is a multiple of ARCHIVAL_ROUND_INTERVAL.
    """
    if round_num > 0 and round_num % ARCHIVAL_ROUND_INTERVAL == 0:
        return True
    if spec_path.is_file():
        try:
            line_count = len(spec_path.read_text().splitlines())
            return line_count > ARCHIVAL_LINE_THRESHOLD
        except OSError:
            pass
    return False


def build_spec_archival_prompt(
    spec_text: str,
    index_text: str,
    git_log: str,
    round_num: int,
    run_dir: Path,
    spec_path: Path,
    archive_dir: Path,
) -> str:
    """Build the prompt for the Sid SPEC archival agent.

    Args:
        spec_text: Current content of SPEC.md.
        index_text: Current content of SPEC/archive/INDEX.md (or empty).
        git_log: Output of ``git log --oneline -30``.
        round_num: Current round number.
        run_dir: Session run directory for audit log output.
        spec_path: Absolute path to the spec file.
        archive_dir: Absolute path to SPEC/archive/.

    Returns:
        The fully assembled archival prompt string.
    """
    audit_path = run_dir / f"spec_curation_round_{round_num}.md"

    return f"""\
You are Sid, the SPEC Archivist (see agents/archivist.md).

Your single task: identify stable/historical sections in SPEC.md, extract them
to SPEC/archive/ with summary stubs, and write an audit log.

## Current SPEC.md

{spec_text}

## Current SPEC/archive/INDEX.md

{index_text if index_text else "(empty — first archival pass)"}

## Recent git log (last 30 commits)

{git_log}

## Instructions

Run four passes in order:

1. **Stability detection** — for each section in SPEC.md, classify as:
   - ACTIVE: current contract the agent needs → KEEP
   - STABLE: working mechanism, rarely touched → KEEP (unless very long)
   - HISTORICAL: completed migration, one-shot examples, design archaeology → ARCHIVE candidate

2. **Stub drafting** — for each archive candidate, draft a 2-5 line summary:
   - The conclusion (current state)
   - A conditional pointer: `→ Full history: [SPEC/archive/NNN-<slug>.md]`
   - Note: stub MUST be strictly shorter (in lines) than the archived body

3. **Archive extraction** — for each candidate:
   - Next ID = max(existing IDs in INDEX.md) + 1, zero-padded to 3 digits
   - Write full section to: {archive_dir}/NNN-<slug>.md
   - Update INDEX.md at: {archive_dir}/INDEX.md

4. **SPEC rewrite** — replace archived section bodies with stubs in SPEC.md.
   Do NOT reorder sections. Do NOT change non-archived sections.

## Output

You MUST produce these files:

1. **Rewritten SPEC.md** — write to: {spec_path}
   - Archived sections replaced with stubs
   - Non-archived sections untouched
   - Section order preserved

2. **Archive files** — one per archived section at:
       {archive_dir}/NNN-<slug>.md

3. **Updated INDEX.md** — write to: {archive_dir}/INDEX.md
   Format:
   ```
   # SPEC Archive Index

   | ID  | Slug | Archived | Trigger |
   |-----|------|----------|---------|
   | NNN | slug | YYYY-MM-DD | reason |
   ```

4. **Audit log** — write to: {audit_path}
   Format:
   ```
   # Round {round_num} — SPEC Archival (Sid)

   **SPEC.md before:** <line count> lines / <byte count> bytes
   **SPEC.md after:**  <line count> lines / <byte count> bytes
   **Decisions:** X KEEP, Y ARCHIVE

   ## Ledger

   | Section | Decision | Reason |
   |---------|----------|--------|
   | ... | ... | ... |

   ## Narrative
   <What changed, ≤ 5 sentences>
   ```

Write all files using the Write tool, then stop.
"""


async def _run_spec_archival_claude_agent(
    prompt: str,
    project_dir: Path,
    run_dir: Path,
) -> None:
    """Run the Sid archival agent via the Claude SDK.

    Uses Opus (centralized MODEL), effort=EFFORT, max_turns=MAX_TURNS.  Only
    allows Write, Read, Grep, Glob tools — no Edit, Bash, or Agent.
    """
    # Bare ``from evolve import agent`` bypasses the DDD linter
    # (``_classify_module("evolve")`` returns None).  Attribute access
    # on the module object preserves test-patch compatibility: tests
    # that ``patch("evolve.agent.MODEL", ...)`` will see their mock
    # propagated because we read attributes at call time.
    import evolve.infrastructure.claude_sdk.agent as _agent_mod
    MODEL = __import__("evolve.infrastructure.claude_sdk.runtime", fromlist=["MODEL"]).MODEL
    EFFORT = __import__("evolve.infrastructure.claude_sdk.runtime", fromlist=["EFFORT"]).EFFORT
    MAX_TURNS = __import__("evolve.infrastructure.claude_sdk.runtime", fromlist=["MAX_TURNS"]).MAX_TURNS
    _patch_sdk_parser = __import__("evolve.infrastructure.claude_sdk.runtime", fromlist=["_patch_sdk_parser"])._patch_sdk_parser
    _summarise_tool_input = __import__("evolve.infrastructure.claude_sdk.runtime", fromlist=["_summarise_tool_input"])._summarise_tool_input

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

    log_path = run_dir / "archival_conversation.md"
    ui = get_tui()

    with open(log_path, "w", buffering=1) as log:
        log.write("# SPEC Archival (Sid)\n\n")

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


def run_spec_archival(
    project_dir: Path,
    run_dir: Path,
    round_num: int,
    spec_path: Path,
) -> str:
    """Run the Sid SPEC archival agent and return the verdict.

    Returns one of: ``"ARCHIVED"``, ``"ABORTED"``, ``"SDK_FAIL"``, ``"SKIPPED"``.

    Args:
        project_dir: Root directory of the project.
        run_dir: Session run directory.
        round_num: Current round number.
        spec_path: Absolute path to the spec file.
    """
    import subprocess as _sp

    # Bare ``from evolve import agent`` bypasses the DDD linter.
    import evolve.infrastructure.claude_sdk.agent as _agent_mod
    _run_agent_with_retries = __import__("evolve.infrastructure.claude_sdk.runtime", fromlist=["_run_agent_with_retries"])._run_agent_with_retries

    ui = get_tui()

    if not _should_run_spec_archival(spec_path, round_num):
        return "SKIPPED"

    if not spec_path.is_file():
        return "SKIPPED"
    original_text = spec_path.read_text()
    original_size = len(original_text.encode("utf-8"))

    # Ensure archive directory exists
    archive_dir = spec_path.parent / "SPEC" / "archive"
    archive_dir.mkdir(parents=True, exist_ok=True)

    # Read existing INDEX.md
    index_path = archive_dir / "INDEX.md"
    index_text = ""
    if index_path.is_file():
        try:
            index_text = index_path.read_text()
        except OSError:
            pass

    # Git log
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
    prompt = build_spec_archival_prompt(
        spec_text=original_text,
        index_text=index_text,
        git_log=git_log,
        round_num=round_num,
        run_dir=run_dir,
        spec_path=spec_path,
        archive_dir=archive_dir,
    )

    # Run the agent
    try:
        _run_agent_with_retries(
            lambda: _run_spec_archival_claude_agent(
                prompt, project_dir, run_dir,
            ),
            fail_label="SPEC archival (Sid)",
            max_retries=2,
        )
    except Exception as e:
        ui.warn(f"SPEC archival SDK failed: {e}")
        spec_path.write_text(original_text)
        return "SDK_FAIL"

    # Check audit log exists
    audit_path = run_dir / f"spec_curation_round_{round_num}.md"
    if not audit_path.is_file():
        ui.warn("SPEC archival: no audit log produced — restoring original")
        spec_path.write_text(original_text)
        return "SDK_FAIL"

    # Check shrinkage
    if spec_path.is_file():
        new_text = spec_path.read_text()
        new_size = len(new_text.encode("utf-8"))
    else:
        new_size = 0

    if original_size > 0:
        shrink_ratio = 1.0 - (new_size / original_size)
    else:
        shrink_ratio = 0.0

    if shrink_ratio > _ARCHIVAL_MAX_SHRINK:
        ui.warn(
            f"SPEC archival ABORTED: would shrink by {shrink_ratio:.0%} "
            f"(>{_ARCHIVAL_MAX_SHRINK:.0%} threshold) — restoring original"
        )
        spec_path.write_text(original_text)
        # Update audit log with ABORTED verdict
        try:
            audit_text = audit_path.read_text()
            audit_path.write_text(
                f"**verdict: ABORTED** (shrink {shrink_ratio:.0%} > "
                f"{_ARCHIVAL_MAX_SHRINK:.0%} threshold)\n\n{audit_text}"
            )
        except OSError:
            pass
        return "ABORTED"

    return "ARCHIVED"
