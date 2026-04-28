# Multi-call Pipeline: Implementation Wiring

> Archived from SPEC.md § "Multi-call round architecture" — stable
> implementation wiring since the three-call split landed.

## What each prompt file contains

- ``prompts/draft.md`` (~100 lines) — Winston + John pipeline, US
  template, ID allocation rule, single-item constraint.  No
  implementation instructions, no review attack plan, no Phase 1
  errors-first rule (draft doesn't touch code).
- ``prompts/system.md`` (~250 lines, down from ~700) — Amelia's
  focused implementation prompt.  Phase 1 errors-first stays
  (code changes can fail tests).  Phase 3.5 structural-change
  self-detection stays (relevant to code commits).  Phase 2
  rebuild, Phase 3 drafting, Phase 3.6 review — removed,
  delegated.
- ``prompts/review.md`` (~120 lines) — Zara's adversarial review
  four-pass protocol, verdict schema, minimum-findings rule.  No
  role-play of other personas.

## Orchestrator contract (evolve/orchestrator.py)

``_run_single_round_body(project_dir, round_num, check_cmd, ...)``
follows the pipeline literally:

1. Run pre-check.
2. Inspect ``{runs_base}/improvements.md``:
   - If any ``[ ]`` item present → ``analyze_and_fix(...)``
     (implement path, Amelia).
   - Else → ``run_draft_agent(...)`` (draft path, Winston + John).
3. Stage + commit ``COMMIT_MSG`` (or confirm agent already did).
4. Run post-check.
5. ``run_review_agent(round_num, run_dir, project_dir)``.
6. ``_check_review_verdict(run_dir, round_num)`` — route the
   verdict to retry / exit / proceed as before.
7. Phase 4 convergence check (deterministic).

## Retry semantics within a round

If any of the three calls crashes, stalls, or produces a no-progress
outcome, the orchestrator retries the FAILED call (not the whole
round).  Scope-creep and backlog-violation detections still apply to
the implement call's commit.  Circuit breaker still fires on three
identical failure signatures.
