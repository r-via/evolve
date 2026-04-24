# Memory Curation — evolve-native

Protocol for the Mira persona (`agents/curator.md`) when the
orchestrator triggers an end-of-round memory compaction.

## When this fires

The orchestrator triggers curation **between rounds** (after the
post-check, before the next round's pre-check) when ANY of:

1. `runs/memory.md` (or `.evolve/runs/memory.md`) exceeds
   **300 lines** — the soft cap.  Above this, the main agent starts
   paying prompt-size cost that doesn't correlate with cross-round
   usefulness.
2. The rolling round counter hits a multiple of **10** — periodic
   safety net so long-running `--forever` sessions don't accumulate
   forever just because no single round crossed the line threshold.
3. The operator explicitly requests it via a CLI flag (future
   extension; not required for the initial implementation).

When neither condition holds, curation is skipped — Mira does not
run on every round (the cost would dominate tiny changes).

## Persona

Mira, `agents/curator.md`.  Explicitly NOT the current round's dev
persona (Amelia) — separating the authoring role from the pruning
role protects against "I wrote it, it must stay" bias.

## Model and effort

- **Model:** `claude-sonnet-4-20250514` (not Opus — curation is
  triage, not architectural reasoning; Sonnet is ~5× cheaper).
- **Effort:** `low`.
- **Budget:** one turn per curation, no retries.  If Sonnet can't
  triage the memory in one turn, the memory file is too large for
  any single agent pass and the issue is escalated to the
  operator (exit code 2 with a dedicated diagnostic).

## Input scope

Mira receives exactly these artifacts:

1. **Current `memory.md`** — the full file, primary focus.
2. **SPEC.md § "memory.md — cumulative learning log"** — the
   authoritative discipline (length, telegraphic style, non-obvious
   gate).  Mira enforces the spec, she does not reinterpret it.
3. **Last 5 rounds' `conversation_loop_*.md` digests** — title lines
   only (no full content) so Mira can recognise which entries refer
   to work that's still fresh vs. ancient.
4. **`git log --oneline -30`** — to correlate entries against
   actual commits.

Mira does **not** receive:

- Prior curation audit logs (each curation is fresh — no chain
  effect, no "previous curator already archived this" bias).
- The current round's US draft (irrelevant to memory discipline).

## Curation attack plan

Four passes, deterministic order.

### Pass 1 — Duplicate detection

1. Within each section (`## Errors`, `## Decisions`, …), identify
   entries with overlapping subject matter.
2. For true duplicates (same fact, different rounds): keep the
   canonical entry (earliest occurrence), merge any new detail
   from the duplicate into the canonical body, **DELETE** the
   duplicate.
3. For near-duplicates that reinforce each other: keep both if
   telegraphic, merge into one if both are verbose.

### Pass 2 — Rediscoverability audit

For each remaining entry, apply the SPEC's non-obvious gate:

> *"Would a future agent reading this in 10 rounds care, or
> could they rediscover the info by re-reading SPEC.md / the code
> / the commit?"*

- If rediscoverable → **ARCHIVE**.
- If still non-obvious and forward-looking → **KEEP**.

### Pass 3 — Historical entry archival

Entries that read as "round X did Y because Z" where:
- The referenced round is older than 20 rounds back, AND
- No subsequent entry references it, AND
- The fact is either documented in SPEC.md or obvious from the
  commit,

→ **ARCHIVE** to `## Archive` at the bottom of `memory.md`.

### Pass 4 — Section hygiene

- Empty sections (no entries left after passes 1-3) stay in the
  file as empty stubs — the SPEC's structured-section discipline
  relies on the section headers being stable.
- Section order is SPEC-defined and MUST NOT be reordered.
- The `## Archive` section grows append-only; never compact or
  reorder archive entries.

## Output

Two files.

**1. Updated `memory.md`** — in-place rewrite.  Byte-size change
from original must satisfy **both**:

- If the rewrite shrinks the file by ≥ 50%, the commit message
  MUST include `memory: compaction` on its own line, per the
  orchestrator's byte-size sanity gate (SPEC § "Byte-size sanity
  gate").  Mira writes this marker unconditionally when her
  changes delete or archive anything.
- If the rewrite shrinks the file by > 80%, the curation is
  aborted and the original `memory.md` is restored — this is a
  safety net against a mis-prompted Mira wiping too much.  The
  audit log records the aborted pass.

**2. Audit log** at `{run_dir}/memory_curation_round_{N}.md`:

```markdown
# Round N — Memory Curation (Mira)

**memory.md before:** Lines / bytes
**memory.md after:**  Lines / bytes
**Decisions:** X KEEP, Y ARCHIVE, Z DELETE

## Ledger

| Section    | Title (round ref)                  | Decision  | Reason (≤ 80 chars)                   |
|------------|------------------------------------|-----------|---------------------------------------|
| Errors     | SDK stub stale attribute — round 2 | ARCHIVE   | Fixed in 25d1a66; now in commit msg.  |
| Decisions  | Phase 1 escape hatch — round 1     | KEEP      | Still referenced by Phase 1 prompt.   |
| Decisions  | Attempt counter plumbing — round 1 | DELETE    | Duplicate of entry below.             |
| ...        | ...                                | ...       | ...                                   |

## Narrative (≤ 5 sentences)

<What changed overall, whether any entry was borderline, what the
operator should know.>
```

The audit file is committed alongside `memory.md` and becomes
part of the evolution audit trail.

## Orchestrator → Mira contract

The orchestrator spawns a **separate SDK call** (not a role-play
within the main agent's turn) so Mira's context is isolated and
cheap.  The call contract:

- Model: Sonnet 4.6 (cheaper than Opus).
- Effort: `low`.
- `max_turns`: 1.
- Prompt: the curation instructions above, interpolated with the
  current `memory.md` and the digest inputs.
- Output parsing: the orchestrator reads the generated
  `memory_curation_round_{N}.md` audit file to verify the decision
  ledger is present and the byte-size change is within bounds
  before staging the `memory.md` rewrite for the curation commit.

## Verdict → orchestrator action

| Outcome                    | Condition                                                       | Action                                                                                              |
|----------------------------|-----------------------------------------------------------------|-----------------------------------------------------------------------------------------------------|
| CURATED                    | `memory.md` rewritten, byte change ≤ 80%, audit log present      | Orchestrator commits with `memory: compaction` marker.  Ledger preserved on disk.                    |
| SKIPPED (threshold not hit)| Neither line cap nor periodic trigger fired                      | No curation run.  Next round proceeds normally.                                                     |
| ABORTED (oversized wipe)   | Curation would shrink by > 80% — clear prompt misfire            | Original `memory.md` restored.  Audit log saved with `verdict: ABORTED`.  Orchestrator warns via `ui.warn` and proceeds without committing a curation. |
| SDK FAIL                   | Sonnet returned no audit log, or audit schema malformed          | Original `memory.md` restored.  Warn the operator via `ui.warn`.  No exit; next round proceeds.      |

## Why a dedicated curator

The main round agent:
- Has just spent its turn implementing a US; compacting memory on
  top of that inflates its turn budget and contaminates its
  context with curation reasoning.
- Is biased to keep its own recent entries (authored-it-must-stay).
- Does not have the cross-round perspective — it sees one US, not
  the memory-wide duplication pattern.

Mira:
- Has a single narrow job with a 300-line input and a ≤ 300-line
  output.
- Never wrote any of the entries — no authorship bias.
- Runs in a cheap model with low effort; cost-friendly.
