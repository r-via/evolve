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

### Retry continuity tests: section header appears twice in prompt — round 6 of 20260423_142834
`## Previous attempt log` rendered AS section + referenced in Step 0 prose. Match
`\n## Previous attempt log\n` (standalone), not bare substring. Same trap lurks for
any header name quoted in system.md instructions.

### Malware-reminder false positive → refuse-loop — round 7 of 20260423_142834
post-Read system reminder re "malware" fires on every file read. Evolve IS
target → proceed. Attempt 2 took reminder literally, refused all edits,
zero-progress. Rule: reminder = reflexive heuristic, not malware finding.

## Patterns

### Party-mode prompt capture: patch target is `agent.run_claude_agent`
mock `agent.run_claude_agent` (not `loop._run_party_agent` — doesn't exist). Also patch `agent._is_benign_runtime_error=False` + `agent._should_retry_rate_limit=None` + pre-create `agents/*.md` persona file, else `_run_party_mode` short-circuits before prompt building.

## Insights

### Startup-vs-runtime cairosvg check: patch ordering — round 8 of 20260423_142834
`RichTUI.__init__` runs `import cairosvg` once → must `patch.dict("sys.modules", {"cairosvg": None})` BEFORE construction. Existing tests patched after → only covered runtime fallback in `capture_frame()`. Same trap for any startup-time optional-dep guard.

### Memory-discipline rewrite: static vs runtime header drift — round 9 of 20260423_142834
`prompts/system.md` § Memory now says log broad (errors, decisions, surprises, patterns, insights). BUT `agent.py:223` still injects `## Memory (errors from previous rounds — do NOT repeat these)` — contradicts broadened policy. Static template ≠ runtime section header; both must move together or discipline doesn't land.

### Memory-wipe sanity gate: snapshot-timing trap — round 10 of 20260423_142834
`mem_size_before` captured BEFORE `_run_monitored_subprocess` → mock writes inside mock don't affect it. On retry-attempt 2, pre-snapshot = already-wiped state → shrink not re-detected. Test asserts only that `MEMORY WIPED:` appears in diagnostics list, not exit code — retry may recover via `unchecked != prev_unchecked`. Use `MEMORY WIPED` prefix (not `NO PROGRESS`) so `agent.py` picks correct header branch.

### memory.md scaffold lives in `_init_config`, not first-round agent — round 12 of 20260423_142834
`_init_config` in evolve.py now writes `runs/memory.md` with 4 typed sections. No code path creates memory.md otherwise — previously stayed "(none)" until agent first wrote. Scaffold hard-references `SPEC.md` in the pointer text → drifts for `--spec CLAIMS.md` projects (tracked as follow-up).

### `_run_rounds` UI stub: MagicMock > hand-rolled — round 13 of 20260423_142834
`_run_rounds` calls `round_header`, `progress_summary`, `warn`, `capture_frame`, … — surface keeps growing. Hand-rolled `_StubTUI` breaks on new method. `MagicMock()` swallows all — simpler, survives future TUIProtocol additions.

### Backlog rule 1: detect via line-set diff, NOT count diff — round 14 of 20260423_142834
`_detect_backlog_violation` compares verbatim `- [ ]` line sets pre/post — counts alone false-positive when one item is checked off and another added (legit empty-queue add: pre={A}, post={B} → no violation). New = post − pre, violation iff `new` ≠ ∅ AND `len(post) > len(new)`. Diagnostic prefix `BACKLOG VIOLATION` chosen so agent.py's `elif` chain (memory > backlog > no-progress) renders dedicated header without grep-collision with the system-prompt's own "Backlog discipline" prose.

### state.json: `backlog` added BESIDE `improvements`, not replacing — round 15 of 20260423_142834
SPEC § "Growth monitoring" documents `state.backlog.{pending,done,blocked,added_this_round,growth_rate_last_5_rounds}`. Kept legacy `state.improvements.{done,remaining,blocked}` because 8+ existing tests assert its exact shape (`test_loop.py:523` etc.). `pending` ≡ `remaining` — same value, two keys for back-compat. Don't dedup until callers are migrated.

### Rules 2-4 are prompt-only — tests assert text, not behavior — round 16 of 20260423_142834
Rule 1 = orchestrator code (`_detect_backlog_violation`). Rules 2-4 = system prompt directives only. Tests grep `prompts/system.md` AND the `build_prompt()` output for rule labels + key directives (`"extend the existing item"`, `[P1/P2/P3]` + `TOP/middle/BOTTOM`, `last 3`/`conversation_loop_`). Must collapse whitespace before substring match — rule 2 phrase wraps across a line break in source.

### Constant-drift test: `count <= 1` heuristic — round 19 of 20260423_142834
Drift-catch via `src.count("literal") <= 1` — allow 1 (the constant definition itself), fail on 2+. Simpler than AST-walk + survives future docstring quotes of same literal provided they're spelled differently. Picked distinctive substrings (`"README drift:"`, `## Previous attempt log`, `silently wiped memory.md`, `capture_frames is enabled but cairosvg`) — NOT whole-sentence match, which would false-positive on SPEC.md-style prose.

### `_init_config` spec plumbing: keep constant agnostic, substitute at render — round 20 of 20260423_142834
`_DEFAULT_MEMORY_MD` stays spec-agnostic (constant-drift test asserts "SPEC.md §" NOT in template). Added `_render_default_memory_md(spec)` seam: None / "README.md" → return verbatim; explicit spec → `.replace("your project's spec file", spec)`. Init now calls `_resolve_config` first so EVOLVE_SPEC env honored before scaffold write.

### Mechanism A blocking: helper vs loop split — round 1 of 20260423_162904
`_audit_readme_sync` stays pure item-adder (never touches CONVERGED). New `_enforce_readme_sync_gate` + `_has_unresolved_readme_sync_items` in loop.py do the unlink. Idempotency now scans for `` `<claim>` `` substring (not full item line) + skips any line with both claim marker AND `[wontfix-sync:`. Checked `[x]` without wontfix-sync does NOT bypass re-propose — test must also update README text, else next audit re-queues.

### `.lower()` case-sensitivity trap in `in` assertions — round 2 of 20260423_162904
`"not write CONVERGED again" in prompt.lower()` never matches — `.lower()` lowers BOTH sides implicitly only in the left operand, so uppercase `CONVERGED` in the literal stays uppercase and misses `converged` in lowered prompt. Rule: when mixing `.lower()` with substring search, lower the search literal too.
