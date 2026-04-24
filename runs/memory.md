# Agent Memory

Cumulative learning log across evolution rounds. Append-only. See
SPEC.md § `memory.md` for the discipline (length cap, telegraphic
style, non-obvious gate).

## Errors

### Convergence gate false positive on stale marker — round 3 of 20260423_180038
`_detect_premature_converged` did full-text `"[stale: spec changed]" in imp_text` → matched phrase inside `[x]` item descriptions. Fix: scan only `- [ ]` lines. Same trap as backlog gate — always iterate lines, never substring-match whole file.

### Zero-progress false positive on convergence — round 3 attempt 2 of 20260423_180038
`imp_unchanged` fires even when CONVERGED written + all items already `[x]`. Fix: `effective_imp_unchanged = imp_unchanged and not converged_written`. Convergence-gate backstop handles premature case independently. Note: fix on disk doesn't help running orchestrator process — must also change improvements.md in same round.

### Stale marker false positive persists across attempts — round 4 of 20260423_180038
Running orchestrator still uses old `_detect_premature_converged` (full-text match) even after fix committed. Rephrasing `[stale: spec changed]` in `[x]` item descriptions is the only workaround — code fix applies to NEW sessions only.

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

### Mechanism A revert: backlog gate tag list — round 1 of 20260423_171103
Dropping `[wontfix-sync:]` from `_detect_premature_converged` requires sync edits in the gate docstring AND two test fixtures (`test_convergence_gate.py` gate-docstring + `test_only_tagged_blockers` fixture + `test_premature_converged_renders_dedicated_header` diagnostic). Forgotten `wontfix-sync` in any of the three causes late test failure on attempt 2.

### Mechanism B revert: preserve tuple return shape — round 2 of 20260423_171103
`_forever_restart` still returns `tuple[bool, bool]` (2nd always `False`) — caller in `evolve_loop` unpacks as tuple + legacy path used `isinstance(_, tuple)` guard. Changing to bare `bool` → unpacking break. Also kept `test_legacy_commit_message_when_spec_is_readme` untouched — its `startswith("chore(evolve): forever mode")` assertion survives new "adopt proposal" tail.

### Mechanism C revert: bookkeeping-only attempt 2 — round 3 of 20260423_171103
attempt 1 landed the code/test deletions (commit 8ce882f) but ran out of 40-turn budget before ticking box + writing COMMIT_MSG → orchestrator flagged NO-PROGRESS. attempt 2 = pure bookkeeping close-out. Rule: on retry, check working tree / last commit BEFORE re-reading source — work may already be done, only the two bookkeeping signals (checkbox + COMMIT_MSG) missing.

### Stale-README advisory: integer-floor drift at day boundary — round 4 of 20260423_171103
`drift_days = int(drift_seconds // 86400)` + strict `>` → drift == threshold is silent (30 days drift + threshold 30 → int=30, 30>30 False). Matches SPEC "default threshold 30 days" wording — fires at 31+. Config resolution: env > evolve.toml > default, invalid env silently falls through to config (not default) — checked via `threshold_days is None` sentinel, not empty string.

### sync-readme: `NO_SYNC_NEEDED` sentinel for exit-1 — round 4 attempt 2 of 20260423_171103
agent has 2 outputs: write `<project>/README_proposal.md` (or `README.md` in apply) → exit 0; write `<run_dir>/NO_SYNC_NEEDED` → exit 1; neither → exit 2. Apply mode also asserts mtime advanced before `_git_commit`, else exit 2 (catches silent agent no-op). Spec=None / "README.md" → refuse with exit 1, no agent call, no run_dir creation. Backlog rule 1: `--effort` re-add legitimate only because queue was genuinely empty post-checkoff.

### --effort plumbing: 3-attempt pattern — module global + grep-count test — round 5 of 20260423_171103
`agent.EFFORT` module global overwritten by loop entry points (`run_single_round` etc.) — NOT threaded through every function signature. `ClaudeAgentOptions(...)` opened in 3 agent sites; test greps `agent.py.count("effort=EFFORT") >= 3` instead of mocking each site. Attempts 1+2 did the code but skipped checkoff+COMMIT_MSG → zero-progress twice. Rule: on retry, finish bookkeeping BEFORE any new code.

### Module extraction: patch target split — round 3 of 20260423_213701
`patch("loop.subprocess.run")` survives extraction (shared module obj). `patch("loop.datetime")` / `patch("loop.get_tui")` break — module-level name replacements don't cross module boundaries. Rule: after function extraction, fix patches that mock module-level names (datetime, get_tui), leave patches on dotted attrs (subprocess.run) alone.

### Package move shim: `from X import *` skips private names — round 1 of 20260424_120253
Moving `agent.py` → `evolve/agent.py` with `from evolve.agent import *` in shim does NOT re-export `_private` names. Tests importing `from agent import _run_validate_claude_agent` etc. fail with ImportError. Fix: add explicit `from evolve.agent import (_detect_current_attempt, _patch_sdk_parser, ...)` block in shim for every `_private` name used by tests.

### Package move: patch targets must follow the real module — round 1 of 20260424_120253
After moving `agent.py` → `evolve/agent.py`, `patch("agent.get_tui")` patches the shim's namespace but code in `evolve.agent` uses its own `get_tui`. All `patch("agent.X")` for module-level names must become `patch("evolve.agent.X")`. Dotted attrs (`agent.asyncio.run`) also updated for consistency. Same pattern as prior loop.py extraction.

### Package move: Path(__file__) resolution shift — round 1 of 20260424_120253
`Path(__file__).parent / "prompts" / "system.md"` resolves to `evolve/prompts/system.md` after move (doesn't exist). Fix: `Path(__file__).resolve().parent.parent / "prompts" / "system.md"` to go up to project root. Any module doing file-relative path resolution must be audited when moved into a subdirectory.

### Source-reading tests: update paths after package move — round 1 of 20260424_120253
Tests like `test_constant_drift.py` that `read_text()` the source file to count literal occurrences need path updates (root `agent.py` → `evolve/agent.py`). The shim is ~35 lines and won't contain the constants/patterns being tested. Same for `test_evolve.py` effort-flag grep.

### CLI move: __init__.py→cli.py Path(__file__) unchanged — round 1 of 20260424_123612
Both `evolve/__init__.py` and `evolve/cli.py` are in `evolve/` → `.parent.parent` gives same project root. No path fixup needed unlike agent.py move (which went from root to subdirectory).

### Shim removal: local `from X import` inside function bodies — round 2 of 20260424_132929
Round 1 grep `^from (loop|agent) import` caught top-level imports only. ~50 local imports inside test function bodies (`from loop import run_diff`, `from agent import _run_validate_claude_agent`) were missed. Rule: always grep WITHOUT `^` anchor when hunting root-module imports — indented local imports are invisible to line-start matches.
