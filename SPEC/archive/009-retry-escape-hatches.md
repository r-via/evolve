# 009 — Retry Continuity and Phase 1 Escape Hatch

> Archived from SPEC.md § "Retry continuity" and § "Phase 1 escape hatch
> for unrelated pre-existing failures" on 2026-04-27. Stable protocols —
> designed once, implemented, not changing.

---

### Retry continuity

Debug retries of the **same round** must reuse the previous attempt's work;
otherwise the retry wastes 40 turns re-discovering the same facts. Three rules
implement this:

1. **Per-attempt log files.** Each attempt of round N writes to its own file:
   `conversation_loop_N_attempt_1.md`, `conversation_loop_N_attempt_2.md`, etc.
   Nothing is overwritten. The final successful attempt is also symlinked (or
   copied) to `conversation_loop_N.md` for backward compatibility with
   downstream consumers (report generation, party mode, self-monitoring).
2. **Enriched diagnostic in the retry prompt.** Instead of just the last 3000
   chars of output, the debug retry prompt includes a dedicated
   `## Previous attempt log` section with the **full path** to
   `conversation_loop_N_attempt_{K-1}.md` and an explicit instruction:
   *"Read this file first. It contains everything the previous attempt
   already discovered — the tool calls, the dead ends, the working
   hypotheses. Do not redo that investigation. Continue from where it
   stopped."*
3. **Retry-aware self-monitoring.** The agent's first action in any round is
   to check for prior attempts of the **current** round number on disk (glob
   `conversation_loop_{current_round}_attempt_*.md`). If any exist, read them
   **before** looking at rounds N-1 / N-2 — they carry the most relevant
   context by far. The N-1 / N-2 check remains, but runs after.

This closes the gap where retries are currently blind to their own prior
attempt: the first attempt crashes after 40 turns of investigation, the
diagnostic gives the retry a 3000-char snippet (usually just the error
traceback), and the retry restarts the same investigation from scratch.
Treating each round as a continuum of attempts — not a fresh start each
time — is the difference between 3 wasted retries and 3 retries that each
progress further.

### Phase 1 escape hatch for unrelated pre-existing failures

Phase 1 is mandatory by contract: *fix any failure from the check command
before touching the current improvement target*. In practice this is the
right default — broken tests or lint errors usually point at regressions
introduced by the previous round and must be cleared first. But it produces
a specific lockup when:

- The failures are **pre-existing and unrelated** to the current target
  (e.g. flaky environment-specific issues, test-isolation bugs, third-party
  regressions that are genuinely someone else's problem)
- They resist two debug retries
- The agent has spent its turn budget on diagnosis without producing any
  fix

In that situation the round cannot make *any* progress — not on Phase 1,
not on the target — and keeps consuming retries and rounds until
`max_rounds`. Rounds 1-5 of session `20260423_134609` are the canonical
example: 50+ cumulative tool calls across attempts investigating a Rich
`Style.parse` LRU cache issue, zero code edits, same target re-picked
every round.

**The escape hatch.** When **all** of the following hold:

- The round is on its **second debug retry** (attempt 3, the final one
  before exhaustion)
- Phase 1 errors are still present
- The failing tests / check output touch **none** of the files the current
  improvement target names (verified by scanning the target text for file
  references and cross-checking against the failing output)

...the agent is permitted to:

1. Log the failing tests to `memory.md` under a new `## Blocked Errors`
   section with the full check output excerpt, timestamps, and a note
   explaining the Phase 1 bypass
2. Append a dedicated high-priority item to `improvements.md`:
   `[ ] [functional] Phase 1 bypass: fix pre-existing failures (<short
   summary>) that blocked round N — see memory.md § Blocked Errors`
3. **Proceed with the original Phase 3 target** for this attempt, treating
   the pre-existing failures as known-broken state to work around (e.g.
   run tests with `-k 'not broken_test'` or equivalent while building and
   verifying the target's changes)
4. At commit time, include in `COMMIT_MSG` a top-level line
   `Phase 1 bypass: <short summary>` so the escape hatch is visible in
   git history and the report

The bypass does **not** apply when:

- The failures reference files in the target's scope (those MUST be fixed
  first — they're the target's responsibility)
- The round has retries remaining (the retry might yet resolve the issue)
- The failures are the regression introduced by the current target (those
  mean the target was implemented incorrectly, not that Phase 1 is
  unrelated)

This is a **deliberate hole in the "fix errors first" rule**, narrow enough
that it only triggers on genuinely unresolvable pre-existing state, and
loud enough (via memory.md + improvements.md + commit message) that the
bypass never goes unnoticed. The orphaned errors become the top-priority
item for the next cycle and get dedicated attention instead of starving
every round.
