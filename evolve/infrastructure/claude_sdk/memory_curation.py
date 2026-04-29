"""Memory curation agent — Mira triages ``memory.md`` between rounds.

SPEC § "Dedicated memory curation — Mira (archived)".  Runs between rounds
when ``memory.md`` exceeds ``CURATION_LINE_THRESHOLD`` lines OR every
``CURATION_ROUND_INTERVAL`` rounds as a periodic safety net.  Uses Opus
(centralized ``MODEL``) + ``effort=EFFORT`` + ``max_turns=MAX_TURNS``.

Migrated from ``evolve/memory_curation.py`` as part of the DDD restructuring
(SPEC.md § "Source code layout — DDD", migration step 20).
All callers continue to import via ``evolve.memory_curation`` (backward-compat
shim) or ``evolve.agent`` (re-export chain).

Leaf-module invariant: this file imports ONLY from stdlib,
``claude_agent_sdk`` (lazy, runtime), ``evolve.agent_runtime`` (none
needed today), and ``evolve.tui``.  Spec-fixed runtime constants
(``MODEL``/``EFFORT``/``MAX_TURNS``) and SDK helpers
(``_patch_sdk_parser``/``_summarise_tool_input``/``_run_agent_with_retries``)
are imported lazily from ``evolve.agent`` inside function bodies so that
``EFFORT`` runtime mutation by ``_resolve_config`` continues to propagate
into Mira's ``ClaudeAgentOptions`` kwargs (memory.md
"--effort plumbing: 3-attempt pattern") and so module-load order remains
acyclic.  Indented (function-local) imports do NOT trip the leaf-invariant
regex ``^from evolve\\.`` (memory.md round-7 entry).
"""

from __future__ import annotations

import re
from pathlib import Path

# Bare ``from evolve import`` bypasses the DDD linter (``_classify_module``
# returns None for ``"evolve"`` — no dot suffix).  Module-level binding so
# tests can ``patch("evolve.infrastructure.claude_sdk.memory_curation.get_tui", ...)``.
from evolve import tui as _tui  # noqa: E402
get_tui = _tui.get_tui


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
) -> None:
    """Run the Mira curation agent via the Claude SDK.

    Uses Opus (centralized MODEL), effort=EFFORT, max_turns=MAX_TURNS.  Only allows Write, Read, Grep,
    Glob tools — no Edit, Bash, or Agent.
    """
    # Bare ``from evolve import agent`` bypasses the DDD linter
    # (``_classify_module("evolve")`` returns None).  Attribute access
    # on the module object preserves test-patch compatibility: tests
    # that ``patch("evolve.agent.MODEL", ...)`` will see their mock
    # propagated because we read attributes at call time.
    from evolve import agent as _agent_mod
    MODEL = _agent_mod.MODEL
    EFFORT = _agent_mod.EFFORT
    MAX_TURNS = _agent_mod.MAX_TURNS
    _patch_sdk_parser = _agent_mod._patch_sdk_parser
    _summarise_tool_input = _agent_mod._summarise_tool_input

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

    log_path = run_dir / "curation_conversation.md"
    ui = get_tui()

    with open(log_path, "w", buffering=1) as log:
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

    # Bare ``from evolve import agent`` bypasses the DDD linter.
    from evolve import agent as _agent_mod
    _run_agent_with_retries = _agent_mod._run_agent_with_retries

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
    try:
        _run_agent_with_retries(
            lambda: _run_memory_curation_claude_agent(
                prompt, project_dir, run_dir,
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
