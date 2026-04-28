# Zero-progress diagnostic carve-outs

*Archived from SPEC.md on 2026-04-28. These are two special-case
carve-outs within the "Zero progress" detection mechanism in
"Subprocess monitoring & debug retries".*

---

## Carve-out: scope creep (rebuild + implement in one round)

A round that adds new `[ ]` items to `improvements.md` AND
modifies non-improvements files (code / tests / docs) in the
same commit is mixing two round kinds: Phase 2 backlog rebuild
and Phase 3 implementation.  Earlier versions of the system
prompt explicitly encouraged this ("your round target becomes
the FIRST of the newly rebuilt items") and the symptom reported
by operators was exactly that pattern: a rebuild round that
drafts multiple US items AND starts coding the first one, 300+
seconds per round, no clean commit boundary between planning
output and code changes.

The orchestrator now detects the mix -- `backlog_new_items > 0`
AND `git diff-tree --name-only HEAD` lists files outside
`improvements.md` / `memory.md` / the runs base -- and emits
a dedicated `SCOPE CREEP:` diagnostic.  `build_prompt` in
`agent.py` surfaces a `## CRITICAL -- Scope creep: rebuild
mixed with implementation` section instructing the retry to:

1. `git reset HEAD~1` (if the commit went through).
2. Stage ONLY the `improvements.md` rebuild.
3. Discard the code / test / doc edits -- the next round's fresh
   agent will re-derive them from the rebuilt backlog.
4. Write `chore(spec): rebuild backlog after spec change` (or
   similar) and stop the round.

The next round picks up the first new item and implements it
cleanly.  Rebuild rounds produce a clean commit boundary
between planning and coding; the git history shows them as
distinct actions rather than one mashed-up commit.

---

## Carve-out: backlog drained, CONVERGED skipped

There is one case where `imp_unchanged=True` + `no_commit_msg=True`
is *not* a failure: every `[ ]` item in `improvements.md` has
been checked off but the agent stopped short of writing
`CONVERGED`.  The round had nothing to implement -- the correct
next step is Phase 4 (verify README claims, then converge), not a
zero-progress retry that pushes the agent to fabricate filler
work.

The orchestrator detects this state -- `_count_unchecked(imp) ==
0` AND `imp_unchanged` AND no `CONVERGED` marker -- and emits
a dedicated `BACKLOG DRAINED: all [ ] items checked off, but
agent did not write CONVERGED` diagnostic instead of the generic
`NO PROGRESS` one.  `build_prompt` in `agent.py` recognises
the prefix and surfaces a `## CRITICAL -- Backlog drained,
CONVERGED skipped` section that steers the retry straight to
Phase 4 (re-read the spec line by line, verify each claim, write
`CONVERGED` or add exactly one new US item covering a genuinely
missing claim).  Explicit guard in the prompt: do NOT fabricate
filler improvements to make the round look productive -- that is
worse than not converging.
