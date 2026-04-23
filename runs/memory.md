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

### Mechanism C: stale-since persisted in readme_sync — round 2 of 20260423_142834
`readme_stale_since_round` stored INSIDE `state.json.readme_sync` itself (not
top-level). Read-back in `_compute_readme_sync` from prior state. Internal
field — SPEC schema shows only 4 documented keys but persistence needs 5th.

### Mechanism C: first-warn round off-by-one — round 3 of 20260423_142834
threshold `> 3` strict — drift-starts-at-R1 → first warn R5 (rss=4), not R4
(rss=3). Round N since=1 → rss = N-1. Easy test misread.

### Retry continuity: attempt-log naming driven by orchestrator diagnostic — round 5 of 20260423_142834
`analyze_and_fix` names log via `_detect_current_attempt(run_dir, round_num)` parsing
`subprocess_error_round_N.txt` header `(attempt K)` → next is K+1. SDK rate-limit
retries share one attempt log; only subprocess-level attempts bump K. Canonical
`conversation_loop_N.md` is `shutil.copyfile` (cross-fs safe), not symlink.

### Bookkeeping: commit-message + improvements.md both required for progress — round 5 of 20260423_142834
zero-progress detector trips on EITHER missing COMMIT_MSG OR byte-identical
improvements.md. Real code edits + passing tests aren't enough — must also
check off item and write COMMIT_MSG, else round is flagged no-progress and
retried.

## Patterns

### Party-mode prompt capture: patch target is `agent.run_claude_agent`
mock `agent.run_claude_agent` (not `loop._run_party_agent` — doesn't exist). Also patch `agent._is_benign_runtime_error=False` + `agent._should_retry_rate_limit=None` + pre-create `agents/*.md` persona file, else `_run_party_mode` short-circuits before prompt building.

## Insights
