# evolve — specification

This file is the **formal specification** of evolve. It is the contract that
`evolve start --spec SPEC.md` converges to. For a friendly user introduction —
install, quickstart, examples — see [README.md](README.md).

`README.md` is stable and user-facing. `SPEC.md` is dense, exhaustive, and is
what the agent verifies claim-by-claim. New features land here first as
claims; the user-facing text follows once the claim is stable.

---

## Architecture

Evolve is organized as a Python package (`evolve/`) with clear module
responsibilities:

| Module | Responsibility |
|--------|---------------|
| `evolve/cli.py` | CLI entry point, argument parsing, config resolution |
| `evolve/orchestrator.py` | Round lifecycle, subprocess monitoring, watchdog, debug retries |
| `evolve/agent.py` | Claude SDK interface — prompt building, agent execution, retry logic |
| `evolve/git.py` | Git operations — commit, push, branch management, ensure-git |
| `evolve/state.py` | State management — state.json, improvements parsing, convergence gates, backlog discipline |
| `evolve/party.py` | Party mode orchestration — multi-agent brainstorming, proposal generation |
| `evolve/tui/__init__.py` | TUI protocol definition and `get_tui` factory |
| `evolve/tui/rich.py` | Rich-based TUI implementation with frame capture |
| `evolve/tui/plain.py` | Plain-text fallback TUI |
| `evolve/tui/json.py` | Structured JSON output TUI for CI/CD |
| `evolve/hooks.py` | Event hooks — loading config, matching events, fire-and-forget execution |
| `evolve/costs.py` | Token tracking, cost estimation, budget enforcement |

### Package structure

The project uses a standard Python package layout:

```
evolve/
├── __init__.py           # package marker, re-exports for backward compat
├── cli.py                # CLI entry point (was evolve.py)
├── orchestrator.py       # round lifecycle (extracted from loop.py)
├── agent.py              # Claude SDK interface (was agent.py)
├── git.py                # git operations (extracted from loop.py)
├── state.py              # state/convergence logic (extracted from loop.py)
├── party.py              # party mode (extracted from loop.py)
├── hooks.py              # event hooks (was hooks.py)
├── costs.py              # token tracking and cost estimation (new)
└── tui/
    ├── __init__.py       # TUIProtocol + get_tui factory (was tui.py)
    ├── rich.py           # RichTUI with frame capture
    ├── plain.py          # PlainTUI fallback
    └── json.py           # JsonTUI for CI/CD
```

The `pyproject.toml` entry point is `evolve.cli:main`. The package is
installed via `pip install .` and the `evolve` command is available globally.

**Backward compatibility.** During the migration from flat modules to the
package structure, root-level shim files (`loop.py`, `agent.py`, `tui.py`,
`hooks.py`) re-export all public names from their new locations via
`from evolve.orchestrator import *` etc. These shims exist for one release
cycle and emit `DeprecationWarning` on import. They will be removed in a
future version.

**Migration strategy.** The restructuring is designed to work within evolve's
"one granular improvement per round" model:

1. Create `evolve/` package skeleton with `__init__.py`
2. Move `hooks.py` first (smallest module, fewest dependencies)
3. Extract `git.py` from `loop.py` (self-contained git operations)
4. Extract `state.py` from `loop.py` (state management, convergence gates)
5. Extract `party.py` from `loop.py` (party mode orchestration)
6. Split `tui.py` into `tui/` subpackage
7. Move `agent.py` into the package
8. Move `evolve.py` → `evolve/cli.py`, update entry point
9. Remaining `loop.py` → `evolve/orchestrator.py`
10. Remove root-level shims once all imports updated

Each step is one round. Tests and imports are updated in the same round as
each move.

### Config resolution

Settings are resolved via a data-driven loop over field definitions, with each
field checking CLI → environment variable → config file → default in order.
Resolution order (first wins):

1. CLI flags (`--check "pytest"`)
2. Environment variables (`EVOLVE_MODEL`, `EVOLVE_SPEC`, `EVOLVE_ALLOW_INSTALLS`, ...)
3. `evolve.toml` in project root
4. `pyproject.toml [tool.evolve]` section
5. Built-in defaults

### Retry and error handling

**Agent-level retries** — `analyze_and_fix` and `_run_party_mode` share retry
helpers for:
- Benign async teardown errors (cancel scope, event loop closed)
- Rate limit detection with exponential backoff
- Configurable max retries

**Orchestrator-level retries** — `_run_rounds` monitors each subprocess with a
watchdog timer and retries failed rounds:
- `_run_monitored_subprocess` uses `Popen` + reader thread for real-time output
  streaming and stall detection (120s silence threshold)
- `_save_subprocess_diagnostic` writes crash/stall context to disk
- Debug retry loop re-runs the round with the diagnostic injected into the
  agent's prompt, up to 2 retries per round

### Hook execution

The `hooks.py` module manages event hook lifecycle:
- Loads hook configuration from `evolve.toml` or `pyproject.toml`
- Matches lifecycle events to configured shell commands
- Executes hooks as fire-and-forget subprocesses with 30-second timeout
- Sets environment variables for hook context (`EVOLVE_SESSION`, `EVOLVE_ROUND`, `EVOLVE_STATUS`)
- Logs failures without blocking the evolution loop
- Fully testable in isolation from the orchestrator

---

## Session layout

Each `evolve start` creates a timestamped session. Each round runs as a
**monitored subprocess** so code changes are picked up immediately and stalled
processes are automatically detected and killed.

```
<project>/
├── README.md                          # user-facing documentation
├── SPEC.md                            # THE SPEC — evolve converges to this
├── evolve.toml                        # (optional) project-level config
├── runs/
│   ├── improvements.md                # shared — one improvement added per round
│   ├── memory.md                      # shared — cumulative learning log (append-only, compacted past ~500 lines)
│   ├── 20260324_160000/               # session 1
│   │   ├── state.json                 # real-time session state (queryable)
│   │   ├── conversation_loop_1.md     # full opus conversation log
│   │   ├── conversation_loop_1_attempt_1.md   # per-attempt log when retries occur
│   │   ├── conversation_loop_1_attempt_2.md
│   │   ├── check_round_1.txt          # post-fix check results
│   │   ├── usage_round_1.json         # per-round token usage
│   │   ├── subprocess_error_round_3.txt  # diagnostic from crashed/stalled round
│   │   ├── evolution_report.md        # post-session summary with timeline
│   │   ├── dry_run_report.md          # (dry-run only) read-only analysis
│   │   ├── validate_report.md         # (validate only) spec compliance report
│   │   ├── diff_report.md             # (diff only) spec compliance delta
│   │   ├── COMMIT_MSG                 # (transient) commit message from opus
│   │   ├── frames/                    # (optional) captured TUI frames (PNG)
│   │   │   ├── round_1_end.png
│   │   │   ├── round_2_end.png
│   │   │   └── converged.png
│   │   └── CONVERGED                  # written by opus when done
│   └── 20260324_170000/               # session 2
│       ├── ...
│       ├── party_report.md            # multi-agent discussion log
│       └── SPEC_proposal.md           # proposed next spec (name mirrors --spec)
└── prompts/
    └── evolve-system.md               # (optional) project-specific prompt override
```

---

## Round lifecycle

Each round — one improvement at a time:

