# 008 — Complete LLM Stream Capture Specification

> Archived from SPEC.md § "Complete LLM stream capture per agent invocation"
> on 2026-04-27. Stable implementation spec — file naming, capture contract,
> format rules, real-time write discipline.

---

### Complete LLM stream capture per agent invocation

Every Claude Agent SDK invocation evolve makes — implement (Amelia),
draft (Winston + John), review (Zara), memory curation (Mira),
SPEC archival (Sid), sync-readme, dry-run, validate, diff,
party — MUST persist its **complete stream** to a dedicated file
under the run directory.  "Complete" is normative: nothing the
SDK emits about that invocation may be silently dropped.  The
files are the primary debugging surface when an agent
misbehaves; truncated or summary logs make post-mortems
impossible and force operators to re-run with extra
instrumentation, which (a) costs another round's tokens and
(b) frequently fails to reproduce the original misbehavior.

**File naming.**  One file per agent invocation per round
(per attempt for implement, since implement retries within a
round).  Path is always under the session run directory
(``.evolve/runs/<timestamp>/``):

| Agent              | File pattern                                       |
|--------------------|----------------------------------------------------|
| Implement (Amelia) | ``conversation_loop_{N}_attempt_{M}.md``           |
| Implement summary  | ``conversation_loop_{N}.md`` (last successful attempt, copied for backward compatibility) |
| Draft (Winston+John) | ``draft_conversation_round_{N}.md``               |
| Review (Zara)      | ``review_conversation_round_{N}.md``               |
| Memory curation (Mira) | ``curation_conversation_round_{N}.md``         |
| SPEC archival (Sid) | ``archival_conversation_round_{N}.md``             |
| Sync-readme         | ``sync_readme_conversation.md``                   |
| Dry-run / validate / diff | ``{mode}_conversation.md``                  |
| Party               | ``party_conversation.md``                         |

The names use the **agent role** (not the persona) so logs
remain greppable across model / persona changes.

**What MUST be captured.**  For every message the SDK yields
during the invocation, the file MUST contain (in stream order,
deduplicated by block id where the SDK streams partials):

1. ``SystemMessage`` events — at minimum a marker so the
   session boundary is visible.
2. ``AssistantMessage`` ``ThinkingBlock``s — the model's
   extended-thinking blocks, verbatim.  Thinking is the most
   load-bearing diagnostic signal when an agent goes off the
   rails ("I will now refactor the working code because the
   SPEC suggests it could be cleaner") — losing thinking blocks
   is losing the why.
3. ``AssistantMessage`` ``TextBlock``s — the model's plain
   reasoning / narration text, verbatim.
4. ``AssistantMessage`` ``ToolUseBlock``s — every tool call
   with **its name and input** (input may be summarised /
   length-capped at a generous limit, e.g. 2000 chars per
   field, but never elided to "..." without the original
   length recorded).
5. ``ToolResultBlock``s — every tool result with
   ``is_error`` flag and content (length-capped per field, with
   the cap recorded in the file header so a reader knows the
   ceiling).
6. ``RateLimitEvent`` markers — visible as ``> Rate limited``
   lines so retry behavior is reconstructible.
7. The final ``ResultMessage`` — full payload: ``subtype``,
   ``is_error``, ``num_turns``, ``stop_reason``,
   ``total_cost_usd``, ``duration_ms``, ``usage``.  This is
   the authoritative termination signal (§ "Authoritative
   termination signal from the SDK") and MUST appear as the
   last entry in the file, formatted on a single
   ``**Result**: subtype=…, num_turns=…, …`` line so it is
   greppable across logs.

**What MAY be omitted.**  Partial streamed deltas of a block
that the SDK ultimately re-emits as a complete block (the
deduplication-by-id step) — only the final consolidated block
is logged.  Nothing else.

**Format.**  Markdown, one section per message kind (``###
Thinking``, ``**ToolName**``, etc.), so a human can scroll the
file top-to-bottom and reconstruct the run.  Code-block fences
around tool outputs.  No JSON dumps in place of human-readable
sections — the file is for humans first, machines second.

**Real-time write — no buffering.**  Each entry MUST be flushed
to disk **as it is received from the SDK stream**, not buffered
until the agent finishes.  Concretely: open every conversation
log with line-buffering (``open(path, "w", buffering=1)``) or
call ``flush()`` after every ``write()``.  The default Python
file buffering (~4–8 KB block buffer) is forbidden for these
files because it defeats the primary use case:

1. **Live tailing.**  Operators run ``tail -f
   .evolve/runs/<latest>/conversation_loop_N.md`` to watch an
   agent reason in real time.  A buffered log shows nothing
   for minutes, then dumps the whole transcript at once when
   the agent finishes — useless for spotting a stuck agent
   before the watchdog kills it.
2. **Crash forensics.**  When an agent hangs (rate-limited
   forever, infinite tool-loop, OOM) and the watchdog SIGKILLs
   the round subprocess, an unbuffered log preserves
   everything up to the kill instant.  A buffered log loses the
   final 4 KB — which is precisely the part that explains why
   the agent got stuck.
3. **Mid-round operator inspection.**  ``grep "Rate limited"
   .evolve/runs/<latest>/*.md`` only works during a long round
   if the writes have actually hit disk.  Same for any
   live-debugging workflow that opens the log alongside the
   running agent.

The cost of line buffering is negligible (one ``write`` syscall
per ``\n`` instead of one per buffer fill); the diagnostic
value is enormous.  A test MUST exist that writes to a
conversation log path and asserts the on-disk byte count
strictly increases between successive writes — guarding against
a future regression that re-introduces block buffering.

**Length cap exemption from the 500-line rule.**  These
conversation files are data, not source code, and routinely
exceed the project's 500-line cap on Python files (§ "Hard
rule: source files MUST NOT exceed 500 lines").  They are
explicitly out of scope for that rule.

**Operator workflow.**  When a round misbehaves, the operator
opens ``.evolve/runs/<latest>/`` and reads the agent file
matching the suspect role.  ``grep -l "subtype=error_max_turns"
.evolve/runs/<latest>/*.md`` immediately surfaces every agent
that hit the turn cap; ``grep "Rate limited"`` surfaces
throttling; ``grep "is_error=True"`` surfaces tool failures.
This is the contract these files exist to deliver.
