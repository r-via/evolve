# US Persona Pipeline Protocol (archived from SPEC.md)

> **Archived:** 2026-04-29 | **Trigger:** Every 20 rounds (round 20)
> **Status:** Settled -- three-persona pipeline (Winston -> John -> Amelia) implemented in prompt files; orchestrator pre-commit check enforces US format via regex + required section headers

---

## Forced review sequence -- three personas

The full persona pipeline mirrors a real product-engineering loop:

| Stage       | Persona (file)              | Output                                           |
|-------------|-----------------------------|--------------------------------------------------|
| Draft       | Winston -- Architect (`agents/architect.md`) | Technical design considerations, pattern choice, risks |
| Validate    | John -- PM (`agents/pm.md`)  | User value, priority rationale, explicit non-goals |
| Implement   | Amelia -- Dev (`agents/dev.md`) | Code + tests that satisfy every acceptance criterion |

**When a new item is being added** (Phase 3 step 6 in
`prompts/system.md`, or Phase 2 rebuild) the agent MUST role-play
**Winston -> John -> final draft** in its own conversation log
**before** writing the item to `improvements.md`:

```
### Drafting US-<id> -- architect pass
[Winston speaks -- pattern choice, constraints, risk, integration]
...
### Drafting US-<id> -- PM pass
[John speaks -- user value, priority rationale, explicit non-goals]
...
### US-<id> final draft
[the rendered item exactly as it will land in improvements.md]
```

**When the current target is being implemented** (Phase 3 steps 2-4,
i.e. picking up an existing `[ ]` item and turning it into `[x]`),
the agent MUST role-play **Amelia** for the implementation block:

```
### US-<id> implementation -- dev pass
[Amelia speaks -- ultra-succinct, file paths and AC IDs, one line
per edit, one line per test]
```

Amelia's contract is in `agents/dev.md`: *"All existing and new
tests must pass 100% before story is ready for review.  Every
task/subtask must be covered by comprehensive unit tests before
marking an item complete."*  This is not decorative -- the [x]
checkoff is forbidden until every acceptance criterion has a
corresponding passing test and Amelia has cited the file path
where the criterion is enforced.

The four blocks in the conversation log (Winston draft, John
validate, final draft, Amelia implement) are the audit trail -- an
operator reviewing the round's log can see the persona reasoning
that shaped the item AND the disciplined implementation that closed
it.  The circuit-breaker / prior-round-audit paths can spot
shortcuts (a one-line "Winston: looks fine" with no substantive
reasoning, or an Amelia block with zero file-path citations) ->
retry with stricter instruction.

## Orchestrator check (pre-commit)

Immediately after the existing backlog-discipline rule 1 check
("Backlog discipline"), the orchestrator also verifies that every
newly-added `[ ]` line in the committed `improvements.md` matches
the US header regex (`^- \[ \] \[\w+\](?: \[\w+\])* US-\d{3,}: `)
and that the item body includes the three required section headers
(`**As**`, `**Acceptance criteria`, `**Definition of done`).
Missing any of these triggers a debug-retry diagnostic header
`"CRITICAL -- US format violation: new item lacks required
sections"` and the agent is re-invoked with a prompt that
includes the missing pieces.

## Rationale

Free-form improvement items let the agent write vague targets like
"improve test coverage" or "refactor the config loader" that the
*same* agent later finds impossible to declare [x]-done
unambiguously.  US format with acceptance criteria forces the
definition-of-done *before* the work starts, which both (a)
sharpens implementation (the agent knows exactly what to build) and
(b) makes the [x] checkoff verifiable by the orchestrator's
post-round check (each criterion maps to a runnable assertion).
