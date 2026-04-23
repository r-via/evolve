# Agent Memory

Cumulative log of errors encountered and lessons learned across evolution rounds.
Each agent reads this before starting and compacts it at the end of their turn.

## Decisions

### Phase 1 escape hatch plumbing — round 1 of 20260423_140637
- Context: SPEC.md § "Phase 1 escape hatch for unrelated pre-existing failures"
  required teaching the agent the bypass rules AND letting it know at runtime
  which attempt it was on. No explicit attempt counter was plumbed through the
  `evolve _round` subprocess invocation.
- Choice: parse `(attempt K)` from the existing `subprocess_error_round_N.txt`
  diagnostic file in `build_prompt`, guarded on the filename matching the
  current round_num so unrelated older diagnostics don't promote the counter.
  Injected an `{attempt_marker}` placeholder into prompts/system.md. Also
  enriched the diagnostic itself (in loop.py) with a "Phase 1 escape hatch
  notice" block written only when the next attempt will be 3.
- Rationale: keeps the orchestrator → subprocess contract unchanged (no new
  CLI flag), while giving the agent three signals for attempt-3 (banner in
  system prompt, Phase 1 escape hatch rules in system prompt, and the
  diagnostic's own notice block). Redundancy is intentional: the rules live in
  one document (system.md) but the attempt marker is computed fresh each round.

### Phase 1 escape hatch test markers — round 2 of 20260423_140637
- Context: writing tests for the attempt-marker substitution kept failing
  on assertions like `"FINAL RETRY" not in prompt` because that string
  also appears in the static system.md section header
  ("Phase 1 escape hatch — FINAL RETRY ONLY (attempt 3 of 3)") and in the
  escape hatch banner that loop._save_subprocess_diagnostic injects into
  the diagnostic file (which is then included in the prompt body).
- Choice: assert against `"CURRENT ATTEMPT: 3 of 3"` and `"NOW PERMITTED"`
  — these strings are emitted ONLY by agent.build_prompt's runtime
  attempt-3 banner substitution and appear nowhere in the static
  template or in the diagnostic banner.
- Rationale: the attempt-marker code path is the unlock signal we want
  to verify in isolation; static template text is documentation, not
  runtime state. Future tests of similar runtime substitutions should
  prefer markers unique to the substitution site (e.g. `>>> ... <<<`
  delimiters or unique capitalised phrases) over substrings shared with
  the surrounding documentation.
