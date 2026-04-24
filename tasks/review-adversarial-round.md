# Adversarial Round Review — evolve-native

Inspired by `tasks/review-adversarial-general.xml` (BMad) and
`tasks/code-review/instructions.xml` (BMad), adapted to evolve's
round-based convergence loop.

## When this fires

After every successful round's **implementation commit** (the
`feat(evolve): ✓ US-NNN: ...` or equivalent commit), before the
Phase 4 convergence check and before the orchestrator decides the
round is "done".  The review sits in **Phase 3.6** of the round
lifecycle — between Phase 3.5 structural-change self-detection
and Phase 4 convergence.

## Reviewer persona

Zara — `agents/reviewer.md`.  Explicitly NOT:

- Winston (architect) — drafted the US, conflict of interest on
  design validation.
- John (PM) — drafted the value framing, conflict of interest on
  priority / scope assessment.
- Amelia (dev) — implemented the story, conflict of interest on
  self-audit.

The review is a forced persona reset: the agent erases its
implementer mindset and steps into Zara's cynical-reviewer stance
with a fresh read of the artifacts.

## Input scope

Zara has access to exactly these artifacts (no more, no less):

1. The US item text — pulled from the current `improvements.md`
   line that round N just checked off (`[x] [type] [priority]
   US-NNN: <summary>` + the multi-line body including the AC
   block and the Definition of Done).
2. The round's git diff — `git diff HEAD^ HEAD -- .` on the
   commit just made by the dev persona.
3. The full conversation log — `conversation_loop_{N}.md`
   (includes the architect, PM, and dev persona blocks if the
   round wrote a new US; otherwise just the dev block for a
   round that implemented a pre-existing US).
4. `SPEC.md` — authoritative spec for compliance checks.
5. Prior `memory.md` — for context on what was already learned /
   what anti-patterns were flagged in previous rounds.

Zara does NOT see:

