# 002 — Multi-call Round Architecture: Design History

Archived from SPEC.md § Multi-call round architecture on 2026-04-27.
Trigger: SPEC.md > 2000 lines (3075 lines at archival time).

---

## Rationale for the three-call split

Earlier versions asked one Opus call to do
everything: run Phase 1 (errors-first), Phase 2 (freshness gate),
Phase 3 (draft + implement), Phase 3.5 (structural self-detection),
Phase 3.6 (Zara adversarial review), Phase 4 (convergence).
Within that one call the agent had to role-play four personas
(Winston, John, Amelia, Zara) and decide when to stop.  Symptoms:

- 300+ second rounds with the agent drifting between phases.
- Personas mixing — Amelia's implementation contaminated by
  Winston's drafting mid-turn.
- "Stop the round" verbal instruction that the model could
  interpret as "keep doing tool calls" because there was no
  structural exit point.
- Opaque failures — when a round failed, no clear signal pointing
  at which phase broke down.

Splitting the work restores clarity: one call = one persona = one
deliverable = one exit point.

## Cost and latency projections

| Call          | Typical cost       | Typical duration |
|---------------|--------------------|------------------|
| draft_agent   | ~$0.002 / round    | 20-40 s          |
| implement     | ~$0.02 / round     | 60-180 s         |
| review_agent  | ~$0.002 / round    | 15-30 s          |
| **Total**     | **~$0.025 / round**| **~120-240 s**   |

Compared to the single-call round at ~$0.05+ and 200-400+ s with
heavy drift, the pipeline is roughly 2x cheaper and more
predictable.  The prompt-caching gains compound: each agent's
prompt prefix is deterministic across rounds of the same kind,
so the cache-hit rate on draft/review calls approaches
100% after the first round.

## Migration strategy

Rather than a single large refactor, the migration lands as three
independent US items, each touching one call:

- Extract ``review_agent`` (Zara) — lowest risk, Zara is already
  conceptually separate.
- Extract ``draft_agent`` (Winston + John) — medium risk, replaces
  Phase 2 rebuild logic.
- Slim ``implement_agent`` (Amelia) — highest risk, touches the
  core ``analyze_and_fix`` path.

Each extraction's acceptance criteria include: (a) the new agent
runs as a separate SDK call, (b) its prompt file is dedicated,
(c) the main ``prompts/system.md`` is slimmed by the
corresponding section, (d) existing tests are updated to match,
(e) a session-level integration test proves the pipeline executes
all three calls in order on a typical round.
