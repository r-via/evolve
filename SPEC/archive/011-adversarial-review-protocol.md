# 011 — Adversarial Round Review Protocol (Phase 3.6)

> Archived from SPEC.md § "Adversarial round review (Phase 3.6)" on
> 2026-04-27. Stable protocol — Zara's four-pass review, verdict routing,
> interaction with retry mechanisms.

---

### Adversarial round review (Phase 3.6)

After each **implement** round's commit — but before the Phase 4
convergence check — the agent role-plays a dedicated adversarial
reviewer persona (**Zara**, `agents/reviewer.md`) and runs a
skeptical audit of the round's work.  This closes the self-
assessment conflict of interest: without an adversarial pass, the
same agent that drafted the US, implemented it, and decides the
checkoff produces "looks good" reviews by default, and the
cumulative quality of `improvements.md` drifts downward.

**Scope: implement rounds only.**  Zara is **skipped** on draft
rounds (the branch taken when the backlog is drained and Winston +
John write a new ``[ ]`` US into ``improvements.md``).  Three
reasons:

1. **No adversarial code surface.**  A draft round produces only a
   text edit to ``improvements.md`` and a ``COMMIT_MSG`` — there is
   no code, no tests, no behavior change for Zara's four attack
   passes (regression risk, AC compliance, SPEC normative checks,
   structural drift) to bite on.
2. **Drafting is already dual-reviewed.**  The draft agent runs
   Winston (architect) and John (PM) as an internal two-pass review
   of the US before writing it (architecture pattern fit, value /
   priority sanity).  Adding Zara on top is a third reviewer
   reviewing planning, not code.
3. **Empirically high false-positive rate.**  When Zara was run on
   draft rounds she pivoted to wording-quality critique of the US
   (vague AC, fuzzy scope) and routinely returned BLOCKED /
   CHANGES REQUESTED verdicts on perfectly serviceable drafts —
   feeding the auto-retry loop (§ "Verdict → orchestrator action")
   with non-actionable churn.  Skipping Zara on drafts removes the
   noise without losing real signal.

The orchestrator emits ``draft round — skipping review (Zara
reviews implement rounds only)`` when the skip path runs, so the
behavior is observable in the log.

**Persona separation.**  Zara is NOT the same persona as Winston
(architect — drafted the US), John (PM — validated value/priority),
or Amelia (dev — implemented the story).  Persona mixing defeats
the purpose: the drafter and implementer already believe the work
is good.  Zara's mandate is to find what they glossed over.

**Input scope.**  Zara receives exactly these artifacts:

1. The US item text (the `[x] [type] [priority] US-NNN: ...` line
   plus its AC block and Definition of Done).
2. `git diff HEAD^ HEAD -- .` — the round's commit.
3. `conversation_loop_{round_num}.md` — including the persona
   blocks that led to the US and the dev's implementation block.
4. `SPEC.md` — for normative-statement compliance.
5. `runs/memory.md` (or `.evolve/runs/memory.md`) — cross-round
   context.

Zara does **not** receive:

- Prior round reviews (each round is reviewed fresh — no chain
  effect, no reviewer-colludes-with-past-reviewer failure mode).
- `state.json` or cost data (irrelevant to code quality).

**Four attack passes.** (Full protocol in
`tasks/review-adversarial-round.md`.)

| Pass | Focus                                       | Key failure modes to find                                          |
|------|---------------------------------------------|--------------------------------------------------------------------|
| 1    | Acceptance-criteria audit                   | AC classified as PARTIAL / MISSING with no test evidence → HIGH    |
| 2    | Claim-vs-reality (dev narrative vs. diff)   | Claim without diff evidence → HIGH; silent diff hunks → MEDIUM      |
| 3    | Code and test quality                       | Placeholder asserts, swallowed exceptions, self-pass tests         |
| 4    | SPEC-compliance                             | Implementation violates a MUST / MUST NOT in `SPEC.md` → HIGH      |

**Minimum findings.**  Zara produces **at least 3 findings** per
review.  If genuinely clean, she enumerates the three highest-risk
areas checked and cites why each was sound — no "looks good"
reviews.  A review with fewer than 3 findings is suspected of
insufficient scrutiny and triggers a retry of the review itself.

**Output.**  Written to `{run_dir}/review_round_{N}.md` with a
strict schema (verdict, categorised findings, reviewer narrative).
The file is committed alongside the round's other artifacts and
becomes part of the evolution audit trail; the prior-round audit
path (§ "Prior round audit") scans prior review files as an
additional anomaly signal.

**Verdict → orchestrator action.**

| Verdict           | Condition                                                    | Action                                                                                                              |
|-------------------|--------------------------------------------------------------|---------------------------------------------------------------------------------------------------------------------|
| APPROVED          | 0 HIGH findings AND 0 MEDIUM findings                         | Round proceeds to Phase 4 convergence.                                                                              |
| CHANGES REQUESTED | 1-2 HIGH findings, OR any MEDIUM findings                     | Orchestrator writes `subprocess_error_round_{N}.txt` with a `REVIEW: changes requested` prefix listing the HIGH **and** MEDIUM findings; triggers a debug retry (same mechanism as NO PROGRESS / MEMORY WIPED).  The retry reads the review and addresses every HIGH and MEDIUM finding before re-committing. |
| BLOCKED           | ≥ 3 HIGH findings, OR any finding tagged `[regression-risk]` | Same auto-retry path as CHANGES REQUESTED — orchestrator writes `subprocess_error_round_{N}.txt` with a `REVIEW: blocked` prefix and triggers a debug retry to auto-fix the findings.  The deterministic-loop guard (§ "Circuit breakers") caps runaway retries; the operator never arbitrates findings manually. |

**Auto-fix invariant.**  Every HIGH and MEDIUM finding is auto-fixed
by the next attempt — there is **no** verdict that drops the session
into a "manual operator review" state.  Earlier versions exited with
code 2 on BLOCKED; that path was removed because (a) operator
arbitration is exactly the kind of manual loop evolve exists to
eliminate, and (b) the circuit breaker / deterministic-loop guard
already provides the safety net for genuinely unfixable findings.
LOW findings are surfaced in the review file for the audit trail but
do not gate the verdict — they are not auto-fixed inside the round.

The `REVIEW:` diagnostic prefix is recognised by `build_prompt` in
`agent.py` alongside the existing prefixes (NO PROGRESS, MEMORY
WIPED, BACKLOG VIOLATION, PREMATURE CONVERGED) and emits a dedicated
`## CRITICAL — Previous attempt failed adversarial review` section
at the top of the retry prompt, with the HIGH findings expanded.

**Interaction with existing mechanisms.**

- Complements `prev_crash_section` (for orchestrator-level failures)
  and `prev_attempt_section` (for within-round retry continuity).
  Zara's output is for *cross-phase* continuity: the dev persona
  committed successfully, but the work still didn't pass skeptical
  review.
- Complements the circuit breaker: if three successive retries
  produce the same CHANGES REQUESTED verdict with the same HIGH
  signature, the circuit breaker's identical-failure fingerprint
  includes `REVIEW: changes requested` and exit 4 fires.
- The minimum-findings rule (≥ 3) prevents Zara from becoming a
  rubber stamp even on clean code — if she cannot find three
  substantive things to check, she is under-scrutinising.

**Future extensions (not required for initial implementation):**

- Session-end review — a higher-level Zara pass at convergence
  that audits the whole session's story arc (did the backlog make
  sense end-to-end, or did rounds produce churn?).
- Forever-cycle review — between forever-mode cycles, audit the
  SPEC proposal adoption for drift from the original spec intent.
