# 014 — Memory Curation Protocol (Mira)

> Archived from SPEC.md § "Dedicated memory curation (Mira)" on
> 2026-04-27. Stable protocol — Mira's trigger conditions, four passes,
> safeguards, and verdict routing.

---

### Dedicated memory curation (Mira)

The main round agent's "append-only during work, compact past 500
lines" contract has a structural weakness: the same entity that
*writes* memory entries is asked to *decide* which past entries
stay in the working prompt.  This produces two failure modes in
practice:

1. **Authored-it-must-stay bias.**  The agent keeps its own recent
   entries even when they've become historical noise, because
   removing them feels like discarding its own work.
2. **Turn-budget contamination.**  Asking the same agent that just
   spent a turn implementing a US to also compact memory inflates
   the turn budget and risks a "compact aggressively to save
   context" shortcut that silently wipes real signal.

The orchestrator therefore spawns a **dedicated curator agent
(Mira, `agents/curator.md`)** with a single narrow job: triage the
existing `memory.md` into KEEP / ARCHIVE / DELETE decisions.  The
full protocol is in `tasks/memory-curation.md`; in short:

- **When.**  Between rounds (after post-check, before the next
  round's pre-check) when ANY of: `memory.md` > 300 lines (soft
  cap); rolling round counter is a multiple of 10; explicit
  operator request.  Skipped otherwise — Mira does not run on
  every round.
- **Persona.**  Mira is NOT the round's draft/implement persona
  (Winston / John / Amelia) and NOT the reviewer (Zara).  Fresh
  eyes, no authorship bias.
- **Model + effort.**  Opus (centralized ``MODEL``), ``effort=low``,
  ``max_turns=MAX_TURNS`` — see § "Single model: Opus everywhere"
  for the rationale.  Curation is triage, not architectural
  reasoning, but Opus at low effort still avoids the misclassification
  errors Sonnet produced on memory-triage decisions.
- **Input scope.**  Current `memory.md` + SPEC § "memory.md" + last
  5 rounds' conversation-log titles + `git log --oneline -30`.
  Mira does NOT see prior curation audit logs (each curation is
  fresh — no chain-effect bias where a past curator's mistakes
  propagate).
- **Four passes.**  Duplicate detection → rediscoverability audit
  (can a future agent find this by reading SPEC / code / commit?)
  → historical archival (entries > 20 rounds old with no forward
  signal) → section hygiene (empty sections stay as stubs; section
  order is SPEC-locked; `## Archive` is append-only).
- **Output.**  Rewritten `memory.md` + audit log at
  `{run_dir}/memory_curation_round_{N}.md` with a KEEP / ARCHIVE /
  DELETE ledger and a narrative summary.
- **Safeguards.**
  - The rewrite must include `memory: compaction` in the commit
    message (unchanged from the existing byte-size sanity gate).
  - If the rewrite would shrink `memory.md` by > 80%, the
    curation is **aborted** — original file restored, audit log
    saved with `verdict: ABORTED`, operator warned.  This is a
    belt-and-suspenders guard on top of the 50% gate: a 60-80%
    shrink might be legitimate (big session crossed the cap), but
    > 80% is almost always a prompt misfire.
  - Archive is soft-delete: removed entries land in `## Archive`
    at the bottom of `memory.md`, still on disk, still greppable.
    Only true duplicates are deleted outright, and even those
    leave a trace in the audit ledger.

**Verdict routing.**

| Verdict     | Condition                                              | Orchestrator action                                                       |
|-------------|--------------------------------------------------------|---------------------------------------------------------------------------|
| CURATED     | Rewrite within bounds, audit log present                | Commit with `memory: compaction` marker.  Ledger preserved on disk.       |
| SKIPPED     | Threshold not hit                                      | No curation run; next round proceeds normally.                            |
| ABORTED     | Rewrite would shrink by > 80%                          | Restore original `memory.md`; save audit with `verdict: ABORTED`; warn.   |
| SDK FAIL    | No audit log, or schema malformed                       | Restore original; warn; next round proceeds.                              |

**Why this is worth the extra SDK call.**  Without Mira, the main
agent's memory contract devolves to either (a) append forever and
suffer prompt bloat, or (b) compact during the main turn and risk
wipes.  With Mira, the main agent stays strictly append-only
(simple contract, no bias), and the curator handles the prune in
isolation with a dedicated cheaper model.  Net cost is lower than
the status quo where memory bloat inflates every subsequent
round's prompt budget.
