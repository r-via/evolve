# 006 — Model Selection Rationale (Why Opus Everywhere)

Archived from SPEC.md § Single model: Opus everywhere on 2026-04-27.
Trigger: SPEC.md > 2000 lines (3075 lines at archival time).

---

**Why Opus across the board, not Sonnet for "lighter" agents.**
Earlier versions of evolve used Sonnet for the "non-implement"
agents (draft, review, curation, archival) on the assumption these
were narrow planning / triage tasks where Opus was overkill.  In
practice the opposite happened:

1. **Hallucinated US items.**  Winston + John on Sonnet routinely
   drafted US items whose acceptance criteria were already
   implemented in the codebase (the draft agent did not verify
   current state before writing claims).  These spurious items fed
   the implement agent, which then spent a full round either
   rediscovering "nothing to do" or worse, refactoring working code
   to match an imagined future shape.  Opus catches this with the
   same Glob / Grep discipline it applies in implement rounds.
2. **Adversarial review false positives.**  Zara on Sonnet pivoted
   from real adversarial code review to wording-quality critique of
   US items and AC blocks — flagging HIGH findings on perfectly
   serviceable rounds, which under the auto-fix invariant fed
   churn back into the loop.  Opus reads the actual diff with
   enough context to find or not find genuine regression risk.
3. **Cost calculus changed.**  The supposed savings (~60% token
   cost on draft/review calls) were dwarfed by the cost of the
   *extra rounds* hallucinated US items and false-positive reviews
   triggered.  One bad draft can burn a full implement round
   ($0.50-$2.00 of Opus turns) to confirm the US was already done.
   Saving $0.018/round on review while paying for one extra
   implement round per false-positive is net negative.
4. **One knob to tune.**  Centralizing on ``MODEL`` means model
   upgrades (4.7 -> 4.8, etc.) propagate to every agent in one
   edit — no risk of forgetting to upgrade Mira while bumping
   Amelia, no version skew between personas working on the same
   round's artifacts.

**Single effort: medium across the board.**  Earlier versions of
evolve used ``effort=low`` for the planning / review / triage
agents on the assumption these were "narrow" tasks where low
effort would suffice.  In practice low effort produced exactly the
hallucinated-US and false-positive-review failure modes described
above — the agents skipped verification steps that medium effort
would have done by default.  All callsites now pass
``effort=EFFORT`` (the centralized ``evolve.agent.EFFORT``
constant, default ``"medium"``).  The CLI ``--effort`` flag still
overrides for the whole session uniformly — there is no per-agent
effort knob, exactly as there is no per-agent model knob.

**Mandatory pre-draft verification.**  In conjunction with the
medium effort policy, ``prompts/draft.md`` mandates a "Step 0 —
Verify the claim is genuinely missing" block in the draft agent's
conversation log, ahead of Winston's architectural pass.  Step 0
requires concrete ``Grep`` / ``Glob`` / ``Read`` evidence for
every candidate claim, and explicit rejection of candidates whose
evidence shows them implemented.  A draft committed without a
visible Step 0 block — or whose Step 0 block lacks evidence for
the surviving candidate — is treated as a failed draft round and
rolled back on the next attempt.  This rule directly fixes the
US-026 false-positive class: the draft agent reading a SPEC
section about an already-implemented architecture feature and
mistaking the SPEC description for a missing claim.
