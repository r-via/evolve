# SPEC Archival — Full Implementation Detail

> Archived from SPEC.md § "SPEC archival (Sid)" on 2026-04-27.
> Feature implemented in commit `bdd16e5` (US-025). Protocol now lives
> in `agents/archivist.md` + `tasks/spec-archival.md`.

---

SPEC.md accumulates content monotonically as features land —
completed migrations, one-shot CI/CD examples, TUI implementation
details, historical design decisions.  These stay in every agent
prompt forever, blowing the context budget without earning their
keep after the feature is stable.  At the time of writing SPEC is
at 2474 lines / ~110 KB — the feedback from the adversarial
reviewer (Zara) during round 1 of session 20260424_145954 named
this as a maintainability risk.

**Persona — Sid (SPEC Archivist, ``agents/archivist.md``).**  A
parallel of Mira (memory curator) for ``SPEC.md``.  Same
discipline, different input: Sid reads SPEC.md, identifies
stable / historical sections, extracts them to
``SPEC/archive/NNN-<slug>.md``, and leaves a short summary stub
in SPEC.md pointing at the archive.  Opus (centralized ``MODEL``),
``effort=low``, ``max_turns=MAX_TURNS`` — runs between rounds not
during them, never touches active contracts.

**Trigger conditions.**  Between rounds (after post-check,
before next round's pre-check) when ANY of:

1. ``SPEC.md > 2000 lines`` (soft cap — the point at which
   prompt bloat starts measurably hurting round latency and cost).
2. Rolling round counter hits a multiple of 20 (periodic safety
   net so long ``--forever`` sessions don't accumulate forever).
3. Operator explicit request (``evolve archive-spec`` — deferred
   to a later US).

**Archive directory layout.**

```
<project>/
├── SPEC.md                           # active contracts, ≤ 2000 lines
└── SPEC/
    └── archive/
        ├── INDEX.md                  # catalog: ID → slug → archive date → trigger context
        ├── 001-migration-strategy.md # completed package restructure (rounds 5-22)
        ├── 002-cicd-integration.md   # GitHub Actions examples
        ├── 003-frame-capture-design.md
        └── 004-tui-internals.md
```

**Stub format in SPEC.md.**  Every archived section leaves a 2-5
line stub at its original location:

```markdown
## Architecture — package migration (archived)

The flat-module layout (loop.py, agent.py, tui.py, costs.py,
hooks.py at project root) was extracted into the ``evolve/``
package over rounds 5-22 (steps 1-10).  Completed; shims
removed.  Current package layout is authoritative.

→ Full step-by-step history: [`SPEC/archive/001-migration-strategy.md`]
  (Read ONLY if diagnosing a package-structure issue.  For
  normal work, the current code layout + shim absence are the
  truth — the archive adds no current-contract signal.)
```

The stub gives the agent the **conclusion** + a **conditional
pointer**.  No reason to read the archive in a normal round.

**Sid's four passes.**

1. **Stability detection** — for each SPEC section, answer:
   is this a current contract (active), a stable mechanism
   (rarely touched, documented once), or historical (migration
   complete, example not current)?  Active stays.  Stable stays
   for now.  Historical → archive candidate.
2. **Stub drafting** — for each archive candidate, draft a
   2-5 line summary capturing the conclusion + conditional
   link.  The stub MUST be strictly shorter than the archived
   content.
3. **Archive extraction** — write the full section to
   ``SPEC/archive/NNN-<slug>.md`` with an ID one higher than
   the current max in ``SPEC/archive/INDEX.md``.  Never reuse
   an ID.
4. **SPEC rewrite** — replace the section body in SPEC.md with
   its stub, update ``SPEC/archive/INDEX.md``.

**Output.**  Updated ``SPEC.md`` + new archive file(s) + updated
``INDEX.md`` + audit log at
``{run_dir}/spec_curation_round_{N}.md`` with a
KEEP / ARCHIVE ledger per section and a narrative summary.

**Read discipline — defense against the read-back loop.**

Zara's concern about archives: *"if the agent follows the link,
it ends up loading it anyway, defeating the purpose"*.  Three
layers of defense:

1. **Physical separation.**  Archives live under ``SPEC/archive/``
   not SPEC.md.  The agent's default context (prompt + README +
   SPEC.md + memory.md + improvements.md) does NOT include them.
2. **Explicit permission rule in ``prompts/system.md``:**

   > ``SPEC/archive/*.md`` are historical records, NOT current
   > contract.  You MUST NOT read them unless ALL of:
   >
   > 1. The current US's target explicitly references a concept
   >    that a SPEC.md stub points to in the archive.
   > 2. The stub's summary is insufficient for the target.
   > 3. You have already read the non-archive sources (SPEC.md,
   >    code, memory.md).
   >
   > The orchestrator logs every Read of ``SPEC/archive/*.md`` to
   > ``{runs_base}/memory.md`` under ``## Archive reads`` with
   > round + justification.  Three archive reads in a single
   > round without justification = scope creep, flagged by Zara
   > at Phase 3.6 review.
3. **Orchestrator-side observability.**  The round subprocess
   inspects its own conversation log post-round for
   ``Read → SPEC/archive/`` patterns.  Each occurrence is
   counted and written to ``memory.md`` under
   ``## Archive reads``.  Zara's Phase 3.6 review reads those
   counts: > 1 read per round without a matching stub
   reference in the US target → severity MEDIUM finding.

**Acceptance criteria:**

1. ``agents/archivist.md`` persona file exists, defining Sid's
   role (parallel of Mira).
2. ``tasks/spec-archival.md`` protocol document exists,
   describing the four passes and the output contract.
3. Orchestrator helper ``_should_run_spec_archival(project_dir,
   round_num)`` returns True only when the trigger conditions
   hold.
4. ``run_spec_archival(project_dir, run_dir)`` in ``evolve/agent.py``
   spawns Sid via the centralized ``MODEL`` + ``effort=low``, writes the
   ``spec_curation_round_N.md`` audit log, and applies the
   rewrite iff the audit log is well-formed.
5. ``SPEC/archive/INDEX.md`` is created (or updated) on the
   first archival pass.
6. ``prompts/system.md`` gains the archive-read discipline
   section with the three-condition gate.
7. Zara's Phase 3.6 review attack plan (``tasks/
   review-adversarial-round.md``) gains an "archive read
   count" signal in Pass 2 (claim-vs-reality).
8. Tests in ``tests/test_spec_archival.py`` cover: trigger
   conditions, four passes on a synthetic SPEC, stub
   shorter-than-body invariant, INDEX.md ID monotonic, audit
   log schema.

**Interaction with prompt caching.**  Combined with the caching
contract (above), the effect compounds:

- The Claude Code CLI's native caching gives ~90% discount on
  the **stable leading prefix** of the system prompt across
  calls within the TTL — that prefix is typically system.md +
  SPEC.md + README.md.
- Archival reduces the volume of that leading prefix by ~30-40%
  over time as stable sections move out, so the first round of
  a session (cache write) is cheaper too.

The two levers are orthogonal: caching attacks the per-call
cost of repeated content (cache reads on round N > 1);
archival attacks the intrinsic volume of what gets included in
every call (cheaper cache writes on round 1).  Both should
land.

**Migration bootstrap — what to archive first.**

The initial archival pass has clear candidates that are
definitively historical (migration complete, example material):

- **Package restructuring migration** (steps 1-10, done rounds
  5-22) — historical record, no forward signal.
- **CI/CD integration examples** — GitHub Actions workflow
  templates, reference material for operators.
- **Development / Test structure details** — how to contribute
  to evolve, outside agent scope.
- **Detailed TUI internals** — the code is self-documenting.
- **Duplicate ``## Cost Summary`` section** (appears at lines
  946 and 2071 — SPEC bug, pick one, archive the other).

These alone should bring SPEC.md from 2474 → ~1600 lines.
