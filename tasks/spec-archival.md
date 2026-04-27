# SPEC Archival — evolve-native

Protocol for the Sid persona (`agents/archivist.md`) when the
orchestrator triggers a SPEC compaction pass.

## When this fires

The orchestrator triggers archival **between rounds** (after the
post-check, before the next round's pre-check) when ANY of:

1. `SPEC.md` (or the `--spec` target) exceeds **2000 lines** — the
   soft cap.  Above this, the agent's system prompt bloats and
   round latency / cost increases measurably.
2. The rolling round counter hits a multiple of **20** — periodic
   safety net so long-running `--forever` sessions don't accumulate
   forever.
3. The operator explicitly requests it (future extension; not
   required for the initial implementation).

When neither condition holds, archival is skipped — Sid does not run
on every round.

## Persona

Sid, `agents/archivist.md`.  Explicitly NOT the current round's dev
persona (Amelia) — separating the archiving role from the
implementing role avoids "I need this section" bias.

## Model and effort

- **Model:** `claude-sonnet-4-20250514` (not Opus — archival is
  triage, not architectural reasoning; Sonnet is ~5x cheaper).
- **Effort:** `low`.
- **Budget:** `max_turns=MAX_TURNS` (centralized constant).

## Input scope

Sid receives exactly these artifacts:

1. **Current SPEC.md** — the full file, primary focus.
2. **`SPEC/archive/INDEX.md`** — existing archive catalog (if any)
   so Sid knows the next available ID.
3. **`git log --oneline -30`** — to correlate sections against
   actual commits and assess staleness.

Sid does **not** receive:

- Prior archival audit logs (each archival is fresh).
- `memory.md` or `improvements.md` (irrelevant to SPEC triage).
- The current round's US draft.

## Archival attack plan

Four passes, deterministic order.

### Pass 1 — Stability detection

For each top-level or second-level section in SPEC.md, classify:

- **Active** — describes current behavior, constraints, or contracts
  the agent must implement. KEEP.
- **Stable** — describes a mechanism that works and is rarely touched.
  KEEP for now (unless very long).
- **Historical** — describes a completed migration, one-shot example
  material, or a design decision whose only value is archaeological.
  → Archive candidate.

### Pass 2 — Stub drafting

For each archive candidate, draft a 2-5 line summary capturing:

- The **conclusion** (what happened, what the current state is)
- A **conditional pointer** to the archive file
- A note about when to read the archive (diagnostic only)

The stub MUST be strictly shorter (in lines) than the archived
section body.

### Pass 3 — Archive extraction

For each candidate:

1. Determine the next archive ID: `max(existing IDs in INDEX.md) + 1`,
   zero-padded to 3 digits.
2. Write the full section to `SPEC/archive/NNN-<slug>.md`.
3. Update `SPEC/archive/INDEX.md` with a new row.

### Pass 4 — SPEC rewrite

Replace each archived section's body in SPEC.md with its stub.
Do NOT reorder sections. Do NOT change non-archived sections.

## Output

Two+ files.

**1. Updated SPEC.md** — in-place rewrite. The rewrite must satisfy:

- If the rewrite shrinks by > 80%, the archival is ABORTED and the
  original restored — safety net against a mis-prompted Sid
  removing too much.

**2. Archive files** at `SPEC/archive/NNN-<slug>.md` — one per
archived section.

**3. Updated INDEX.md** at `SPEC/archive/INDEX.md`:

```markdown
# SPEC Archive Index

| ID  | Slug                    | Archived | Trigger                  |
|-----|-------------------------|----------|--------------------------|
| 001 | migration-strategy      | 2026-04-27 | SPEC > 2000 lines, round 15 |
| 002 | cicd-integration        | 2026-04-27 | SPEC > 2000 lines, round 15 |
```

**4. Audit log** at `{run_dir}/spec_curation_round_{N}.md`:

```markdown
# Round N — SPEC Archival (Sid)

**SPEC.md before:** <line count> lines / <byte count> bytes
**SPEC.md after:**  <line count> lines / <byte count> bytes
**Decisions:** X KEEP, Y ARCHIVE

## Ledger

| Section                        | Decision | Reason (≤ 80 chars)              |
|--------------------------------|----------|----------------------------------|
| Architecture                   | KEEP     | Active contract — agent needs it. |
| Package migration (steps 1-10) | ARCHIVE  | Completed; shims removed.        |
| ...                            | ...      | ...                              |

## Narrative (≤ 5 sentences)

<What changed overall, what the operator should know.>
```

## Verdict → orchestrator action

| Outcome    | Condition                                            | Action                                                           |
|------------|------------------------------------------------------|------------------------------------------------------------------|
| ARCHIVED   | SPEC rewritten, shrink ≤ 80%, audit log present      | Orchestrator commits with descriptive message. Ledger preserved. |
| SKIPPED    | Neither line cap nor periodic trigger fired           | No archival run. Next round proceeds normally.                   |
| ABORTED    | Archival would shrink by > 80%                        | Original SPEC restored. Audit log saved with verdict: ABORTED.   |
| SDK FAIL   | Agent returned no audit log, or schema malformed      | Original SPEC restored. Warn operator. Next round proceeds.      |