- The implementer's scratch reasoning outside the conversation
  log (no agent thought-traces beyond what's already in the log).
- Prior round reviews (each review is fresh — no chain effect).
- `state.json` or cost data (irrelevant to code quality).

## Review attack plan

Zara runs four passes, each producing findings:

### Pass 1 — Acceptance-criteria audit

For each AC in the US:

1. Read the AC text.
2. Search the diff + the broader project for evidence the AC is
   satisfied (the test that enforces it, the behaviour that
   implements it, the artifact that embodies it).
3. Classify: **IMPLEMENTED** / **PARTIAL** / **MISSING**.
4. PARTIAL or MISSING → **HIGH** finding with exact AC text and
   evidence gap.

Zara cross-checks each AC against a *runnable* assertion —
"pytest passes" is not sufficient unless there's a test that
would fail without the implementation.  If the AC wording itself
is not testable ("code is clean"), that's a **HIGH** finding
against the US drafting phase, not a free pass for the
implementation.

### Pass 2 — Claim vs. reality (git vs. story)

1. Read the dev persona's conversation block.  Extract every
   claim ("added test X", "refactored Y to Z", "kept backward
   compat via W").
2. Grep the git diff for evidence of each claim.
3. **Claim without evidence** → **HIGH** finding.
4. **Evidence without claim** (diff touches a file the dev never
   mentioned) → **MEDIUM** finding — silent changes erode
   trust.
5. Check the commit message: does it describe what was actually
   changed, or is it the fallback `chore(evolve): round N`?

### Pass 3 — Code and test quality

For every file in the diff:

- **Test quality**: placeholder asserts (`assert True`,
  `assert result is not None` without checking content), no
  negative-path coverage, mocks that mock away the thing being
  tested.
- **Error handling**: silent `except Exception: pass`, bare
  `except`, swallowed exceptions that hide real bugs.
- **Naming and structure**: magic numbers without a constant,
  functions > 50 lines doing three things, names that lie about
  behaviour.
- **Security**: shell injection in subprocess calls, path
  traversal in file operations, unvalidated input at trust
  boundaries.
- **Performance**: N+1 loops, full-file reads inside tight
  iterations, work done per-round that should be per-session.

Zara is especially cynical about:

- New tests written by the same pass that introduced the bug
  they "cover" — tests should fail first, then pass (TDD).
  Check whether the test ever failed in the conversation log.
- Docstrings that paraphrase the code but add no new information
  (a docstring that says "returns True if X" for a function
  literally named `is_x` is LOW noise, not documentation).

### Pass 4 — SPEC compliance

1. Read the paragraphs of `SPEC.md` closest to the US topic.
2. Cross-reference the implementation against each normative
   ("MUST", "MUST NOT", "SHOULD") statement.
3. **Violation** → **HIGH** finding.
4. **Ambiguous spec** → **LOW** finding against the spec, not the
   implementation (but flagged so a future round can sharpen the
   spec).

## Minimum findings rule

Zara produces **at least 3 findings** on every review.  If genuinely
clean (rare), she enumerates the three highest-risk areas she
checked and cites *why* each was sound — no "looks good" reviews.

If Zara would produce **more than 10 findings**, she caps at 10
(the highest severity + specificity wins) and notes the overflow
count — a round with 10+ issues should not block on Zara's
findings alone; the signal is "this round needs a rewrite, not
a patch list".

## Output

Written to `{run_dir}/review_round_{N}.md`:

```markdown
# Round N — Adversarial Review (Zara)

**US reviewed:** US-NNN: <summary>
**Commit:** <short sha> — <message first line>
**Verdict:** APPROVED | CHANGES REQUESTED | BLOCKED
**Findings:** X HIGH, Y MEDIUM, Z LOW (T total)

## HIGH — must fix before round is [x]'d

- [HIGH-1] <title>
  **AC or pass:** <AC-2 / Pass-2 claim-vs-reality / ...>
  **Evidence:** <file:line or commit hunk>
  **Remediation:** <what would make this pass>

- [HIGH-2] ...

## MEDIUM — should fix, not blocking the round

- [MED-1] ...

## LOW — nice-to-fix, backlog-candidate

- [LOW-1] ...

## Reviewer narrative

<2-5 sentences: what Zara thinks is really wrong — or, on a
genuinely clean review, the three highest-risk areas she
checked and found sound.>
```

## Verdict → orchestrator action

| Verdict          | Condition                                             | Action                                                                                                  |
|------------------|-------------------------------------------------------|---------------------------------------------------------------------------------------------------------|
| APPROVED         | 0 HIGH findings                                        | Round proceeds to Phase 4 convergence check.  Review file committed alongside the round's artifacts.     |
| CHANGES REQUESTED | ≥ 1 HIGH finding, < 3 HIGH findings                   | Orchestrator writes `subprocess_error_round_{N}.txt` with a `REVIEW: changes requested` prefix listing the HIGH findings; triggers a debug retry (same mechanism as NO PROGRESS / MEMORY WIPED).  The retry reads the review file and addresses each HIGH finding before re-committing. |
| BLOCKED          | ≥ 3 HIGH findings, OR any finding tagged `[regression-risk]` | Orchestrator exits with code 2 and surfaces the review summary.  Operator intervention required — the round needs a rewrite, not a retry. |

The review file is **kept on disk** regardless of verdict — part
of the evolution audit trail.  The prior-round audit path (Step
1.5 in `prompts/system.md`) scans review files of round N-1 as
another anomaly signal.

## Why this matters

Evolve's own round agent has a conflict of interest on
self-assessment — it's the same entity that drafted the US,
implemented it, and decides to check it off.  Without an
adversarial pass, "looks good" reviews slip through and the
cumulative quality of `improvements.md` drifts downward.  Zara
is the structural guarantee that every `[x]` passed through at
least one skeptical look at the actual diff.
