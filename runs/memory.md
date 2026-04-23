# Agent Memory

Cumulative learning log across evolution rounds. Append-only. See
SPEC.md § `memory.md` for the discipline (length cap, telegraphic
style, non-obvious gate).

## Errors

## Decisions

### Mechanism B: redundant warn suppression — round 1 of 20260423_142834
`_forever_restart` → warn on missing README_proposal.md ONLY when spec_proposal adopted. Party-mode total failure → single SPEC-level warn; test asserts `ui.warn.call_args[0][0]` = last call, extra warn would break it.

### Phase 1 escape hatch: attempt counter plumbing — round 1 of 20260423_140637
attempt K → `{attempt_marker}` placeholder in system prompt, parsed from
`subprocess_error_round_N.txt` filename. Guarded on matching round_num so
older diagnostics don't promote counter. No new CLI flag.

### Phase 1 escape hatch: test markers — round 2 of 20260423_140637
Assert against `"CURRENT ATTEMPT: 3 of 3"` / `"NOW PERMITTED"` — emitted only
by runtime banner, not by static template or diagnostic banner. Rule: prefer
substitution-site-unique markers over substrings shared with surrounding docs.

## Patterns

## Insights