```
1. Run check command (pytest, npm test, cargo test, etc.) → results
2. Opus receives: SPEC.md (or --spec target) + improvements.md + memory.md
   + check results + crash diagnostic from previous round (if any)
3. Opus reads run directory and memory.md for context
4. Phase 1 — ERRORS: fix any failures from check command (mandatory)
5. Phase 2 — SPEC FRESHNESS CHECK (gate): compare
   `mtime(SPEC.md)` vs `mtime(improvements.md)`.
     - If the spec is **newer** than `improvements.md`, the spec has
       changed since the backlog was last built and the existing items are
       considered stale. The agent sets the whole backlog aside (items are
       marked `[stale: spec changed]`) and rebuilds `improvements.md` from
       the spec: one item per claim that is not yet implemented. The
       round's target becomes the first of those rebuilt items.
     - If `improvements.md` is newer or equal, skip to Phase 3 — the backlog
       is still aligned with the spec.
   This cheap mtime check is what guarantees spec edits take priority over
   the improvement queue: touching the spec today means the next round
   first rebuilds the backlog from the updated spec, then works on the new
   gap — no full spec walk required every round.
6. Phase 3 — IMPROVEMENT: implement one item from `improvements.md`, verify,
   check it off. Then add exactly one new improvement (most impactful next
   issue).
7. Phase 4 — CONVERGENCE: only when `mtime(improvements.md) >= mtime(SPEC.md)`
   AND `improvements.md` has no unchecked non-blocked items, write `CONVERGED`
8. Opus appends errors/decisions/patterns/insights to memory.md (append-only;
   compacts only if >500 lines — see "memory.md" section)
9. Opus verifies every file it wrote by reading it back
10. Opus writes COMMIT_MSG with conventional commit message
11. Git commit + push
12. Fire event hooks (on_round_end)
13. Orchestrator re-runs check → saves check_round_N.txt
14. Orchestrator reads usage_round_N.json → updates cumulative token counts
15. Write updated state.json (including usage and cost fields)
16. Check --max-cost budget — if cumulative cost exceeds budget, pause session
17. Next round starts as fresh subprocess (reloaded code)

--- watchdog & debug retry ---

If a subprocess crashes, stalls (no output for 120s), or makes no progress,
the orchestrator:
  a. Saves a diagnostic file (subprocess_error_round_N.txt)
  b. Fires on_error hook
  c. Retries the round (up to 2 debug retries per round)
  d. The retry receives the crash diagnostic in its prompt
  e. In --forever mode, exhausted retries skip to the next round

--- after convergence ---

18. Fire on_converged hook
19. Party mode: all agents brainstorm next evolution
20. Agents produce:
    - party_report.md — full discussion log with each agent's reasoning
    - <spec>_proposal.md — proposed updated spec (filename derived from --spec)
21. Operator reviews both files
22. If approved (or in --forever mode): replace SPEC.md → new evolution loop
```

---

## Convergence

Opus decides convergence, but only after **two independent gates** both pass in
the same round:

1. **Spec freshness gate** (Phase 2) — `mtime(improvements.md) >= mtime(SPEC.md)`.
   If the spec was touched more recently than the backlog, the backlog is
   stale and must be rebuilt before anything else happens.
2. **Improvement backlog gate** — `improvements.md` has zero unchecked
   non-blocked items.

The spec gate always runs first, on every round, *before* any improvement
work. This guarantees spec edits made mid-run take priority: touching
`SPEC.md` today means the next round rebuilds `improvements.md` from the
updated spec, pushing the stale backlog aside until the new claims are
implemented. A single `stat` call is all it takes — no full spec walk on
rounds where the spec hasn't moved.

When both gates pass, Opus writes `CONVERGED` with justification.

---

## README as a user-level summary (when `--spec` is set)

When `--spec` points at a file other than `README.md`, the two documents
serve **orthogonal purposes** and evolve separately:

- **SPEC.md** — the contract evolve converges to. Exhaustive, dense,
  may include internal implementation details. Changes often as the
  system grows.
- **README.md** — a user-level **summary** that helps a reader discover
  what the software does and how to use it. Deliberately incomplete
  relative to SPEC. Changes slowly, in response to user-visible
  behavior changes, not to internal refactors.

**The evolution loop never writes to `README.md`.** Party mode only
produces `<spec>_proposal.md` (never a README proposal). README is
authored and maintained by the human operator.

When the operator wants to refresh README to reflect the current spec
(e.g. after a batch of user-visible feature adds), they invoke the
dedicated one-shot subcommand `evolve sync-readme` (see CLI flags §
"evolve sync-readme"). This is never automatic and never runs as part
of a round — it is an explicit, human-initiated action.

### Stale-README pre-flight check (lightweight observability)

At the start of every `evolve start` (before any round), the orchestrator
compares `mtime(spec_file)` and `mtime(README.md)`. If the spec is
significantly newer — default threshold **30 days** — the TUI prints a
single-line advisory:

```
ℹ️  README has not been updated in 42 days — consider `evolve sync-readme`
```

This is pure observability. It does not block anything, does not modify
any file, and does not appear during rounds (only once at startup). It
is the only automated reference the evolution loop makes to the README.
Threshold configurable via `evolve.toml`:

```toml
[tool.evolve]
readme_stale_threshold_days = 30   # or 0 to disable the advisory entirely
```

(When `--spec` is unset, README IS the spec — this section does not
apply, and no advisory is ever emitted.)

---

## CLI flags

### The --check flag

Specifies how to verify the project works. Any shell command:

```bash
--check "pytest"                    # Python
--check "npm test"                  # Node
--check "cargo test"                # Rust
--check "go test ./..."             # Go
--check "make test && make lint"    # Multiple checks
```

If omitted, evolve auto-detects the test framework by looking for common tools
(`pytest`, `npm test`, `cargo test`, `go test`, `make test`, etc.) and uses the
first one found. With an explicit `--check`, the orchestrator uses that command
instead. In both cases, the check is run automatically before and after each
round for objective verification.

### The --timeout flag

Maximum time (seconds) the check command is allowed to run before being
killed. Defaults to 300 seconds. Increase for slow test suites:

```bash
--timeout 600    # 10 minutes
```

### The --model flag

Claude model to use for evolution. Defaults to `claude-opus-4-6`. Can also be
set via the `EVOLVE_MODEL` environment variable (CLI flag takes precedence).

```bash
--model claude-opus-4-6             # Default — most capable
--model claude-sonnet-4-20250514    # Faster, lower cost
```

### The --effort flag

Reasoning effort level passed to the Claude Agent SDK. Controls how much
extended-thinking budget the agent is allowed per turn. One of `low`,
`medium`, `high`, `max`, or unset (SDK default).

```bash
--effort max      # Default — maximum reasoning, highest quality
--effort high     # deeper reasoning with a smaller budget than max
--effort medium   # balanced
--effort low      # quick iteration on simple targets
```

Also configurable via `evolve.toml`:

```toml
[tool.evolve]
effort = "max"
```

And `EVOLVE_EFFORT` environment variable. Resolution order is standard:
CLI → env → `evolve.toml` → `pyproject.toml` → default.

**Default is `"max"`.** Evolve's targets are typically non-trivial
(architectural changes, test coverage, multi-file refactors), and the
quality gain of `max` over `medium` tends to dominate the cost/latency
delta on a per-round basis. Operators who prioritize speed or cost over
quality can opt down explicitly via `--effort low` or `--effort medium`.

**Scope note.** The `--effort` flag applies to evolve's own SDK sessions
(every round's agent invocation, dry-run, validate, and party-mode agents).
It does **not** propagate from the operator's Claude Code `/effort` setting
— Claude Code and evolve are separate processes with separate SDK
contexts. If you want evolve to run at high effort, pass `--effort high`
to evolve explicitly; your Claude Code session's effort is unrelated.

### The --spec flag

By default, evolve treats `README.md` as the project specification. Use
`--spec` to point at a different file — e.g. `SPEC.md`,
`docs/specification.md`, `CLAIMS.md`.

```bash
# Use a custom spec filename at the project root
evolve start ~/projects/my-tool --check "pytest" --spec SPEC.md

# Use a spec file nested in a subdirectory
evolve start ~/projects/my-tool --check "pytest" --spec docs/specification.md
```

The path is resolved relative to the project directory. The chosen file takes
the exact role that `README.md` normally plays:

- It is the source of truth the agent converges to
- Every claim in it is verified during `--validate`
- In `--forever` mode, party mode produces a `<spec>_proposal.md` next to it
  and replaces it at the start of the next cycle

Also available via `evolve.toml` (`spec = "SPEC.md"`) and environment variable
(`EVOLVE_SPEC`). Resolution order matches every other setting.

If the specified file does not exist, evolve exits with code 2 and a clear
error message — it does not fall back to `README.md`.

### The --dry-run flag

Runs the agent in **read-only analysis mode** — examines the project and
produces a report of what it *would* change, without modifying any files.

```bash
evolve start ~/projects/my-tool --check "pytest" --dry-run
```

**How it works:**

1. Runs the check command (if provided) to see current state
2. Launches the agent with write-related tools disabled (no Edit, Write, Bash)
3. Agent analyzes the spec, code, and check results using only Read, Grep, Glob
4. Produces `runs/<session>/dry_run_report.md` with:
   - Identified gaps between spec and implementation
   - Proposed improvements (what would be added to `improvements.md`)
   - Estimated number of rounds to convergence
5. No files are modified, no git commits are created

Useful for:
- Previewing evolution scope before committing to a full run
- Auditing what the agent considers "missing" from the spec
- Estimating effort for a new project
- CI/CD gates that check spec compliance without modifying code

### The --validate flag

Runs a **spec compliance check** — verifies every claim in the spec against
the actual codebase and reports pass/fail for each one. Similar to `--dry-run`
but focused specifically on validation rather than improvement planning.

```bash
evolve start ~/projects/my-tool --check "pytest" --validate
```

**How it works:**

1. Runs the check command (if provided) to verify current test state
2. Launches the agent in read-only mode with a validation-focused prompt
3. Agent systematically checks every spec claim against the code
4. Produces `runs/<session>/validate_report.md` with:
   - Each spec claim listed with ✅ (implemented) or ❌ (missing/broken)
   - Overall compliance percentage
   - Specific gaps identified with file references
5. No files are modified, no git commits are created

**Exit codes for --validate:**

| Exit Code | Meaning |
|-----------|---------|
| 0 | All spec claims validated — spec compliant |
| 1 | One or more claims failed validation |
| 2 | Error during validation |

### The --resume flag

Resumes the most recent interrupted session instead of creating a new one.
Detects the last completed round from existing conversation logs and continues
from the next round.

```bash
evolve start ~/projects/my-tool --check "pytest" --resume
```

If no previous session exists, `--resume` starts a fresh session (same as
without the flag).

### The --forever flag

Autonomous evolution mode. Runs indefinitely on a **separate git branch**
until the operator stops it (Ctrl+C or kill).

```bash
evolve start ~/projects/my-tool --check "pytest" --forever
```

**How it works:**

1. Creates a new branch `evolve/<timestamp>` from the current branch
2. Runs the normal evolution loop (Phase 1-4) until convergence
3. After convergence, launches party mode — agents brainstorm the next cycle
4. **Instead of waiting for operator approval**, automatically merges the
   `<spec>_proposal.md` into the spec file
5. Resets `improvements.md` and starts a new evolution loop against the
   updated spec
6. Repeats until stopped by the operator

```
main ──────────────────────────────────────────────────
       \
        evolve/20260324_220000 ─── round 1 ─── round 2 ─── CONVERGED
                                                                │
                                                          party mode
                                                                │
                                                     SPEC_proposal → SPEC.md
                                                                │
                                                          round 1 ─── round 2 ─── CONVERGED
                                                                                       │
                                                                                 party mode
                                                                                       │
                                                                                     ...
```

All work happens on the `evolve/*` branch — `main` is never touched. The
operator can:
- Watch progress in real-time via the TUI
- Review the branch at any time (`git log evolve/<timestamp>`)
- Merge when satisfied (`git merge evolve/<timestamp>`)
- Or discard the branch entirely (`git branch -D evolve/<timestamp>`)

Combines well with `--allow-installs` for fully autonomous evolution:

```bash
evolve start ~/projects/my-tool --check "pytest" --forever --allow-installs
```

### The --json flag

Switches output from the interactive TUI to structured JSON events on stdout.
Each line is a valid JSON object. Designed for CI/CD pipelines, monitoring
dashboards, and programmatic consumption.

```bash
evolve start ~/projects/my-tool --check "pytest" --json
```

Each line is a JSON object with a `type`, `timestamp`, and event-specific fields:

```json
{"type": "round_start", "timestamp": "2026-03-24T16:00:00Z", "round": 1, "max_rounds": 10}
{"type": "check_result", "timestamp": "2026-03-24T16:00:05Z", "label": "check", "cmd": "pytest", "passed": true}
{"type": "agent_tool", "timestamp": "2026-03-24T16:01:00Z", "tool": "Edit", "input": "src/parser.py"}
{"type": "improvement_completed", "timestamp": "2026-03-24T16:02:00Z", "description": "Add input validation"}
{"type": "converged", "timestamp": "2026-03-24T16:05:00Z", "round": 3, "reason": "All spec claims verified"}
{"type": "hook_fired", "timestamp": "2026-03-24T16:05:01Z", "event": "on_converged", "success": true}
{"type": "usage", "timestamp": "2026-03-24T16:02:01Z", "round": 1, "input_tokens": 45230, "output_tokens": 12400, "estimated_cost_usd": 1.24}
{"type": "budget_reached", "timestamp": "2026-03-24T16:10:00Z", "budget_usd": 10.0, "spent_usd": 10.24}
```

The `JsonTUI` class implements the same `TUIProtocol` as `RichTUI` and
`PlainTUI`, ensuring all output methods are available in JSON mode with zero
changes to business logic.

### --allow-installs mode

By default, improvements tagged `[needs-package]` — those that would require
installing a new dependency — are skipped. Pass `--allow-installs` (or set
`allow_installs = true` in `evolve.toml`, or export `EVOLVE_ALLOW_INSTALLS=1`)
to let the agent install packages and work on those items.

The scope of the flag is deliberately narrow: it only unlocks
`[needs-package]` items. It does **not** disable any other safeguard
(watchdog, check command, debug retries, per-turn cap, zero-progress
detection). The name was chosen over the older `--yolo` precisely because
`--yolo` oversold the risk and undersold what the flag actually does.

**Deprecated alias.** `--yolo` (CLI) and `yolo = true` (config) are kept as
deprecated aliases for one release cycle. They behave identically to
`--allow-installs` but emit a `DeprecationWarning` to stderr pointing at the
new name. They will be removed in a future version.

### The --max-cost flag

Budget cap for a session's estimated API cost. When the cumulative estimated
cost exceeds the budget, the session pauses gracefully after the current
round completes (the in-progress round is never interrupted mid-work).

```bash
--max-cost 10.00    # Pause after ~$10.00 estimated spend
--max-cost 50       # Pause after ~$50.00
```

Also configurable via `evolve.toml`:

```toml
[tool.evolve]
max_cost_usd = 10.0
```

And `EVOLVE_MAX_COST` environment variable. Resolution order is standard:
CLI → env → `evolve.toml` → `pyproject.toml` → default.

**Default: no budget cap** (unset). When unset, the session runs until
convergence or `max_rounds`, whichever comes first. Setting a budget does
not change any other behavior — rounds, convergence gates, and retries all
work identically.

**How it works:**

1. After each round, the orchestrator reads `usage_round_N.json` and
   accumulates the session's token counts
2. The cost estimation function converts tokens to estimated USD using the
   model's rate (see § "Cost estimation")
3. If cumulative estimated cost exceeds `--max-cost`, the session pauses:
   - Writes `state.json` with `status: "budget_reached"`
   - Fires `on_error` hook with `EVOLVE_STATUS=budget_reached`
   - Prints a clear TUI panel explaining the budget was reached
   - Exits with code 1 (same as max rounds — work remains)
4. The operator can resume with `--resume` and a higher `--max-cost`

**Budget-reached TUI message:**

```
╭──────────── Budget Reached ─────────────╮
│ ⚠️  Session paused at round 5           │
│ Budget: $10.00 / Used: $10.24            │
│ Use --resume with a higher --max-cost    │
│ to continue                              │
╰──────────────────────────────────────────╯
```

### `evolve sync-readme`

One-shot subcommand that refreshes `README.md` to reflect the current spec.
Never runs as part of the evolution loop — always invoked explicitly by the
operator:

```bash
# Produce a proposal for review (default; does not modify README.md)
evolve sync-readme [<project-dir>] [--spec SPEC.md]

# Apply the refresh directly, committing the updated README
evolve sync-readme [<project-dir>] --apply [--spec SPEC.md]
```

**How it works:**

1. Loads the spec file (resolved from `--spec`, `evolve.toml`, `EVOLVE_SPEC`,
   or default `README.md` — same resolution order as every other flag).
2. Loads the current `README.md`.
3. Launches the agent in a dedicated one-shot session with a sync-focused
   prompt: *"Update the README to reflect the current spec. Preserve the
   README's tutorial voice — brevity, examples, links to the spec for
   internals. Do not copy the spec verbatim. Do not invent features that
   aren't in the spec."*
4. Writes the output to `README_proposal.md` at the project root (default
   mode) or directly to `README.md` with a git commit (`--apply` mode).
5. Exits.

**Exit codes:**

| Exit Code | Meaning |
|-----------|---------|
| 0 | Proposal written (or applied) successfully |
| 1 | README already in sync — no changes proposed |
| 2 | Error — spec file missing, agent failure, etc. |

**When to use it:**

- After adopting a batch of SPEC changes that introduced user-visible
  features and the README is now misleading
- After a `--forever` run accumulated many cycles and the README has
  drifted from the current behavior
- When the startup advisory (`ℹ️  README has not been updated in N days`)
  prompts you

**What it does NOT do:**

- Run during rounds
- Block convergence
- Add items to `improvements.md`
- Touch any file other than `README.md` (and `README_proposal.md` in
  default mode)

The subcommand is the **only** sanctioned way evolve ever writes to
`README.md` when `--spec` points at a separate file. This separation —
evolution loop touches spec + code, `sync-readme` touches README — is
intentional: it keeps the two concerns orthogonal and avoids the
failure mode where automated sync creates silent drift between user
docs and actual behavior.

### `evolve diff`

One-shot subcommand that shows the delta between the current spec and the
implementation. Lighter-weight than `--validate` — focused on quickly
identifying gaps rather than exhaustive claim-by-claim verification.

```bash
evolve diff [<project-dir>] [--spec SPEC.md]
```

**How it works:**

1. Loads the spec file (same resolution as every other flag)
2. Launches the agent in read-only mode with `--effort low` and a
   gap-detection prompt: *"Scan the spec for major features and
   architectural claims. For each one, check whether it is present in
   the codebase. Report gaps — do not verify exhaustively."*
3. Produces `runs/<session>/diff_report.md` with:
   - Each major spec section with ✅ (present) or ❌ (missing)
   - Overall compliance percentage
   - Specific gaps identified with brief descriptions
4. No files are modified, no git commits are created

**Exit codes:**

| Exit Code | Meaning |
|-----------|---------|
| 0 | All major spec sections present — compliant |
| 1 | One or more gaps found |
| 2 | Error — spec file missing, agent failure, etc. |

**Differences from `--validate`:**
- Uses `--effort low` by default (cheaper, faster)
- Checks for presence/absence of major features, not line-by-line verification
- Does not run the check command
- Produces a shorter, more actionable report
- Designed for quick "how far are we?" checks, not formal validation

### Project-specific prompts

Projects can override the default system prompt by creating
`prompts/evolve-system.md` in their project directory. Evolve will use it
instead of the default.

---

## Cost and token tracking

Every round produces a `usage_round_N.json` file in the session directory
containing raw token counts from the Claude Agent SDK response. The
orchestrator aggregates these into per-session totals in `state.json` and
`evolution_report.md`.

### Token usage capture

The agent writes `usage_round_N.json` at the end of each round:

```json
{
  "round": 3,
  "model": "claude-opus-4-6",
  "input_tokens": 45230,
  "output_tokens": 12400,
  "cache_creation_tokens": 8200,
  "cache_read_tokens": 38100,
  "timestamp": "2026-04-24T16:02:01Z"
}
```

The `TokenUsage` dataclass in `costs.py` encapsulates these fields and
supports addition (accumulating per-round usage into session totals).

### Cost estimation

The `estimate_cost` function in `costs.py` converts token counts to estimated
USD using a built-in rate table for known Claude models:

```python
# Built-in rates (updated periodically)
RATES = {
    "claude-opus-4-6":          {"input": 15.0, "output": 75.0, "cache_read": 1.5},
    "claude-sonnet-4-20250514": {"input": 3.0,  "output": 15.0, "cache_read": 0.3},
}
# Rates are per 1M tokens
```

When the model is not in the rate table, token counts are still tracked
and displayed, but cost estimation shows `"unknown"` instead of a dollar
amount. This is a presentation concern, not a data loss — the raw token
counts are always available in `usage_round_N.json` and `state.json`.

**Custom rates.** Projects can override rates in `evolve.toml`:

```toml
[tool.evolve.rates]
input_per_1m = 15.0
output_per_1m = 75.0
cache_read_per_1m = 1.5
```

These override the built-in rates for the configured model, allowing
evolve to estimate costs for new models or custom pricing tiers before
the built-in table is updated.

### Aggregation in state.json

`state.json` includes a `usage` object updated after every round:

```json
{
  "version": 2,
  "usage": {
    "total_input_tokens": 234500,
    "total_output_tokens": 87200,
    "total_cache_creation_tokens": 42000,
    "total_cache_read_tokens": 189000,
    "estimated_cost_usd": 12.40,
    "rounds_tracked": 8
  }
}
```

### Cost in evolution report

`evolution_report.md` includes a "Cost Summary" section:

```markdown
## Cost Summary
| Round | Input Tokens | Output Tokens | Cache Hits | Est. Cost |
|-------|-------------|---------------|------------|-----------|
| 1     | 45,230      | 12,400        | 38,100     | $1.24     |
| 2     | 52,100      | 15,800        | 41,200     | $1.56     |
...
**Total: ~$12.40** (claude-opus-4-6)
```

### TUI cost display

Cost information appears in two places in the TUI:

1. **Per-round header** — estimated cost for the current session so far:
   ```
   ╭──────────────────── evolve ─────────────────────╮
   │ EVOLUTION ROUND 3/10                     ~$3.80 │
   ```

2. **Completion summary** — total session cost:
   ```
   ╭──────────── Evolution Complete ─────────────╮
   │ ✅ CONVERGED in 8 rounds (12m 34s)          │
   │                                              │
   │ 6 improvements completed                    │
   │ 47 tests passing                            │
   │ ~$12.40 estimated cost                       │
   ╰──────────────────────────────────────────────╯
   ```

`PlainTUI` shows cost as a simple text line. `JsonTUI` emits
`{"type": "usage", ...}` events (see § "The --json flag").

---

## improvements.md — the convergence tracker

Format:

- A checkbox (`[ ]` pending, `[x]` done)
- A type tag: `[functional]` or `[performance]`
- Optional `[needs-package]` flag — skipped unless `--allow-installs`
- Optional priority tag `[P1]` / `[P2]` / `[P3]` (see "Backlog discipline"
  below); untagged items default to `[P2]`

### Backlog discipline

The "add exactly one new improvement per round" rule (Phase 3 step 6) is a
default that only makes sense when the backlog is healthy. In practice it
tends to grow the queue monotonically — each round completes one item and
appends one, and the agent easily pattern-matches on its own recent work,
adding three, four, five variants of the same refactoring before the
original priorities get touched. The session of 20260423_140637 produced
5 consecutive "extract string X to module-level constant" items before
the queued memory-template and coverage items were addressed.

Four guardrails govern new items:

1. **Empty-queue rule (hard gate).** A new item is added **only** when all
   pending items in `improvements.md` are checked off. If any `[ ]` item
   remains after the current target is closed, the agent skips the "add
   one" step and lets the queue drain. This is the strictest of the four
   rules and subsumes most of the others — when it's obeyed, the queue
   monotonically shrinks toward convergence instead of oscillating.
2. **Anti-variante rule.** Before considering any new item, scan pending
   items for a shared template/verb (e.g. "Extract X to a constant",
   "Add tests for Y", "Harden Z against regression"). If the proposed
   item shares the template → **merge into the existing item** (extend
   its description to cover the new case), do not create a duplicate.
   This prevents the 5-variant refactoring pile-up.
3. **Priority-aware insertion.** When a new item is legitimately added
   (the queue was empty per rule 1, and the item doesn't variant-match
   per rule 2), insert it at the position matching its priority tag, not
   blindly at the end:
   - `[P1]` — bugs, missing spec claims, blocked retries: inserted at
     the TOP (next-up)
   - `[P2]` — improvements, enhancements, non-blocking features
     (default): inserted in the middle by insertion order among `[P2]`
   - `[P3]` — refactorings, polish, cosmetic: inserted at the BOTTOM
4. **Anti-stutter rule.** If the last 3 rounds have each added a `[P3]`
   refactoring item, the next round MAY NOT add another `[P3]` item
   even if rules 1-3 would permit it. This short-circuits the "pattern
   match on own work" failure mode when the empty-queue rule somehow
   isn't holding.

The orchestrator enforces rule 1 via a simple pre-commit check: if the
agent's commit modifies `improvements.md` to add a new unchecked item
while at least one other unchecked item exists, the commit is rejected
with a debug-retry diagnostic header `"CRITICAL — Backlog discipline
violation: new item added while queue non-empty"`. Rules 2-4 are
agent-enforced via system prompt.

### Growth monitoring

`state.json` exposes a `backlog` object updated every round:

```json
{
  "backlog": {
    "pending": 3,
    "done": 60,
    "blocked": 0,
    "added_this_round": 0,
    "growth_rate_last_5_rounds": -0.6
  }
}
```

A sustained positive `growth_rate` (backlog grows faster than it drains)
is itself a signal worth logging — in practice, rule 1 should drive this
to ≤ 0 on every run that's actually making progress.

---

## memory.md — cumulative learning log

Each agent reads `runs/memory.md` at the start of its turn and appends to it
during work so future rounds can benefit from what was learned. The file is
shared across rounds and across sessions of the same project — it is the one
durable place where cross-round context accumulates.

**What to log (broad, not just crashes).** Early versions of evolve only
triggered writes on hard errors, which left `memory.md` empty for most runs
because successful rounds had "nothing to log". The current contract is
broader — the agent appends entries for **any** of:

- **Errors** — exceptions, test failures, crashes, stalls
- **Decisions** — non-obvious choices ("tried X, failed, switched to Y and
  why")
- **Surprises** — behaviors that contradicted an initial assumption
- **Patterns** — recurring issues across rounds (e.g. "mocking the SDK
  consistently breaks after upgrades")
- **Insights** — architectural observations that would be useful to a future
  round even without an error trigger

**Structured sections.** `memory.md` uses typed headers so the agent (and
humans) can scan it quickly and the compaction pass doesn't accidentally
merge unrelated entries. **Entries are telegraphic, not narrative** —
sentence fragments, no articles, no ceremonial prose. The section shape
is a scaffold, not a form to fill in:

```markdown
# Agent Memory

## Errors
### <title> — <round ref>
<what happened + root cause + fix, telegraphic, 1-3 lines>

## Decisions
### <title> — <round ref>
<choice + why (non-obvious part only), 1-3 lines>

## Patterns
### <title>
<signature + rounds observed, 1-2 lines>

## Insights
### <title>
<observation + implication, 1-2 lines>
```

**Length discipline.** Entries MUST satisfy **all three**:

1. **Hard cap.** ≤ 5 lines *or* ≤ 400 characters, whichever is stricter.
   If it doesn't fit, it's not a memory entry — resynthesize, or don't log.
2. **Telegraphic style.** Drop articles ("a", "the"), drop ceremonial
   verbs ("implemented", "decided to", "chose to"), use `→` / `:` / `—`
   as connectors, prefer fragments over full sentences. Code identifiers
   stay verbatim. Example:
   - ❌ verbose: *"Context: SPEC.md § 'Phase 1 escape hatch' required
     teaching the agent the bypass rules AND letting it know at runtime
     which attempt it was on. Choice: parse `(attempt K)` from the existing
     `subprocess_error_round_N.txt` diagnostic file in `build_prompt`,
     guarded on the filename matching the current round_num. Rationale:
     keeps the orchestrator → subprocess contract unchanged..."* (19 lines)
   - ✅ telegraphic: *"attempt counter → `{attempt_marker}` placeholder,
     parsed from `subprocess_error_round_N.txt`. No new CLI flag. Three
     redundant attempt-3 signals on purpose."* (2 lines)
3. **Non-obvious gate.** Before logging, ask: *"Would a future agent
   reading this in 10 rounds care, or could they rediscover the info
   by re-reading SPEC.md / the code / the commit?"* If rediscoverable,
   **do not log**. Memory is not a code diary.

No entry restating what SPEC.md or code already documents. No entry
describing a straightforward implementation that a reader of the
resulting commit could infer. Memory is for the non-obvious: surprising
interactions, explicit trade-offs rejected, lessons that survive the
file-level context.

**Compaction discipline.** Aggressive per-turn compaction is what used to
produce the "always empty" fixed point. The current contract:

- **Append-only by default.** A turn adds entries but does not delete
  existing ones.
- **Compact only when `memory.md` exceeds ~500 lines.** When the threshold
  is crossed, the agent merges duplicates within the same section and
  archives entries older than 20 rounds into a collapsed `## Archive`
  section (still on disk, still searchable, just out of the primary read
  path).
- **Never empty a section it couldn't read.** If the agent can't tell
  whether an entry is still relevant, it keeps it.

**Byte-size sanity gate (orchestrator-side).** After every round, the
orchestrator refuses commits where `memory.md` shrunk by more than 50%
compared to its pre-round state unless the commit message explicitly
mentions `memory: compaction`. This is the same family of safeguard as
zero-progress detection — it catches the failure mode where an agent
"compacts" by silently wiping the file.

---

## Subprocess monitoring & debug retries

Every round runs as a monitored subprocess. The orchestrator streams stdout in
real-time via a reader thread and enforces a **watchdog timer** — if the
subprocess produces no output for 120 seconds, it is considered stalled and
killed.

When a round fails (crash, stall, or zero progress), the orchestrator enters a
**debug retry loop**:

1. Writes `subprocess_error_round_N.txt` with full diagnostic (exit code,
   last 3000 chars of output, reason for failure)
2. Fires `on_error` hook
3. Retries the round — the agent receives the diagnostic in its prompt under a
   "CRITICAL — Previous round CRASHED" header and fixes the root cause
4. Up to 2 debug retries per round (3 total attempts)
5. In `--forever` mode, exhausted retries skip to the next round instead of
   exiting

The agent is aware of the watchdog via the system prompt and is instructed to:
- Print progress lines as it works (silence = kill)
- Add logging/probes in delivered code for runtime observability
- Print a status line before long-running commands

### "Zero progress" detection

A round is counted as no-progress (and therefore triggers the debug retry
loop) when **any** of the following holds:

- The subprocess exits non-zero (crash)
- The watchdog fires (120s silence)
- The check command regressed (was passing, now failing)
- The agent hit the Claude Agent SDK `max_turns` cap without finishing the
  improvement — the cap is an intentional granularity forcing function
  (see below), so hitting it is a signal the target was too large, not that
  the cap needs raising
- The agent committed **without** writing a `COMMIT_MSG` file — the orchestrator
  falls back to `chore(evolve): round N`, which is the tell-tale sign the agent
  ran out of turn budget before finishing its work
- **No improvement was checked off and no new improvement was added** to
  `improvements.md` — the round ended with `improvements.md` byte-identical to
  its pre-round state

The last three conditions matter because they catch the failure mode where the
agent spends its entire turn budget on reconnaissance (Reads, Greps) and is
killed before writing any Edit/Write. The subprocess exits 0, the check still
passes (nothing changed), but no real work happened — previously this would
silently burn rounds until `max_rounds`. The debug retry now kicks in, and the
agent receives a "CRITICAL — Previous round made NO PROGRESS" header
instructing it to start with Edit/Write immediately and defer exploration.

### Per-turn cap as a granularity forcing function

The Claude Agent SDK's `max_turns` parameter is set to a deliberately modest
value (currently `40`). This is not primarily a safety bound — the
120s watchdog plays that role — but a forcing function that makes evolve's
core concept ("one granular improvement per round") observable. An item that
does not fit in 40 turns is a signal the item is too large and should be
split; hitting the cap is therefore *expected* behavior for oversized
targets, and it feeds directly into the zero-progress detection above, which
triggers the debug retry with an instruction to split before retrying.
Raising the cap to mask the signal would mask the granularity violation
instead of fixing it.

### Agent-side self-monitoring

On top of the orchestrator's zero-progress detection, the agent itself
inspects the last two rounds' conversation logs (`conversation_loop_{N-1}.md`
and `conversation_loop_{N-2}.md`) at the start of every round and refuses to
repeat a stuck pattern. Specifically, before doing any work the agent:

1. Reads the previous two conversation logs from the current run directory
2. Extracts the improvement target each round was attempting
3. Flags a **stuck loop** if the current target matches either of them and the
   prior round(s) contain no `Edit`/`Write` tool calls — i.e. pure
   reconnaissance followed by a placeholder commit
4. When stuck is detected, the agent does **not** resume the original target.
   Instead, it:
   - Splits the target in `improvements.md` into smaller independent items
     (one per file, per uncovered line range, per behavior), or
   - Marks the target as blocked with `[blocked: target too broad — split required]`
     and picks a different unchecked item
5. Logs the decision to `memory.md` so future rounds don't re-attempt the same
   broken split

This makes the agent self-healing for the most common failure mode — getting
lost in a target that's too large — without operator intervention. The
orchestrator's zero-progress retry remains the safety net; agent-side detection
is the first line of defense and catches the loop one round earlier.

### Retry continuity

Debug retries of the **same round** must reuse the previous attempt's work;
otherwise the retry wastes 40 turns re-discovering the same facts. Three rules
implement this:

1. **Per-attempt log files.** Each attempt of round N writes to its own file:
   `conversation_loop_N_attempt_1.md`, `conversation_loop_N_attempt_2.md`, etc.
   Nothing is overwritten. The final successful attempt is also symlinked (or
   copied) to `conversation_loop_N.md` for backward compatibility with
   downstream consumers (report generation, party mode, self-monitoring).
2. **Enriched diagnostic in the retry prompt.** Instead of just the last 3000
   chars of output, the debug retry prompt includes a dedicated
   `## Previous attempt log` section with the **full path** to
   `conversation_loop_N_attempt_{K-1}.md` and an explicit instruction:
   *"Read this file first. It contains everything the previous attempt
   already discovered — the tool calls, the dead ends, the working
   hypotheses. Do not redo that investigation. Continue from where it
   stopped."*
3. **Retry-aware self-monitoring.** The agent's first action in any round is
   to check for prior attempts of the **current** round number on disk (glob
   `conversation_loop_{current_round}_attempt_*.md`). If any exist, read them
   **before** looking at rounds N-1 / N-2 — they carry the most relevant
   context by far. The N-1 / N-2 check remains, but runs after.

This closes the gap where retries are currently blind to their own prior
attempt: the first attempt crashes after 40 turns of investigation, the
diagnostic gives the retry a 3000-char snippet (usually just the error
traceback), and the retry restarts the same investigation from scratch.
Treating each round as a continuum of attempts — not a fresh start each
time — is the difference between 3 wasted retries and 3 retries that each
progress further.

### Phase 1 escape hatch for unrelated pre-existing failures

Phase 1 is mandatory by contract: *fix any failure from the check command
before touching the current improvement target*. In practice this is the
right default — broken tests or lint errors usually point at regressions
introduced by the previous round and must be cleared first. But it produces
a specific lockup when:

- The failures are **pre-existing and unrelated** to the current target
  (e.g. flaky environment-specific issues, test-isolation bugs, third-party
  regressions that are genuinely someone else's problem)
- They resist two debug retries
- The agent has spent its turn budget on diagnosis without producing any
  fix

In that situation the round cannot make *any* progress — not on Phase 1,
not on the target — and keeps consuming retries and rounds until
`max_rounds`. Rounds 1-5 of session `20260423_134609` are the canonical
example: 50+ cumulative tool calls across attempts investigating a Rich
`Style.parse` LRU cache issue, zero code edits, same target re-picked
every round.

**The escape hatch.** When **all** of the following hold:

- The round is on its **second debug retry** (attempt 3, the final one
  before exhaustion)
- Phase 1 errors are still present
- The failing tests / check output touch **none** of the files the current
  improvement target names (verified by scanning the target text for file
  references and cross-checking against the failing output)

…the agent is permitted to:

1. Log the failing tests to `memory.md` under a new `## Blocked Errors`
   section with the full check output excerpt, timestamps, and a note
   explaining the Phase 1 bypass
2. Append a dedicated high-priority item to `improvements.md`:
   `[ ] [functional] Phase 1 bypass: fix pre-existing failures (<short
   summary>) that blocked round N — see memory.md § Blocked Errors`
3. **Proceed with the original Phase 3 target** for this attempt, treating
   the pre-existing failures as known-broken state to work around (e.g.
   run tests with `-k 'not broken_test'` or equivalent while building and
   verifying the target's changes)
4. At commit time, include in `COMMIT_MSG` a top-level line
   `Phase 1 bypass: <short summary>` so the escape hatch is visible in
   git history and the report

The bypass does **not** apply when:

- The failures reference files in the target's scope (those MUST be fixed
  first — they're the target's responsibility)
- The round has retries remaining (the retry might yet resolve the issue)
- The failures are the regression introduced by the current target (those
  mean the target was implemented incorrectly, not that Phase 1 is
  unrelated)

This is a **deliberate hole in the "fix errors first" rule**, narrow enough
that it only triggers on genuinely unresolvable pre-existing state, and
loud enough (via memory.md + improvements.md + commit message) that the
bypass never goes unnoticed. The orphaned errors become the top-priority
item for the next cycle and get dedicated attention instead of starving
every round.

---

## Event hooks

Evolve fires lifecycle events that can trigger external commands. Configure
hooks in `evolve.toml`:

```toml
[hooks]
on_round_start = "echo 'Starting round'"
on_round_end = "echo 'Round complete'"
on_converged = "curl -s -X POST https://hooks.slack.com/services/T00/B00/xxx -d '{\"text\": \"Project converged!\"}'"
on_error = "notify-send 'evolve error'"
```

**Supported events:**

| Event | Fires when |
|-------|-----------|
| `on_round_start` | A new round begins |
| `on_round_end` | A round completes successfully |
| `on_converged` | The project reaches convergence |
| `on_error` | A round fails (crash, stall, check failure, or budget reached) |

**Hook execution model:**
- Hooks run as fire-and-forget subprocesses with a 30-second timeout
- A failing hook never blocks the evolution loop — failures are logged and skipped
- Hook commands receive event context via environment variables (`EVOLVE_SESSION`,
  `EVOLVE_ROUND`, `EVOLVE_STATUS`)
- Hooks are managed by the `hooks.py` module, keeping orchestration logic clean

---

## Real-time state file

Each session maintains a `state.json` file updated after every round,
providing structured status queryable by external tools (CI systems,
dashboards, monitoring):

```json
{
  "version": 2,
  "session": "20260325_153156",
  "project": "my-tool",
  "round": 5,
  "max_rounds": 20,
  "phase": "improvement",
  "status": "running",
  "improvements": {"done": 12, "remaining": 3, "blocked": 1},
  "backlog": {
    "pending": 3,
    "done": 12,
    "blocked": 1,
    "added_this_round": 0,
    "growth_rate_last_5_rounds": -0.6
  },
  "usage": {
    "total_input_tokens": 234500,
    "total_output_tokens": 87200,
    "total_cache_creation_tokens": 42000,
    "total_cache_read_tokens": 189000,
    "estimated_cost_usd": 12.40,
    "rounds_tracked": 5
  },
  "last_check": {"passed": true, "tests": 143, "duration_s": 1.3},
  "started_at": "2026-03-25T15:31:56Z",
  "updated_at": "2026-03-25T16:05:00Z"
}
```

The `status` field can be: `running`, `converged`, `max_rounds`, `error`,
`party_mode`, or `budget_reached`. The schema is versioned for forward
compatibility.

**Schema versioning.** `state.json` uses a `version` field to signal
breaking schema changes. Version 1 is the original schema (no `usage` or
`backlog` fields). Version 2 adds `usage` and `backlog`. External consumers
should ignore unknown keys for forward compatibility — the version bump is
for consumers that need to know which fields are guaranteed present.

---

## Evolution report

After each session completes (converged or max rounds reached), evolve writes
`runs/<session>/evolution_report.md` — a summary of what happened:

```markdown
# Evolution Report
**Project:** my-tool
**Session:** 20260324_160000
**Rounds:** 8/20
**Status:** CONVERGED

## Timeline
| Round | Action | Files Changed | Tests |
|-------|--------|---------------|-------|
| 1 | fix: parser crash on empty input | parser.py | 42→43 |
| 2 | feat: add input validation | validator.py, parser.py | 43→47 |
...

## Cost Summary
| Round | Input Tokens | Output Tokens | Cache Hits | Est. Cost |
|-------|-------------|---------------|------------|-----------|
| 1     | 45,230      | 12,400        | 38,100     | $1.24     |
| 2     | 52,100      | 15,800        | 41,200     | $1.56     |
...
**Total: ~$12.40** (claude-opus-4-6)

## Summary
- 6 improvements completed
- 2 bugs fixed
- 12 files modified
- ~$12.40 estimated API cost
```

The report is generated by parsing conversation logs, commit messages,
check results, and usage files from the session directory. It serves both
human review (post-session summary) and CI/CD integration (PR description
content).

---

## TUI

Evolve features a modern terminal UI powered by `rich` (optional — falls back
to plain text when `rich` is not installed).

### TUI features

- Colored panels for round headers with progress bars
- Real-time agent activity feed (tools used, files edited)
- Check command results with pass/fail indicators
- Git commit + push status
- Per-round estimated cost display in round headers
- Completion summary panel on exit (including total cost)
- Budget-reached panel when `--max-cost` is exceeded
- Graceful fallback to plain text when `rich` is not installed
- TUI interface enforced via Protocol — `RichTUI`, `PlainTUI`, and `JsonTUI`
  all implement the same `TUIProtocol`, guaranteeing method parity at
  type-check time
- Optional frame capture: snapshot the rendered TUI as PNG at round end /
  convergence / errors, so party-mode agents can reason visually — see
  "Frame capture" below

### Completion summary

When evolution finishes (converged or max rounds), evolve prints a summary
panel to the terminal. The summary is generated from the session's
`evolution_report.md` and displayed through the TUI (Rich panel, plain text,
or JSON event depending on output mode).

### Frame capture (visual context for party mode)

Party mode is blind to the TUI by default — its agents only see the spec,
`improvements.md`, and `memory.md`. With frame capture enabled, evolve
snapshots the rendered TUI at key moments and hands those images to the
party-mode agents, so they can reason about layout, density, progress
visualization, and visual design drift the same way a human operator would.

**Opt-in via config.** Frame capture is off by default (adds disk I/O per
round and requires an optional dependency). Enable it in `evolve.toml`:

```toml
[tool.evolve]
capture_frames = true
```

Or via CLI: `--capture-frames` / env var `EVOLVE_CAPTURE_FRAMES=1`.

**How it works.**

1. `RichTUI` is instantiated with `Console(record=True)`, which accumulates the
   rendered output in an internal buffer without extra overhead
2. The `TUIProtocol` exposes a `capture_frame(label: str) -> Path | None` method:
   - `RichTUI` exports the buffer to SVG via `console.save_svg()` (built-in,
     no new dep), then converts the SVG to PNG via `cairosvg`
   - `PlainTUI` and `JsonTUI` return `None` — there is no visual to capture
3. Captured PNGs land in `runs/<session>/frames/` with deterministic names:
   - `round_N_end.png` — after every completed round
   - `converged.png` — at convergence, just before party mode
   - `error_round_N.png` — on crash / stall / zero-progress
4. Party mode (`_run_party_mode`) scans `frames/` and picks the last 3-5 images
   (convergence + the two or three rounds before it). These are attached to
   each agent's prompt as image blocks via the Claude Agent SDK's multimodal
   input format:

   ```python
   messages = [{
       "role": "user",
       "content": [
           {"type": "text", "text": prompt_text},
           {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": ...}},
           ...
       ],
   }]
   ```

5. The agents can now cite visual evidence in `party_report.md` ("the round
   header's progress bar was clipped at 80 cols", "the completion summary
   buries the improvement count below the fold") and propose concrete visual
   fixes in the spec proposal.

**Bonus — visual evolution report.** When frame capture is on,
`evolution_report.md` embeds the captured PNGs inline under a "Timeline"
section, so post-session review shows a visual progression of the run, not
just a table of commit messages.

**Dependencies.** Frame capture requires `cairosvg` (for SVG→PNG conversion).
Install with `pip install ".[vision]"`. When the `[vision]` extra is missing,
`capture_frames = true` is a no-op and evolve logs a one-line warning at
startup — the run is never blocked on a missing optional dep.

**Headless-safe.** The entire pipeline runs inside Rich's internal buffer
plus `cairosvg`; no X11, no Wayland, no real terminal screenshot. Works in CI,
Docker, and remote SSH sessions identically.

---

## Phase 5 — Party mode (post-convergence)

After convergence, all agents from `agents/` brainstorm the next evolution.

**Inputs:**
- Agent personas from `agents/*.md`
- Workflow from `workflows/party-mode/`
- Current spec (SPEC.md or whatever `--spec` points at), improvements history,
  memory
- Recent captured TUI frames from `runs/<session>/frames/` (when
  `capture_frames = true`). The last 3-5 PNGs covering the final rounds +
  convergence are attached to each agent's prompt as image blocks, giving them
  visual context for the run.
- Session cost summary from `state.json` `usage` field — agents can factor
  cost efficiency into their next-cycle proposals

**Outputs:**
- `party_report.md` — full discussion explaining each agent's reasoning
- `<spec>_proposal.md` — complete updated spec for the next cycle (filename
  derived from `--spec`: `SPEC.md` → `SPEC_proposal.md`, or `README.md` →
  `README_proposal.md` when no `--spec` is set since README is then the spec)

Party mode does **not** produce any additional README output when `--spec`
points at a separate file. README is user-authored and untouched by the
evolution loop (see § "README as a user-level summary"). If the operator
wants to refresh README to reflect the newly-adopted spec, they run
`evolve sync-readme` explicitly after the proposal is adopted.

The operator reviews the output files and decides whether to accept the
proposal. In `--forever` mode the spec proposal is adopted automatically.

---

## Git convention

Every commit follows conventional commits:

```
<type>(<scope>): <short description>

<body>
```

Types: `fix`, `feat`, `refactor`, `perf`, `docs`, `test`, `chore`

---

## Exit codes

`evolve start` returns meaningful exit codes for CI/CD integration:

| Exit Code | Meaning |
|-----------|---------|
| 0 | Converged — project fully matches spec |
| 1 | Max rounds reached or budget reached — improvements remain |
| 2 | Error — agent failure, missing deps, or invalid args |

```bash
# Use in CI
evolve start . --check "pytest" --rounds 20
if [ $? -eq 0 ]; then echo "Converged!"; fi
```

```bash
# Full CI/CD example with JSON output
evolve start . --check "pytest" --rounds 20 --json > evolve-output.jsonl
EXIT_CODE=$?
if [ $EXIT_CODE -eq 0 ]; then
  echo "Converged! Creating PR..."
  # Parse evolve-output.jsonl for PR description
fi
```

---

## CI/CD integration

### GitHub Actions

Evolve works in CI/CD pipelines out of the box. Here's a GitHub Actions
workflow that evolves a project and creates a PR with the results:

```yaml
name: Evolve
on:
  workflow_dispatch:
  schedule:
    - cron: '0 2 * * 1'  # Weekly on Monday at 2am

jobs:
  evolve:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: '3.12'

      - name: Install evolve
        run: pip install .

      - name: Run evolution
        env:
          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
        run: |
          evolve start . --check "pytest" --rounds 20 --max-cost 50 --json > evolve-output.jsonl
          echo "EXIT_CODE=$?" >> $GITHUB_ENV

      - name: Create PR on convergence
        if: env.EXIT_CODE == '0'
        uses: peter-evans/create-pull-request@v6
        with:
          title: 'feat: evolve convergence'
          body: |
            Automated evolution run converged.
            See `runs/*/evolution_report.md` for details.
          branch: evolve/ci-run
```

### Validation in CI

Use `--validate` as a quality gate in pull request checks:

```yaml
name: Spec Compliance
on: [pull_request]

jobs:
  validate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: pip install .
      - name: Validate spec compliance
        env:
          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
        run: evolve start . --validate --check "pytest"
```

---

## Development

Evolve has its own test suite. Run it with pytest:

```bash
# Run all tests
pytest tests/

# Run with coverage
pytest tests/ --cov=evolve --cov-report=term-missing
```

### Test coverage target

The project targets **95% test coverage** minimum. Current coverage should be
verified before merging any changes:

```bash
pytest tests/ --cov=evolve --cov-report=term-missing --cov-fail-under=95
```

Agent.py specifically targets 95%+ coverage. The gap in prior cycles was
the SDK interaction paths — `analyze_and_fix` core loop, party agent async
runner, and sync-readme agent. These are covered by mocking the `ClaudeAgent`
boundary with a fixture that yields controlled tool-use sequences, rather
than requiring a live API key.

### Test structure

```
tests/
├── test_orchestrator.py    # round lifecycle, subprocess monitoring, watchdog
├── test_agent.py           # build_prompt, error helpers, retry logic, SDK mock
├── test_git.py             # git operations, commit, push, branch management
├── test_state.py           # state.json, improvements parsing, convergence gates
├── test_party.py           # party mode orchestration
├── test_tui.py             # TUI Protocol parity, RichTUI, PlainTUI, JsonTUI
├── test_cli.py             # CLI arg parsing, config resolution, subcommands
├── test_hooks.py           # hook loading, event matching, execution, timeout
├── test_costs.py           # token tracking, cost estimation, budget enforcement
├── test_smoke.py           # end-to-end smoke test (one round against trivial project)
└── ...
```

Tests cover all pure utility functions without requiring the Claude SDK.
Integration tests that need the SDK use mocked responses. Error-path tests
verify graceful degradation under failure conditions (corrupted files,
timeouts, missing dependencies).

### End-to-end smoke test

`test_smoke.py` contains a single test that exercises the full pipeline:
creates a temporary git repo with a 3-file Python project and a spec that
has one deliberate gap, runs `evolve start --rounds 1 --check "pytest"` with
a mocked Claude agent that makes a single edit, and verifies that
`improvements.md` was updated, `state.json` was written, and the check
command passed. This catches regressions in the full subprocess → agent →
git → state pipeline that unit tests miss.

### Path-agnostic drift tests

Constant-drift tests (`test_constant_drift.py`) and spec-prompt-sync tests
(`test_spec_prompt_sync.py`) scan source files for magic strings and
invariants. These tests use dynamic package discovery (`importlib` or
`glob`) to find source files rather than hardcoding paths like `"loop.py"`.
This ensures the tests survive the package restructuring without per-file
updates.
