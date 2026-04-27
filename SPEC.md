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

**Package migration (archived).** The flat-module layout (`loop.py`,
`agent.py`, `tui.py`, `hooks.py` at project root) was restructured
into the `evolve/` package over rounds 5-22 (steps 1-10).
Completed; shims removed.

→ Full step-by-step history: [`SPEC/archive/001-package-migration.md`]

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

### The `.evolve/` directory

All evolve-produced artifacts — session runs, cumulative backlog,
memory log, frames, reports — live under **`.evolve/`** at the root
of the project being evolved, following the same dotfile-directory
convention as `.git/`, `.vscode/`, `.pytest_cache/`, `.venv/`.

**Rationale.**  When evolve is used as a pip-installed module driving a
third-party project (`python -m evolve start <target-project>`), the
target project is NOT evolve's own repository — it's arbitrary user
code.  Dropping a top-level `runs/` directory into that project
pollutes its root with a non-idiomatic name that clashes with the
target's own conventions and shows up in every `ls`, every
`git status`, every IDE file tree.  The `.evolve/` prefix makes it
immediately obvious the directory is tool-managed state, follows
the universal dotfile convention every developer already knows, and
is easy to gitignore (a single `.evolve/` line) for projects that
treat evolution artifacts as local-only.

**Layout.**

```
<project>/
├── README.md                          # user-facing documentation
├── SPEC.md                            # THE SPEC — evolve converges to this
├── evolve.toml                        # (optional) project-level config
├── .evolve/                           # ← tool-managed, like .git/
│   └── runs/
│       ├── improvements.md            # shared — one improvement added per round
│       ├── memory.md                  # shared — cumulative learning log (append-only, compacted past ~500 lines)
│       ├── 20260324_160000/           # session 1
│       │   ├── state.json             # real-time session state (queryable)
│       │   ├── conversation_loop_1.md # full implement-agent SDK stream (Amelia)
│       │   ├── conversation_loop_1_attempt_1.md   # per-attempt implement stream when retries occur
│       │   ├── conversation_loop_1_attempt_2.md
│       │   ├── draft_conversation_round_1.md      # full draft-agent SDK stream (Winston + John) — when round was a draft round
│       │   ├── review_conversation_round_1.md     # full review-agent SDK stream (Zara) — when round was reviewed
│       │   ├── curation_conversation_round_1.md   # full memory-curation SDK stream (Mira) — when curation triggered
│       │   ├── archival_conversation_round_1.md   # full SPEC-archival SDK stream (Sid) — when archival triggered
│       │   ├── check_round_1.txt     # post-fix check results
│       │   ├── usage_round_1.json    # per-round token usage
│       │   ├── subprocess_error_round_3.txt  # diagnostic from crashed/stalled round
│       │   ├── evolution_report.md   # post-session summary with timeline
│       │   ├── dry_run_report.md     # (dry-run only) read-only analysis
│       │   ├── validate_report.md    # (validate only) spec compliance report
│       │   ├── diff_report.md        # (diff only) spec compliance delta
│       │   ├── COMMIT_MSG            # (transient) commit message from opus
│       │   ├── frames/               # (optional) captured TUI frames (PNG)
│       │   │   ├── round_1_end.png
│       │   │   ├── round_2_end.png
│       │   │   └── converged.png
│       │   └── CONVERGED             # written by opus when done
│       └── 20260324_170000/          # session 2
│           ├── ...
│           ├── party_report.md      # multi-agent discussion log
│           └── SPEC_proposal.md     # proposed next spec (name mirrors --spec)
└── prompts/
    └── evolve-system.md              # (optional) project-specific prompt override
```

**Single canonical path.**  There is exactly one location for every
artifact: `.evolve/runs/…`.  Code MUST NOT accept both `.evolve/runs/`
and a legacy `runs/` at the same time — ambiguity breaks resume,
breaks cross-round audit, and splits evolution history across two
trees.  Every read and every write resolves to `<project>/.evolve/
runs/<relative>`.

**Legacy `runs/` migration (archived).** Projects that predate
`.evolve/` had a top-level `runs/` directory. The migration
protocol (detect, migrate-in-place or refuse-if-ambiguous) is
completed.

→ Full protocol: [`SPEC/archive/003-legacy-runs-migration.md`]

**Gitignore note.**  The default recommendation is to *track*
`.evolve/runs/` so the evolution audit trail (conversation logs,
state.json, evolution reports) becomes part of the project's git
history.  Projects that treat evolution as ephemeral may gitignore
`.evolve/` entirely or selectively gitignore `.evolve/runs/*/frames/`
(PNGs are expensive in git LFS); both are valid deployments.  Evolve
does not write a `.gitignore` entry automatically.

---

## Multi-call round architecture

A round is **not** a single Claude agent session.  It is a pipeline
of **three narrowly-scoped SDK calls**, each with its own persona,
model, effort level, turn budget, and single deliverable.  The
orchestrator drives the pipeline and routes each call's output to
the next step.

**Design history (archived).** The three-call split replaced an
earlier single-call design that suffered from persona mixing, phase
drift, and opaque failures. The split is ~2x cheaper and more
predictable per round.

→ Full rationale + cost projections + migration: [`SPEC/archive/002-multi-call-design-history.md`]

**Three calls per round.**

| Call          | Persona       | Model         | Effort | Max turns | Deliverable                          |
|---------------|---------------|---------------|--------|-----------|--------------------------------------|
| draft_agent   | Winston + John| Opus (MODEL)  | low    | MAX_TURNS | ONE new US in improvements.md        |
| implement     | Amelia        | Opus (MODEL)  | medium | MAX_TURNS | Code + tests + ``[x]`` + COMMIT_MSG  |
| review_agent  | Zara          | Opus (MODEL)  | low    | MAX_TURNS | ``review_round_N.md`` with verdict   |

All three calls use the same centralized ``MODEL`` (Opus) and
``MAX_TURNS`` constants from ``evolve.agent`` — see § "Single
model: Opus everywhere" below for the rationale.

Each call receives a prompt tailored to its single responsibility
(``prompts/draft.md``, ``prompts/system.md`` for implement,
``prompts/review.md``) — the prompts no longer carry every phase's
instructions in one file.

**Pipeline flow.**

```
┌─ pre-check (subprocess pytest, 20s cap) ────────────────────┐
│                                                              │
├─ if pre-check FAILED  OR  improvements.md has ≥1 [ ] item:  │
│     call implement_agent(target_US)                          │
│       → Amelia edits code + tests                            │
│       → Phase 1 fixes the failing check FIRST (always, even  │
│         when target_US is None — broken tests outrank        │
│         backlog work AND outrank drafting)                   │
│       → checks off [ ] → [x]                                 │
│       → writes COMMIT_MSG                                    │
│       → RETURN                                               │
│                                                              │
│   else (pre-check PASSED  AND  backlog drained):             │
│     call draft_agent(spec, backlog, memory)                  │
│       → Winston + John pipeline (telegraphic role-play)      │
│       → append ONE new US to improvements.md                 │
│       → write COMMIT_MSG "chore(spec): draft US-NNN"         │
│       → RETURN                                               │
│                                                              │
├─ orchestrator stages + commits COMMIT_MSG                    │
│                                                              │
├─ post-check (subprocess pytest, 20s cap)                     │
│                                                              │
├─ call review_agent(round_num, US, diff)  [implement only]   │
│     → Zara four-pass attack plan                             │
│     → write review_round_N.md with verdict + findings        │
│     → RETURN                                                 │
│   (draft rounds skip review — no code surface to audit)      │
│                                                              │
├─ orchestrator parses review_round_N.md                       │
│     APPROVED → proceed to Phase 4 convergence check          │
│     CHANGES REQUESTED → retry with REVIEW: diagnostic        │
│     BLOCKED → retry with REVIEW: diagnostic (same auto-fix)  │
│                                                              │
└─ Phase 4 convergence (deterministic, no agent)               │
```

**Routing invariant: broken pre-check always routes to implement.**
A failing pre-check pre-empts every other routing condition,
including a drained backlog.  The reason is the same as the rule
that puts Phase 1 (errors first) before Phase 3 (improvements) in
``prompts/system.md``: drafting a new US item on top of a broken
test suite is non-sensical — the next implement round would have
to fix the breakage anyway, and the new draft is at best wasted
work (the failure may invalidate the US's premises) and at worst
actively harmful (Winston + John reason about a codebase whose
behavior cannot be trusted).  The orchestrator emits a dedicated
``pre-check failed with drained backlog — routing to implement``
probe line when this branch fires, so the override is observable.
Only when the pre-check is green AND the backlog is drained does
the orchestrator route to ``draft_agent``.

**What each prompt file contains.**

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

**Orchestrator contract (evolve/orchestrator.py).**

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

**Retry semantics within a round** are unchanged: if any of the
three calls crashes, stalls, or produces a no-progress outcome,
the orchestrator retries the FAILED call (not the whole round).
Scope-creep and backlog-violation detections still apply to the
implement call's commit.  Circuit breaker still fires on three
identical failure signatures.

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

Maximum time (seconds) the check command (pre-check and post-check)
is allowed to run before being killed.  **Default: 20 seconds** —
deliberately aggressive.

```bash
--timeout 20     # Default — hard ceiling on the full test suite
--timeout 60     # Bump only when genuinely large
```

**Why 20 seconds as the default.**  A fast test suite is a
quality invariant, not a target.  When tests run in ≤ 20 s the
agent can run, verify, fix, verify, iterate — all within a
reasonable round budget.  When they creep past 20 s, the evolve
loop degrades: heartbeats stretch, the agent waits longer between
edit-and-verify cycles, the watchdog overhead grows, and overall
throughput drops.  The 20-second ceiling forces the agent to
investigate slowness (mark a flaky/slow test, tighten a fixture,
drop an expensive integration dep) rather than silently paper
over it with a bigger budget.

**What happens on TIMEOUT.**

The pre-check / post-check ``subprocess.run(check_cmd, timeout=20)``
raises ``TimeoutExpired``.  The orchestrator writes
``check_output = "TIMEOUT after 20s"`` and passes that into the
agent's next prompt.  The agent recognises the TIMEOUT token and
switches into slowness-investigation mode: run `pytest
--durations=5` *outside* the watchdog (typically by asking the
operator), identify the offending test, apply the appropriate
remedy (mark, fix, or exclude), verify the suite comes back under
20 s, then resume the original target.

**Single-source-of-truth: agents must NOT run the check command
themselves.**

The orchestrator is the only actor that invokes the check command
— once in pre-check (before the agent runs) and once in post-check
(after the agent commits).  The agent receives both outputs in its
prompt and is **forbidden** from running the check command via its
own Bash tool.  Two reasons:

1. **Cost and time explosion.**  Each agent-side run is another
   full test-suite execution layered on top of the orchestrator's
   two.  A chatty agent that runs pytest after every edit turns one
   round's budget of ``2×20s`` into ``10×20s`` trivially.
2. **Watchdog / heartbeat budget.**  The agent's Bash calls run
   inside the round subprocess where the round-wide heartbeat
   keeps the parent watchdog quiet; a long agent-side pytest
   (especially piped through ``| tail``) still consumes real wall
   time, eating into the ``--max-cost`` budget and the operator's
   patience.
3. **Single authoritative signal.**  Two independent pytest runs
   can disagree (flaky test, different CWD, environment drift).
   One orchestrator-controlled run is the source of truth.

The agent reasons from the orchestrator's pre-check output
(``## Check results`` section in the prompt), makes targeted edits,
and trusts the orchestrator's post-check to verify.  If the agent
needs finer granularity (single-file test, ``--durations=5``,
``-x``), it MUST edit the test file or fixtures and let the
orchestrator's next round re-run — not spawn a separate suite.
The system prompt in ``prompts/system.md`` encodes this as the
default rule.

**Narrow escape hatch: ``timeout``-wrapped verification.**

There is one narrow case where the agent genuinely needs fresh
pytest output mid-turn: it has just edited one or more test files
and the orchestrator's post-check (running at round end) cannot
help because the agent must decide whether to proceed with the
current commit or revert.  In that case the agent is permitted to
run the check command ONCE, but **only when wrapped in the
system-level ``timeout`` utility** with the same budget the
orchestrator uses:

```bash
timeout {check_timeout} pytest tests/test_foo.py -x -q
```

(The ``{check_timeout}`` placeholder is substituted into the
system prompt at round start from the resolved ``--timeout`` /
``EVOLVE_TIMEOUT`` / ``evolve.toml`` value — whatever the
orchestrator itself uses.)

A bare ``pytest`` / ``npm test`` / ``cargo test`` call without the
``timeout`` prefix is forbidden regardless of justification — it
bypasses the quality invariant and can stall the round for
minutes.  The agent is instructed to prefer ``-x`` plus a single-
file scope to keep the run well under the ceiling.

**Future enforcement.**  A permission-callback hook on the SDK's
Bash tool could auto-reject or auto-wrap check-like commands that
arrive without ``timeout``; this is deferred to a separate backlog
item (the current implementation is prompt-level only).

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
--effort max      # maximum reasoning, highest quality, highest cost
--effort high     # deeper reasoning with a smaller budget than max
--effort medium   # Default — balanced cost / quality / latency
--effort low      # quick iteration on simple targets
```

Also configurable via `evolve.toml`:

```toml
[tool.evolve]
effort = "medium"
```

And `EVOLVE_EFFORT` environment variable. Resolution order is standard:
CLI → env → `evolve.toml` → `pyproject.toml` → default.

**Default is `"medium"`.** `medium` gives the best
cost/quality/latency ratio for the majority of evolve rounds — small
fixes, test additions, incremental refactors, doc tweaks. Bumping to
`--effort high` or `--effort max` is a per-session decision when the
backlog contains genuinely hard work (architectural changes,
multi-file coordination, subtle invariant preservation). `--effort
low` remains appropriate for quick iteration or `evolve diff`-style
survey runs.

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

### Item format — user story with acceptance criteria

Every new item written to `improvements.md` — whether added one-at-a-
time per Phase 3 step 6, or generated by the Phase 2 spec-freshness
rebuild — MUST be a **user story (US)** with explicit acceptance
criteria.  Free-form prose items are rejected (treated as a
"no progress" round by the orchestrator's backlog-discipline check,
see below).

The agent drafts each US through a forced **architect → PM review**
pass, role-playing the two personas in `agents/architect.md`
(Winston) and `agents/pm.md` (John) before writing the item to disk.
This is not party mode — no multi-agent subprocess is spawned.  It's
a mandatory mental rehearsal that raises item quality by forcing the
model to answer "what user value does this deliver?" (PM lens) and
"what does the technical design look like and what can go wrong?"
(Architect lens) *before* committing.

**Template.**

```
- [ ] [type] [priority] US-<id>: <one-line summary>
  **As** <role>, **I want** <capability> **so that** <value>.
  **Acceptance criteria (must all pass before the item is [x]'d):**
  1. <testable criterion — observable via tests, CLI output, or file state>
  2. <testable criterion>
  3. <testable criterion>
  **Definition of done:**
  - <concrete artifact — file, test, doc, commit>
  - <concrete artifact>
  **Architect notes (Winston):** <pattern choice, constraint, risk,
  integration point>
  **PM notes (John):** <user value, priority rationale, what this is
  explicitly NOT>
```

- `<id>` is a monotonically increasing integer, unique per project.
  The next ID is `max(existing_ids) + 1` (zero-padded to 3 digits:
  `US-001`, `US-002`, …).  IDs are never reused even after deletion.
- `<type>`, `<priority>` follow the existing tag conventions.
- `<role>` is typically "evolve operator", "agent", "reviewer",
  or a project-specific persona — pick the one whose workflow the
  capability actually touches.
- Each acceptance criterion MUST be *testable* — phrased so a human
  (or a post-round check command) can answer yes/no without
  interpretation.  Bad: "code is clean".  Good: "``pytest tests/
  test_<module>.py`` passes".
- Acceptance criteria count: minimum 2, typical 3-5, absolute max 8.
  If the list would exceed 8, the item is too large — split into
  sub-items (subject to backlog discipline rule 1: only split when
  the queue is otherwise empty).

**Forced review sequence — three personas.**

The full persona pipeline mirrors a real product-engineering loop:

| Stage       | Persona (file)              | Output                                           |
|-------------|-----------------------------|--------------------------------------------------|
| Draft       | Winston — Architect (`agents/architect.md`) | Technical design considerations, pattern choice, risks |
| Validate    | John — PM (`agents/pm.md`)  | User value, priority rationale, explicit non-goals |
| Implement   | Amelia — Dev (`agents/dev.md`) | Code + tests that satisfy every acceptance criterion |

**When a new item is being added** (Phase 3 step 6 in
`prompts/system.md`, or Phase 2 rebuild) the agent MUST role-play
**Winston → John → final draft** in its own conversation log
**before** writing the item to `improvements.md`:

```
### Drafting US-<id> — architect pass
[Winston speaks — pattern choice, constraints, risk, integration]
...
### Drafting US-<id> — PM pass
[John speaks — user value, priority rationale, explicit non-goals]
...
### US-<id> final draft
[the rendered item exactly as it will land in improvements.md]
```

**When the current target is being implemented** (Phase 3 steps 2-4,
i.e. picking up an existing `[ ]` item and turning it into `[x]`),
the agent MUST role-play **Amelia** for the implementation block:

```
### US-<id> implementation — dev pass
[Amelia speaks — ultra-succinct, file paths and AC IDs, one line
per edit, one line per test]
```

Amelia's contract is in `agents/dev.md`: *"All existing and new
tests must pass 100% before story is ready for review.  Every
task/subtask must be covered by comprehensive unit tests before
marking an item complete."*  This is not decorative — the [x]
checkoff is forbidden until every acceptance criterion has a
corresponding passing test and Amelia has cited the file path
where the criterion is enforced.

The four blocks in the conversation log (Winston draft, John
validate, final draft, Amelia implement) are the audit trail — an
operator reviewing the round's log can see the persona reasoning
that shaped the item AND the disciplined implementation that closed
it.  The circuit-breaker / prior-round-audit paths can spot
shortcuts (a one-line "Winston: looks fine" with no substantive
reasoning, or an Amelia block with zero file-path citations) →
retry with stricter instruction.

**Orchestrator check (pre-commit).**  Immediately after the
existing backlog-discipline rule 1 check (§ "Backlog discipline"
below), the orchestrator also verifies that every newly-added
`[ ]` line in the committed `improvements.md` matches the US
header regex (`^- \[ \] \[\w+\](?: \[\w+\])* US-\d{3,}: `) and
that the item body includes the three required section headers
(`**As**`, `**Acceptance criteria`, `**Definition of done`).
Missing any of these triggers a debug-retry diagnostic header
`"CRITICAL — US format violation: new item lacks required
sections"` and the agent is re-invoked with a prompt that
includes the missing pieces.

**Rationale.**  Free-form improvement items let the agent write
vague targets like "improve test coverage" or "refactor the config
loader" that the *same* agent later finds impossible to declare
[x]-done unambiguously.  US format with acceptance criteria forces
the definition-of-done *before* the work starts, which both (a)
sharpens implementation (the agent knows exactly what to build)
and (b) makes the [x] checkoff verifiable by the orchestrator's
post-round check (each criterion maps to a runnable assertion).

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

### Dedicated memory curation (Mira)

The main round agent's "append-only during work, compact past 500
lines" contract has a structural weakness: the same entity that
*writes* memory entries is asked to *decide* which past entries
stay in the working prompt.  This produces two failure modes in
practice:

1. **Authored-it-must-stay bias.**  The agent keeps its own recent
   entries even when they've become historical noise, because
   removing them feels like discarding its own work.
2. **Turn-budget contamination.**  Asking the same agent that just
   spent a turn implementing a US to also compact memory inflates
   the turn budget and risks a "compact aggressively to save
   context" shortcut that silently wipes real signal.

The orchestrator therefore spawns a **dedicated curator agent
(Mira, `agents/curator.md`)** with a single narrow job: triage the
existing `memory.md` into KEEP / ARCHIVE / DELETE decisions.  The
full protocol is in `tasks/memory-curation.md`; in short:

- **When.**  Between rounds (after post-check, before the next
  round's pre-check) when ANY of: `memory.md` > 300 lines (soft
  cap); rolling round counter is a multiple of 10; explicit
  operator request.  Skipped otherwise — Mira does not run on
  every round.
- **Persona.**  Mira is NOT the round's draft/implement persona
  (Winston / John / Amelia) and NOT the reviewer (Zara).  Fresh
  eyes, no authorship bias.
- **Model + effort.**  Opus (centralized ``MODEL``), ``effort=low``,
  ``max_turns=MAX_TURNS`` — see § "Single model: Opus everywhere"
  for the rationale.  Curation is triage, not architectural
  reasoning, but Opus at low effort still avoids the misclassification
  errors Sonnet produced on memory-triage decisions.
- **Input scope.**  Current `memory.md` + SPEC § "memory.md" + last
  5 rounds' conversation-log titles + `git log --oneline -30`.
  Mira does NOT see prior curation audit logs (each curation is
  fresh — no chain-effect bias where a past curator's mistakes
  propagate).
- **Four passes.**  Duplicate detection → rediscoverability audit
  (can a future agent find this by reading SPEC / code / commit?)
  → historical archival (entries > 20 rounds old with no forward
  signal) → section hygiene (empty sections stay as stubs; section
  order is SPEC-locked; `## Archive` is append-only).
- **Output.**  Rewritten `memory.md` + audit log at
  `{run_dir}/memory_curation_round_{N}.md` with a KEEP / ARCHIVE /
  DELETE ledger and a narrative summary.
- **Safeguards.**
  - The rewrite must include `memory: compaction` in the commit
    message (unchanged from the existing byte-size sanity gate).
  - If the rewrite would shrink `memory.md` by > 80%, the
    curation is **aborted** — original file restored, audit log
    saved with `verdict: ABORTED`, operator warned.  This is a
    belt-and-suspenders guard on top of the 50% gate: a 60-80%
    shrink might be legitimate (big session crossed the cap), but
    > 80% is almost always a prompt misfire.
  - Archive is soft-delete: removed entries land in `## Archive`
    at the bottom of `memory.md`, still on disk, still greppable.
    Only true duplicates are deleted outright, and even those
    leave a trace in the audit ledger.

**Verdict routing.**

| Verdict     | Condition                                              | Orchestrator action                                                       |
|-------------|--------------------------------------------------------|---------------------------------------------------------------------------|
| CURATED     | Rewrite within bounds, audit log present                | Commit with `memory: compaction` marker.  Ledger preserved on disk.       |
| SKIPPED     | Threshold not hit                                      | No curation run; next round proceeds normally.                            |
| ABORTED     | Rewrite would shrink by > 80%                          | Restore original `memory.md`; save audit with `verdict: ABORTED`; warn.   |
| SDK FAIL    | No audit log, or schema malformed                       | Restore original; warn; next round proceeds.                              |

**Why this is worth the extra SDK call.**  Without Mira, the main
agent's memory contract devolves to either (a) append forever and
suffer prompt bloat, or (b) compact during the main turn and risk
wipes.  With Mira, the main agent stays strictly append-only
(simple contract, no bias), and the curator handles the prune in
isolation with a dedicated cheaper model.  Net cost is lower than
the status quo where memory bloat inflates every subsequent
round's prompt budget.

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

**Carve-out: scope creep (rebuild + implement in one round).**

A round that adds new ``[ ]`` items to ``improvements.md`` AND
modifies non-improvements files (code / tests / docs) in the
same commit is mixing two round kinds: Phase 2 backlog rebuild
and Phase 3 implementation.  Earlier versions of the system
prompt explicitly encouraged this ("your round target becomes
the FIRST of the newly rebuilt items") and the symptom reported
by operators was exactly that pattern: a rebuild round that
drafts multiple US items AND starts coding the first one, 300+
seconds per round, no clean commit boundary between planning
output and code changes.

The orchestrator now detects the mix — ``backlog_new_items > 0``
AND ``git diff-tree --name-only HEAD`` lists files outside
``improvements.md`` / ``memory.md`` / the runs base — and emits
a dedicated ``SCOPE CREEP:`` diagnostic.  ``build_prompt`` in
``agent.py`` surfaces a ``## CRITICAL — Scope creep: rebuild
mixed with implementation`` section instructing the retry to:

1. ``git reset HEAD~1`` (if the commit went through).
2. Stage ONLY the ``improvements.md`` rebuild.
3. Discard the code / test / doc edits — the next round's fresh
   agent will re-derive them from the rebuilt backlog.
4. Write ``chore(spec): rebuild backlog after spec change`` (or
   similar) and stop the round.

The next round picks up the first new item and implements it
cleanly.  Rebuild rounds produce a clean commit boundary
between planning and coding; the git history shows them as
distinct actions rather than one mashed-up commit.

**Carve-out: backlog drained, ``CONVERGED`` skipped.**

There is one case where ``imp_unchanged=True`` + ``no_commit_msg=True``
is *not* a failure: every ``[ ]`` item in `improvements.md` has
been checked off but the agent stopped short of writing
``CONVERGED``.  The round had nothing to implement — the correct
next step is Phase 4 (verify README claims, then converge), not a
zero-progress retry that pushes the agent to fabricate filler
work.

The orchestrator detects this state — ``_count_unchecked(imp) ==
0`` AND ``imp_unchanged`` AND no ``CONVERGED`` marker — and emits
a dedicated ``BACKLOG DRAINED: all [ ] items checked off, but
agent did not write CONVERGED`` diagnostic instead of the generic
``NO PROGRESS`` one.  ``build_prompt`` in ``agent.py`` recognises
the prefix and surfaces a ``## CRITICAL — Backlog drained,
CONVERGED skipped`` section that steers the retry straight to
Phase 4 (re-read the spec line by line, verify each claim, write
``CONVERGED`` or add exactly one new US item covering a genuinely
missing claim).  Explicit guard in the prompt: do NOT fabricate
filler improvements to make the round look productive — that is
worse than not converging.

### Single model: Opus everywhere

Every evolve agent — implement (Amelia), draft (Winston + John),
review (Zara), memory curation (Mira), SPEC archival (Sid),
sync-readme, dry-run, validate, diff, party — uses the centralized
``evolve.agent.MODEL`` constant, currently set to ``claude-opus-4-6``.
There are no per-agent model overrides.

**Model selection rationale (archived).** Sonnet was tried for
"lighter" agents (draft, review, curation) and produced
hallucinated US items, adversarial review false positives, and net
higher cost from churn. All agents now use Opus at medium effort
with mandatory pre-draft verification (Step 0).

→ Full rationale + failure modes: [`SPEC/archive/006-model-selection-rationale.md`]

### Per-turn cap as a granularity forcing function

The Claude Agent SDK's `max_turns` parameter is set to a deliberately modest
value (currently `60`). This is not primarily a safety bound — the
120s watchdog plays that role — but a forcing function that makes evolve's
core concept ("one granular improvement per round") observable. An item that
does not fit in 60 turns is a signal the item is too large and should be
split; hitting the cap is therefore *expected* behavior for oversized
targets, and it feeds directly into the zero-progress detection above, which
triggers the debug retry with an instruction to split before retrying.
Raising the cap to mask the signal would mask the granularity violation
instead of fixing it.

**Single source of truth.**  The cap is exposed as the module-level
constant ``evolve.agent.MAX_TURNS`` and **every** ``claude_agent_sdk.query``
callsite in ``evolve/agent.py`` (implement, draft, review, memory
curator, and any future agent) MUST pass ``max_turns=MAX_TURNS``.  No
per-callsite literal (``max_turns=8``, ``max_turns=1``, etc.) is
permitted — divergent budgets per agent role were a maintenance hazard
(stale doc comments, draft-too-tight bugs, no single knob to tune).
Tests that assert the budget MUST import the constant rather than
hard-coding a number, so the value can be tuned in one place without
a sweeping diff.

### Authoritative termination signal from the SDK

Whether a round hit ``max_turns`` is **not** inferred from indirect tells
(missing ``COMMIT_MSG``, ``imp_unchanged``, etc.) — those remain useful
fallbacks but conflate distinct failure modes. The Claude Agent SDK's
``ResultMessage`` is the authoritative source and MUST be inspected:

```python
@dataclass
class ResultMessage:
    subtype: str           # "success" | "error_max_turns" | "error_during_execution"
    is_error: bool
    num_turns: int
    stop_reason: str | None
    duration_ms: int
    ...
```

Every callsite of ``claude_agent_sdk.query`` in ``evolve/agent.py``
(implement, draft, review, memory curator, and any future agents)
captures the **final** ``ResultMessage`` of the stream and:

1. Logs ``subtype`` and ``num_turns`` on a dedicated line in the
   conversation log (e.g. ``Done: 40 messages, 62 tool calls,
   subtype=error_max_turns, num_turns=40``) so post-mortem analysis no
   longer has to guess from tool-call counts.
2. Prints a console-visible warning when ``is_error=True`` —
   ``⚠ Agent stopped: error_max_turns after 40 turns`` — surfaced via
   ``ui.agent_warn`` so the operator sees the signal in the TUI in
   real time, not only in the log file.
3. Returns the ``subtype`` to the orchestrator as part of the agent's
   result tuple, so ``run_single_round`` can branch the retry logic on
   a precise cause:
   - ``error_max_turns`` → retry with a "fix-only, defer investigation"
     prompt header AND record the granularity violation against the
     current target (candidate for split on next rebuild).
   - ``error_during_execution`` → retry with the SDK error surfaced
     verbatim in the diagnostic.
   - ``success`` + ``imp_unchanged`` → genuine "agent decided no work
     needed" path (e.g. backlog drained, see carve-out above) — do
     NOT conflate with a turn-budget exhaustion.

Until this signal is wired through, the orchestrator's zero-progress
heuristic over-fires on ``success`` rounds where the agent legitimately
made no edits, and under-fires when the agent commits a partial fix
just before hitting the cap. Both bugs disappear once ``subtype`` is
the source of truth.

### Complete LLM stream capture per agent invocation

Every Claude Agent SDK invocation evolve makes — implement (Amelia),
draft (Winston + John), review (Zara), memory curation (Mira),
SPEC archival (Sid), sync-readme, dry-run, validate, diff,
party — MUST persist its **complete stream** to a dedicated file
under the run directory.  "Complete" is normative: nothing the
SDK emits about that invocation may be silently dropped.  The
files are the primary debugging surface when an agent
misbehaves; truncated or summary logs make post-mortems
impossible and force operators to re-run with extra
instrumentation, which (a) costs another round's tokens and
(b) frequently fails to reproduce the original misbehavior.

**File naming.**  One file per agent invocation per round
(per attempt for implement, since implement retries within a
round).  Path is always under the session run directory
(``.evolve/runs/<timestamp>/``):

| Agent              | File pattern                                       |
|--------------------|----------------------------------------------------|
| Implement (Amelia) | ``conversation_loop_{N}_attempt_{M}.md``           |
| Implement summary  | ``conversation_loop_{N}.md`` (last successful attempt, copied for backward compatibility) |
| Draft (Winston+John) | ``draft_conversation_round_{N}.md``               |
| Review (Zara)      | ``review_conversation_round_{N}.md``               |
| Memory curation (Mira) | ``curation_conversation_round_{N}.md``         |
| SPEC archival (Sid) | ``archival_conversation_round_{N}.md``             |
| Sync-readme         | ``sync_readme_conversation.md``                   |
| Dry-run / validate / diff | ``{mode}_conversation.md``                  |
| Party               | ``party_conversation.md``                         |

The names use the **agent role** (not the persona) so logs
remain greppable across model / persona changes.

**What MUST be captured.**  For every message the SDK yields
during the invocation, the file MUST contain (in stream order,
deduplicated by block id where the SDK streams partials):

1. ``SystemMessage`` events — at minimum a marker so the
   session boundary is visible.
2. ``AssistantMessage`` ``ThinkingBlock``s — the model's
   extended-thinking blocks, verbatim.  Thinking is the most
   load-bearing diagnostic signal when an agent goes off the
   rails ("I will now refactor the working code because the
   SPEC suggests it could be cleaner") — losing thinking blocks
   is losing the why.
3. ``AssistantMessage`` ``TextBlock``s — the model's plain
   reasoning / narration text, verbatim.
4. ``AssistantMessage`` ``ToolUseBlock``s — every tool call
   with **its name and input** (input may be summarised /
   length-capped at a generous limit, e.g. 2000 chars per
   field, but never elided to "..." without the original
   length recorded).
5. ``ToolResultBlock``s — every tool result with
   ``is_error`` flag and content (length-capped per field, with
   the cap recorded in the file header so a reader knows the
   ceiling).
6. ``RateLimitEvent`` markers — visible as ``> Rate limited``
   lines so retry behavior is reconstructible.
7. The final ``ResultMessage`` — full payload: ``subtype``,
   ``is_error``, ``num_turns``, ``stop_reason``,
   ``total_cost_usd``, ``duration_ms``, ``usage``.  This is
   the authoritative termination signal (§ "Authoritative
   termination signal from the SDK") and MUST appear as the
   last entry in the file, formatted on a single
   ``**Result**: subtype=…, num_turns=…, …`` line so it is
   greppable across logs.

**What MAY be omitted.**  Partial streamed deltas of a block
that the SDK ultimately re-emits as a complete block (the
deduplication-by-id step) — only the final consolidated block
is logged.  Nothing else.

**Format.**  Markdown, one section per message kind (``###
Thinking``, ``**ToolName**``, etc.), so a human can scroll the
file top-to-bottom and reconstruct the run.  Code-block fences
around tool outputs.  No JSON dumps in place of human-readable
sections — the file is for humans first, machines second.

**Real-time write — no buffering.**  Each entry MUST be flushed
to disk **as it is received from the SDK stream**, not buffered
until the agent finishes.  Concretely: open every conversation
log with line-buffering (``open(path, "w", buffering=1)``) or
call ``flush()`` after every ``write()``.  The default Python
file buffering (~4–8 KB block buffer) is forbidden for these
files because it defeats the primary use case:

1. **Live tailing.**  Operators run ``tail -f
   .evolve/runs/<latest>/conversation_loop_N.md`` to watch an
   agent reason in real time.  A buffered log shows nothing
   for minutes, then dumps the whole transcript at once when
   the agent finishes — useless for spotting a stuck agent
   before the watchdog kills it.
2. **Crash forensics.**  When an agent hangs (rate-limited
   forever, infinite tool-loop, OOM) and the watchdog SIGKILLs
   the round subprocess, an unbuffered log preserves
   everything up to the kill instant.  A buffered log loses the
   final 4 KB — which is precisely the part that explains why
   the agent got stuck.
3. **Mid-round operator inspection.**  ``grep "Rate limited"
   .evolve/runs/<latest>/*.md`` only works during a long round
   if the writes have actually hit disk.  Same for any
   live-debugging workflow that opens the log alongside the
   running agent.

The cost of line buffering is negligible (one ``write`` syscall
per ``\n`` instead of one per buffer fill); the diagnostic
value is enormous.  A test MUST exist that writes to a
conversation log path and asserts the on-disk byte count
strictly increases between successive writes — guarding against
a future regression that re-introduces block buffering.

**Length cap exemption from the 500-line rule.**  These
conversation files are data, not source code, and routinely
exceed the project's 500-line cap on Python files (§ "Hard
rule: source files MUST NOT exceed 500 lines").  They are
explicitly out of scope for that rule.

**Operator workflow.**  When a round misbehaves, the operator
opens ``.evolve/runs/<latest>/`` and reads the agent file
matching the suspect role.  ``grep -l "subtype=error_max_turns"
.evolve/runs/<latest>/*.md`` immediately surfaces every agent
that hit the turn cap; ``grep "Rate limited"`` surfaces
throttling; ``grep "is_error=True"`` surfaces tool failures.
This is the contract these files exist to deliver.

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

### Structural change self-detection

Some improvements rearrange the repository itself — file renames, module
extractions, entry-point changes, package-layout moves. These are **risky
for autonomous evolution**: the pytest suite mocks subprocess invocations
and so passes even when the real subprocess launcher is broken (e.g. the
orchestrator invoking a file that just got moved). A round can land a
"successful" commit that makes the next round fail to start, and the
failure repeats forever because the same broken state is on disk.

To prevent silent self-breakage, the agent detects **structural changes**
before committing and explicitly hands control back to the operator
rather than continuing.

**Scope: self-evolution only.**  ``RESTART_REQUIRED`` is a
*self-evolution* safety protocol.  Its purpose is to protect the
**running orchestrator's Python imports** from going stale after a
rename / `__init__.py` edit / entry-point move.  That is only a
problem when the project being evolved IS evolve's own source tree
— i.e. ``python -m evolve start /path/to/evolve`` where
``/path/to/evolve`` is the same repository that provides the
orchestrator's running code.

When evolve is driving a third-party project (the common case —
``python -m evolve start /path/to/foo``), structural changes in
``foo/`` never touch ``evolve/``'s module layout.  The round
subprocess spawns a fresh Python interpreter per round anyway
(``python -m evolve _round …``), so target-project renames would
be visible on the next round's first `import` regardless.  The
agent is therefore instructed to **skip the RESTART_REQUIRED
write** when ``{project_dir}`` is not the same repository as
evolve's own source tree.

The orchestrator implements the same check as defense-in-depth
(``_is_self_evolving`` in ``evolve/orchestrator.py``): even if an
agent mistakenly writes ``RESTART_REQUIRED`` on a third-party
project, the orchestrator silently ignores the marker — the file
stays on disk as an audit trail but no exit-3 fires, no operator
is paged.

**What counts as structural.** Any of the following, detected via
`git diff` / `git status` against the pre-round state:

- A file rename (`git diff --diff-filter=R` reports entries)
- A file creation or deletion that is referenced by `import` / `from X` in
  another tracked file (`grep -l` across the project)
- Changes to `pyproject.toml` sections `[project.scripts]`,
  `[tool.setuptools]`, or dependency lists that move an entry point
- Changes to `evolve/__init__.py`, `evolve/__main__.py`, or any `__init__.py`
  that alters module re-exports
- Creation or deletion of `__main__.py` anywhere in the tree
- Changes to `conftest.py` or `tests/conftest.py` that affect test
  collection / import paths

**Agent-side protocol.** When a structural change is detected during
Phase 3, the agent MUST:

1. Complete the code change as planned and verify tests pass
2. Write `COMMIT_MSG` with a mandatory `STRUCTURAL:` prefix on the first
   line, e.g.:
   ```
   STRUCTURAL: feat(git): extract git operations from loop.py into evolve/git.py

   <body>
   ```
3. Write a `RESTART_REQUIRED` marker in the current run directory with:
   ```
   # RESTART_REQUIRED
   reason: <one-line why the process must restart>
   verify: <shell command(s) the operator should run to check the new state>
   resume: <shell command to continue evolution>
   round: <current round number>
   timestamp: <ISO-8601>
   ```
4. **Skip Phase 4 (convergence) for this round** — leave convergence to
   the next run after restart. Do not write `CONVERGED` even if the
   backlog is empty.
5. Return cleanly from the round subprocess so the orchestrator can
   commit the change and honor the marker.

**Orchestrator-side protocol.** After the agent's round subprocess
returns and before starting the next round, the orchestrator:

1. Runs the normal round-end pipeline (commit, push, check, state.json)
2. Checks for `RESTART_REQUIRED` in the run directory
3. If present:
   - Fires a new `on_structural_change` hook with the marker fields as
     env vars
   - Renders a blocking red panel via `ui.structural_change_required(marker)`:
     ```
     ╭──── Structural Change — Operator Review Required ────╮
     │ Round <N> committed a structural change:             │
     │   <commit subject>                                   │
     │                                                      │
     │ Reason: <marker.reason>                              │
     │                                                      │
     │ Verify before restarting:                            │
     │   $ <marker.verify>                                  │
     │                                                      │
     │ When ready to continue:                              │
     │   $ <marker.resume>                                  │
     │                                                      │
     │ Or abort and revert:                                 │
     │   $ git reset --hard HEAD~1                          │
     ╰──────────────────────────────────────────────────────╯
     ```
   - Exits the evolution loop with **exit code 3** (new — "structural
     change, manual restart required")
4. `--forever` mode does **not** bypass this — structural changes are the
   one category of commit that always pauses autonomy. Auto-continuing
   would re-invoke potentially-broken code.

**Detection confidence.** The signals above are heuristic, not perfect.
False negatives are possible (an agent could do something structural the
heuristic misses). The existing entry-point-integrity guards (subprocess
smoke test, pytest-mocked-subprocess regression test) remain in place as
a backup: if self-detection fails but the change breaks the entry point,
the next round's subprocess crash triggers the zero-progress retry and
then the Phase 1 escape hatch. The structural-change protocol is the
preventive layer; the retry/escape guards are the reactive layer.

### Adversarial round review (Phase 3.6)

After each **implement** round's commit — but before the Phase 4
convergence check — the agent role-plays a dedicated adversarial
reviewer persona (**Zara**, `agents/reviewer.md`) and runs a
skeptical audit of the round's work.  This closes the self-
assessment conflict of interest: without an adversarial pass, the
same agent that drafted the US, implemented it, and decides the
checkoff produces "looks good" reviews by default, and the
cumulative quality of `improvements.md` drifts downward.

**Scope: implement rounds only.**  Zara is **skipped** on draft
rounds (the branch taken when the backlog is drained and Winston +
John write a new ``[ ]`` US into ``improvements.md``).  Three
reasons:

1. **No adversarial code surface.**  A draft round produces only a
   text edit to ``improvements.md`` and a ``COMMIT_MSG`` — there is
   no code, no tests, no behavior change for Zara's four attack
   passes (regression risk, AC compliance, SPEC normative checks,
   structural drift) to bite on.
2. **Drafting is already dual-reviewed.**  The draft agent runs
   Winston (architect) and John (PM) as an internal two-pass review
   of the US before writing it (architecture pattern fit, value /
   priority sanity).  Adding Zara on top is a third reviewer
   reviewing planning, not code.
3. **Empirically high false-positive rate.**  When Zara was run on
   draft rounds she pivoted to wording-quality critique of the US
   (vague AC, fuzzy scope) and routinely returned BLOCKED /
   CHANGES REQUESTED verdicts on perfectly serviceable drafts —
   feeding the auto-retry loop (§ "Verdict → orchestrator action")
   with non-actionable churn.  Skipping Zara on drafts removes the
   noise without losing real signal.

The orchestrator emits ``draft round — skipping review (Zara
reviews implement rounds only)`` when the skip path runs, so the
behavior is observable in the log.

**Persona separation.**  Zara is NOT the same persona as Winston
(architect — drafted the US), John (PM — validated value/priority),
or Amelia (dev — implemented the story).  Persona mixing defeats
the purpose: the drafter and implementer already believe the work
is good.  Zara's mandate is to find what they glossed over.

**Input scope.**  Zara receives exactly these artifacts:

1. The US item text (the `[x] [type] [priority] US-NNN: …` line
   plus its AC block and Definition of Done).
2. `git diff HEAD^ HEAD -- .` — the round's commit.
3. `conversation_loop_{round_num}.md` — including the persona
   blocks that led to the US and the dev's implementation block.
4. `SPEC.md` — for normative-statement compliance.
5. `runs/memory.md` (or `.evolve/runs/memory.md`) — cross-round
   context.

Zara does **not** receive:

- Prior round reviews (each round is reviewed fresh — no chain
  effect, no reviewer-colludes-with-past-reviewer failure mode).
- `state.json` or cost data (irrelevant to code quality).

**Four attack passes.** (Full protocol in
`tasks/review-adversarial-round.md`.)

| Pass | Focus                                       | Key failure modes to find                                          |
|------|---------------------------------------------|--------------------------------------------------------------------|
| 1    | Acceptance-criteria audit                   | AC classified as PARTIAL / MISSING with no test evidence → HIGH    |
| 2    | Claim-vs-reality (dev narrative vs. diff)   | Claim without diff evidence → HIGH; silent diff hunks → MEDIUM      |
| 3    | Code and test quality                       | Placeholder asserts, swallowed exceptions, self-pass tests         |
| 4    | SPEC-compliance                             | Implementation violates a MUST / MUST NOT in `SPEC.md` → HIGH      |

**Minimum findings.**  Zara produces **at least 3 findings** per
review.  If genuinely clean, she enumerates the three highest-risk
areas checked and cites why each was sound — no "looks good"
reviews.  A review with fewer than 3 findings is suspected of
insufficient scrutiny and triggers a retry of the review itself.

**Output.**  Written to `{run_dir}/review_round_{N}.md` with a
strict schema (verdict, categorised findings, reviewer narrative).
The file is committed alongside the round's other artifacts and
becomes part of the evolution audit trail; the prior-round audit
path (§ "Prior round audit") scans prior review files as an
additional anomaly signal.

**Verdict → orchestrator action.**

| Verdict           | Condition                                                    | Action                                                                                                              |
|-------------------|--------------------------------------------------------------|---------------------------------------------------------------------------------------------------------------------|
| APPROVED          | 0 HIGH findings AND 0 MEDIUM findings                         | Round proceeds to Phase 4 convergence.                                                                              |
| CHANGES REQUESTED | 1-2 HIGH findings, OR any MEDIUM findings                     | Orchestrator writes `subprocess_error_round_{N}.txt` with a `REVIEW: changes requested` prefix listing the HIGH **and** MEDIUM findings; triggers a debug retry (same mechanism as NO PROGRESS / MEMORY WIPED).  The retry reads the review and addresses every HIGH and MEDIUM finding before re-committing. |
| BLOCKED           | ≥ 3 HIGH findings, OR any finding tagged `[regression-risk]` | Same auto-retry path as CHANGES REQUESTED — orchestrator writes `subprocess_error_round_{N}.txt` with a `REVIEW: blocked` prefix and triggers a debug retry to auto-fix the findings.  The deterministic-loop guard (§ "Circuit breakers") caps runaway retries; the operator never arbitrates findings manually. |

**Auto-fix invariant.**  Every HIGH and MEDIUM finding is auto-fixed
by the next attempt — there is **no** verdict that drops the session
into a "manual operator review" state.  Earlier versions exited with
code 2 on BLOCKED; that path was removed because (a) operator
arbitration is exactly the kind of manual loop evolve exists to
eliminate, and (b) the circuit breaker / deterministic-loop guard
already provides the safety net for genuinely unfixable findings.
LOW findings are surfaced in the review file for the audit trail but
do not gate the verdict — they are not auto-fixed inside the round.

The `REVIEW:` diagnostic prefix is recognised by `build_prompt` in
`agent.py` alongside the existing prefixes (NO PROGRESS, MEMORY
WIPED, BACKLOG VIOLATION, PREMATURE CONVERGED) and emits a dedicated
`## CRITICAL — Previous attempt failed adversarial review` section
at the top of the retry prompt, with the HIGH findings expanded.

**Interaction with existing mechanisms.**

- Complements `prev_crash_section` (for orchestrator-level failures)
  and `prev_attempt_section` (for within-round retry continuity).
  Zara's output is for *cross-phase* continuity: the dev persona
  committed successfully, but the work still didn't pass skeptical
  review.
- Complements the circuit breaker: if three successive retries
  produce the same CHANGES REQUESTED verdict with the same HIGH
  signature, the circuit breaker's identical-failure fingerprint
  includes `REVIEW: changes requested` and exit 4 fires.
- The minimum-findings rule (≥ 3) prevents Zara from becoming a
  rubber stamp even on clean code — if she cannot find three
  substantive things to check, she is under-scrutinising.

**Future extensions (not required for initial implementation):**

- Session-end review — a higher-level Zara pass at convergence
  that audits the whole session's story arc (did the backlog make
  sense end-to-end, or did rounds produce churn?).
- Forever-cycle review — between forever-mode cycles, audit the
  SPEC proposal adoption for drift from the original spec intent.

### Prior round audit

Every round (≥ 2) runs a pre-flight audit of the previous round's
artifacts before the agent touches the backlog.  The goal is a simple,
unavoidable rule: if round N-1 finished in a state that deserves a
second look, round N has to fix that second-look item *first*, not
carry on with whatever the improvements.md current target says.

**Signals scanned programmatically (by ``_detect_prior_round_anomalies``
in ``agent.py``):**

| Signal                         | Source                                                      |
|--------------------------------|-------------------------------------------------------------|
| orchestrator diagnostic present | ``subprocess_error_round_{N-1}.txt`` exists                  |
| post-fix check FAIL            | ``check_round_{N-1}.txt`` contains ``post-fix check: FAIL`` |
| watchdog stall / SIGKILL       | ``stalled (Ns without output) — killing subprocess`` in log |
| subprocess killed by signal    | ``Round N failed (exit -K)`` in log                         |
| pre-check TIMEOUT              | ``pre-check TIMEOUT after Ns`` in log                       |
| frame capture error            | ``Frame capture failed for X: not well-formed`` in log      |
| circuit breaker tripped (exit 4) | ``deterministic loop detected`` in log                      |

When any signal fires, ``build_prompt`` injects a dedicated
``## Prior round audit`` section at the top of the system prompt
(between ``target_section`` and ``prev_crash_section``) listing every
anomaly detected and the mandatory action sequence: read the three
artifacts named above, identify root cause, apply the fix, commit
with a ``fix(audit):`` prefix, *then* resume the current target.

**Interaction with the existing prev_crash / retry-continuity paths:**

- ``prev_crash_section`` (pre-existing) handles the *strong* signal of
  an orchestrator diagnostic file and tailors the message per crash
  type (MEMORY WIPED, BACKLOG VIOLATION, NO PROGRESS, PREMATURE
  CONVERGED, generic CRASH).  The audit section is *additive*: it
  lists the diagnostic alongside the softer signals (frame capture
  errors, watchdog warnings, circuit-breaker notices) that the
  prev_crash path doesn't surface.
- ``prev_attempt_section`` (retry continuity) handles within-round
  continuity when the agent is on attempt 2 or 3.  The audit section
  handles *cross-round* continuity — the previous round committed,
  but left behind evidence that something needs attention before the
  next target.

**Deferral escape hatch.** If an anomaly is genuinely unfixable (a
flaky external service, a platform-specific bug that doesn't affect
the evolve project itself), the agent is instructed to document it in
``runs/memory.md`` under a ``## Known anomalies`` section rather than
spend every round re-investigating the same known-benign signal.
Rounds audit against that log: if the signal matches a known-anomaly
entry, the section is still rendered (so the operator sees it) but
the agent may acknowledge and proceed.

### Round-wide heartbeat

The parent orchestrator watches each round subprocess with a
silence-based watchdog (`_run_monitored_subprocess`,
`WATCHDOG_TIMEOUT` = 120s of no stdout → SIGKILL).  Several operations
inside a round naturally buffer or suppress output:

- The pre-check / post-check running `pytest` silently while it
  collects or runs long-running tests;
- Agent tool calls that pipe output through `| tail`, redirect to
  `/dev/null`, or pass `-q`/`--quiet` flags;
- Long agent "thinking" gaps between streaming messages (Opus can
  spend tens of seconds on extended reasoning before emitting);
- Git operations on large repos;
- The Claude Agent SDK subprocess buffering at its own layer.

Without intervention, any of these would race the watchdog and lose
— the round gets SIGKILL'd mid-work, the agent never completes, the
debug retry re-enters the same buffering pattern and loses again,
and the circuit breaker eventually fires because three attempts
share the same "stalled" signature.

To prevent that, `run_single_round` starts a daemon heartbeat thread
that prints `[probe] round N alive — Ns elapsed` every 30s for the
entire round duration.  The heartbeat is cheap (one print per
30 seconds), safely terminated via a `threading.Event` in a
`try/finally`, and covers every phase: pre-check, agent invocation,
agent tool calls, git commit, post-check.  Total round duration is
still bounded by the user's budget (`--max-cost`), round count
(`--rounds`), and convergence — the heartbeat removes only the
120-second silence-based cudgel, not those higher-level bounds.

Pre-check and post-check still use their own `subprocess.run(...,
timeout=timeout)` to catch genuinely hung commands.  When that
timeout fires, `check_output` becomes `"TIMEOUT after Ns"`, the
agent is invoked (or the post-check result recorded) normally, and
the agent receives the timeout message in its prompt — so it can
investigate (skip a flaky test, fix a slow fixture, adjust the
check command) rather than watching the round get murdered.

### Circuit breakers

The debug-retry loop retries each failure up to `MAX_DEBUG_RETRIES` times
within a round, and in `--forever` mode the orchestrator skips to the next
round if retries are exhausted. That design is right for **transient**
failures (a flaky test, an agent timeout that clears on retry) but wrong
for **deterministic** ones (a pre-check command that hangs on every round,
an irrecoverable bug that produces the same stack trace every time). Left
unchecked, forever mode would spin on a deterministic failure forever,
burning tokens without recovery.

**The rule.** When the same failure signature repeats across
`MAX_IDENTICAL_FAILURES` (=3) consecutive failed *attempts* — whether
those attempts are the three debug retries of a single round or span
multiple rounds in `--forever` — the orchestrator exits with **exit
code 4** ("deterministic failure loop detected").  Per-attempt (not
per-round) registration is deliberate: the classic pathology is a
pre-check command (e.g. `pytest`) that hangs identically on every
retry, and the first round already exposes three identical failures
that deserve a fast bail-out rather than burning two more rounds
before firing.

**Failure signature.** A short SHA-256 digest of:
1. Failure kind — `"stalled"`, `"crashed"`, or `"no-progress:<prefix>"`
   where `<prefix>` is one of `NO PROGRESS`, `MEMORY WIPED`, `BACKLOG
   VIOLATION`, or `silent`.
2. Subprocess returncode (negative values indicate kill signals).
3. The trailing 500 bytes of subprocess output (stripped), so that
   mostly-deterministic failures with varying prefixes (timestamps,
   round counters) still hash-match on their stable tail.

**When the counter resets.** Any successful round clears the accumulated
signatures, so a single recovery between otherwise-identical failures
resets the threshold. This makes the breaker specific to *sustained*
deterministic failures — it does not fire on occasional repeats
interleaved with progress.

**Relation to exit code 2.** Exit code 2 fires when a round's retries
are exhausted with *heterogeneous* failure signatures — e.g. attempt 1
crashes, attempt 2 stalls, attempt 3 makes no progress.  Mixed
failures are not strong evidence of a deterministic loop (they might
just be flaky infrastructure), so non-`--forever` still exits 2 and
`--forever` still skips to the next round.  Exit code 4 is reserved
for the *homogeneous* case — three attempts with the same signature,
which is the real signal that retrying further cannot help.  A
supervisor (systemd unit, `while true; do evolve start --forever;
done`, operator tmux loop) can distinguish the two and react
differently: restart cleanly on 4, alert-and-stop on 2 (or vice
versa, depending on deployment).

**What to do when you see exit 4.**
1. Check `runs/<session>/subprocess_error_round_*.txt` for the three
   most recent rounds — they will contain the same failure reason.
2. If the failure is in a pre-check command (`pytest`, `npm test`),
   fix the command or its environment before restarting.
3. If the failure is structural (the agent itself is broken), use
   `git log` to find the last round that committed a change, revert
   if needed, and restart.
4. A supervisor restart is safe only after root-cause remediation —
   evolve cannot break its own deterministic loop without human or
   scripted intervention.

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

### Frame capture (archived)

Frame capture snapshots the TUI as PNG at round end / convergence /
errors and hands images to party-mode agents for visual reasoning.
Opt-in via `capture_frames = true` in `evolve.toml`. Requires
`cairosvg` (`pip install ".[vision]"`); headless-safe.

→ Full implementation detail: [`SPEC/archive/005-frame-capture-design.md`]

---

## Phase 5 — Party mode (post-convergence, ``--forever`` only)

**When it fires.**  Party mode runs **only** when the session is in
``--forever`` mode AND convergence was reached in the current cycle.
Its sole purpose is to draft the next spec proposal so the forever
loop has something to converge toward in the next cycle — without
``--forever`` there is no next cycle, so running party mode would
be wasted work (multi-persona Opus call with non-trivial cost).

Concretely, the orchestrator's convergence handler:

- In ``--forever``: calls ``_run_party_mode(…)`` → reads/writes
  ``party_report.md`` + ``<spec>_proposal.md`` → ``_forever_restart``
  adopts the proposal and starts the next cycle.
- Without ``--forever``: skips party mode entirely, logs
  ``convergence reached — skipping party mode`` at probe level,
  exits with code 0 cleanly.

When convergence occurs, all agents from `agents/` brainstorm the next evolution.

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

## Prompt caching

Every agent round's prompt concatenates the persona system text,
``SPEC.md``, ``README.md``, and project context — tens of
thousands of tokens of static-ish content.  If the underlying
runtime does not cache this stable portion between calls, every
round pays the full input-token cost even though the content
rarely changes.

**SDK contract (claude-agent-sdk 0.1.50).**  The Python SDK's
``ClaudeAgentOptions.system_prompt`` signature is ``str |
SystemPromptPreset | None`` — it does NOT accept the Anthropic
API's ``list[dict]`` shape with explicit ``cache_control``
markers.  Passing a list silently mis-serialises and the API call
arrives with no usable system prompt (symptom: model returns
zero tool calls on well-formed rounds).

**How caching actually happens.**  The underlying Claude Code
CLI that the SDK wraps applies prompt caching natively on stable
system prompts across calls — the caller does NOT need to set
``cache_control`` explicitly.  When the same (or leading-prefix
identical) system prompt is sent within the cache TTL, the CLI
translates it into a ``cache_control`` API call under the hood
and the response's ``ResultMessage.usage`` carries
``cache_read_input_tokens > 0``.

**Caller contract (what evolve code must do).**

- Pass ``system_prompt`` as a **single string** to
  ``ClaudeAgentOptions``.  Never a list-of-dicts.
- Keep the **leading portion** of the prompt stable across
  rounds — put per-round variable content (check results,
  memory, attempt marker, prior audit, crash diagnostics)
  **after** the static content.  The CLI's caching is prefix-
  based: the cached portion is whatever's identical up to the
  first byte that differs.
- Observe cache hits via ``ResultMessage.usage.cache_read_input_tokens``
  and record them in ``usage_round_N.json``.

**Wrong patterns (will silently disable caching):**

- Two-block ``system_prompt=[dict, dict]`` with explicit
  ``cache_control`` (doesn't match the SDK signature — see
  symptom above).
- Per-round content interleaved with static content (breaks the
  leading-prefix hash).
- A timestamp or counter in the first ~200 bytes of the system
  prompt (invalidates the prefix every call).

**Acceptance criteria for verification:**

1. A session-level integration test runs two rounds back-to-back
   with identical inputs and asserts that the second round's
   ``usage_round_2.json`` has ``cache_read_tokens > 0`` —
   evidence the native caching fires.
2. No call site in evolve passes ``system_prompt=[...]`` as a
   list; grep/lint guard in CI.
3. ``build_prompt`` and its siblings place per-round variable
   content **after** the static (system.md + SPEC/README)
   portion.  A unit test asserts ordering on the rendered
   prompt.

---

## SPEC archival (Sid)

SPEC.md accumulates content monotonically as features land —
completed migrations, one-shot CI/CD examples, TUI implementation
details, historical design decisions.  These stay in every agent
prompt forever, blowing the context budget without earning their
keep after the feature is stable.  At the time of writing SPEC is
at 2474 lines / ~110 KB — the feedback from the adversarial
reviewer (Zara) during round 1 of session 20260424_145954 named
this as a maintainability risk.

**Persona — Sid (SPEC Archivist, ``agents/archivist.md``).**  A
parallel of Mira (memory curator) for ``SPEC.md``.  Same
discipline, different input: Sid reads SPEC.md, identifies
stable / historical sections, extracts them to
``SPEC/archive/NNN-<slug>.md``, and leaves a short summary stub
in SPEC.md pointing at the archive.  Opus (centralized ``MODEL``),
``effort=low``, ``max_turns=MAX_TURNS`` — runs between rounds not
during them, never touches active contracts.

**Trigger conditions.**  Between rounds (after post-check,
before next round's pre-check) when ANY of:

1. ``SPEC.md > 2000 lines`` (soft cap — the point at which
   prompt bloat starts measurably hurting round latency and cost).
2. Rolling round counter hits a multiple of 20 (periodic safety
   net so long ``--forever`` sessions don't accumulate forever).
3. Operator explicit request (``evolve archive-spec`` — deferred
   to a later US).

**Archive directory layout.**

```
<project>/
├── SPEC.md                           # active contracts, ≤ 2000 lines
└── SPEC/
    └── archive/
        ├── INDEX.md                  # catalog: ID → slug → archive date → trigger context
        ├── 001-migration-strategy.md # completed package restructure (rounds 5-22)
        ├── 002-cicd-integration.md   # GitHub Actions examples
        ├── 003-frame-capture-design.md
        └── 004-tui-internals.md
```

**Stub format in SPEC.md.**  Every archived section leaves a 2-5
line stub at its original location:

```markdown
## Architecture — package migration (archived)

The flat-module layout (loop.py, agent.py, tui.py, costs.py,
hooks.py at project root) was extracted into the ``evolve/``
package over rounds 5-22 (steps 1-10).  Completed; shims
removed.  Current package layout is authoritative.

→ Full step-by-step history: [`SPEC/archive/001-migration-strategy.md`]
  (Read ONLY if diagnosing a package-structure issue.  For
  normal work, the current code layout + shim absence are the
  truth — the archive adds no current-contract signal.)
```

The stub gives the agent the **conclusion** + a **conditional
pointer**.  No reason to read the archive in a normal round.

**Sid's four passes.**

1. **Stability detection** — for each SPEC section, answer:
   is this a current contract (active), a stable mechanism
   (rarely touched, documented once), or historical (migration
   complete, example not current)?  Active stays.  Stable stays
   for now.  Historical → archive candidate.
2. **Stub drafting** — for each archive candidate, draft a
   2-5 line summary capturing the conclusion + conditional
   link.  The stub MUST be strictly shorter than the archived
   content.
3. **Archive extraction** — write the full section to
   ``SPEC/archive/NNN-<slug>.md`` with an ID one higher than
   the current max in ``SPEC/archive/INDEX.md``.  Never reuse
   an ID.
4. **SPEC rewrite** — replace the section body in SPEC.md with
   its stub, update ``SPEC/archive/INDEX.md``.

**Output.**  Updated ``SPEC.md`` + new archive file(s) + updated
``INDEX.md`` + audit log at
``{run_dir}/spec_curation_round_{N}.md`` with a
KEEP / ARCHIVE ledger per section and a narrative summary.

**Read discipline — defense against the read-back loop.**

Zara's concern about archives: *"if the agent follows the link,
it ends up loading it anyway, defeating the purpose"*.  Three
layers of defense:

1. **Physical separation.**  Archives live under ``SPEC/archive/``
   not SPEC.md.  The agent's default context (prompt + README +
   SPEC.md + memory.md + improvements.md) does NOT include them.
2. **Explicit permission rule in ``prompts/system.md``:**

   > ``SPEC/archive/*.md`` are historical records, NOT current
   > contract.  You MUST NOT read them unless ALL of:
   >
   > 1. The current US's target explicitly references a concept
   >    that a SPEC.md stub points to in the archive.
   > 2. The stub's summary is insufficient for the target.
   > 3. You have already read the non-archive sources (SPEC.md,
   >    code, memory.md).
   >
   > The orchestrator logs every Read of ``SPEC/archive/*.md`` to
   > ``{runs_base}/memory.md`` under ``## Archive reads`` with
   > round + justification.  Three archive reads in a single
   > round without justification = scope creep, flagged by Zara
   > at Phase 3.6 review.
3. **Orchestrator-side observability.**  The round subprocess
   inspects its own conversation log post-round for
   ``Read → SPEC/archive/`` patterns.  Each occurrence is
   counted and written to ``memory.md`` under
   ``## Archive reads``.  Zara's Phase 3.6 review reads those
   counts: > 1 read per round without a matching stub
   reference in the US target → severity MEDIUM finding.

**Acceptance criteria:**

1. ``agents/archivist.md`` persona file exists, defining Sid's
   role (parallel of Mira).
2. ``tasks/spec-archival.md`` protocol document exists,
   describing the four passes and the output contract.
3. Orchestrator helper ``_should_run_spec_archival(project_dir,
   round_num)`` returns True only when the trigger conditions
   hold.
4. ``run_spec_archival(project_dir, run_dir)`` in ``evolve/agent.py``
   spawns Sid via the centralized ``MODEL`` + ``effort=low``, writes the
   ``spec_curation_round_N.md`` audit log, and applies the
   rewrite iff the audit log is well-formed.
5. ``SPEC/archive/INDEX.md`` is created (or updated) on the
   first archival pass.
6. ``prompts/system.md`` gains the archive-read discipline
   section with the three-condition gate.
7. Zara's Phase 3.6 review attack plan (``tasks/
   review-adversarial-round.md``) gains an "archive read
   count" signal in Pass 2 (claim-vs-reality).
8. Tests in ``tests/test_spec_archival.py`` cover: trigger
   conditions, four passes on a synthetic SPEC, stub
   shorter-than-body invariant, INDEX.md ID monotonic, audit
   log schema.

**Interaction with prompt caching.**  Combined with the caching
contract (above), the effect compounds:

- The Claude Code CLI's native caching gives ~90% discount on
  the **stable leading prefix** of the system prompt across
  calls within the TTL — that prefix is typically system.md +
  SPEC.md + README.md.
- Archival reduces the volume of that leading prefix by ~30-40%
  over time as stable sections move out, so the first round of
  a session (cache write) is cheaper too.

The two levers are orthogonal: caching attacks the per-call
cost of repeated content (cache reads on round N > 1);
archival attacks the intrinsic volume of what gets included in
every call (cheaper cache writes on round 1).  Both should
land.

**Migration bootstrap — what to archive first.**

The initial archival pass has clear candidates that are
definitively historical (migration complete, example material):

- **Package restructuring migration** (steps 1-10, done rounds
  5-22) — historical record, no forward signal.
- **CI/CD integration examples** — GitHub Actions workflow
  templates, reference material for operators.
- **Development / Test structure details** — how to contribute
  to evolve, outside agent scope.
- **Detailed TUI internals** — the code is self-documenting.
- **Duplicate ``## Cost Summary`` section** (appears at lines
  946 and 2071 — SPEC bug, pick one, archive the other).

These alone should bring SPEC.md from 2474 → ~1600 lines.

---

## Exit codes

`evolve start` returns meaningful exit codes for CI/CD integration:

| Exit Code | Meaning |
|-----------|---------|
| 0 | Converged — project fully matches spec |
| 1 | Max rounds reached or budget reached — improvements remain |
| 2 | Error — agent failure, missing deps, or invalid args |
| 3 | Structural change — manual restart required (see § "Structural change self-detection") |
| 4 | Deterministic failure loop — same failure signature repeated across multiple rounds (see § "Circuit breakers") |

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

## CI/CD integration (archived)

GitHub Actions workflow examples (evolution + PR creation,
`--validate` as a quality gate) are reference material for
operators, not active contract.

→ Full examples: [`SPEC/archive/004-cicd-integration.md`]

---

## Development

Evolve has its own test suite. Run it with pytest:

```bash
# Run all tests
pytest tests/

# Run with coverage
pytest tests/ --cov=evolve --cov-report=term-missing
```

### Hard rule: source files MUST NOT exceed 500 lines

Every file under ``evolve/`` (and any new code module added to the
project) MUST be ≤ 500 lines.  This is a hard structural limit, not a
soft suggestion: a file that crosses the threshold is a signal the
module is doing too many things and MUST be split — either by extracting
a coherent sub-responsibility into its own module, or by promoting an
existing section (constants, helpers, dataclasses) into a sibling file.

**Why 500 specifically.**  Files larger than ~500 lines exceed what can
be held in working memory in one read, force agents (and humans) to
navigate by grep instead of by reading top-to-bottom, and are the
single strongest predictor of merge-conflict density and partial-edit
bugs in this codebase.  ``evolve/agent.py`` historically grew past
2 000 lines and was the source of repeated regressions where a fix in
one section broke an unrelated section the editor had not seen — the
500-line cap is the fix.

**Scope.**  Applies to all ``*.py`` files under ``evolve/`` and
``tests/``.  Generated files, fixtures embedded as data, and
``SPEC.md`` itself are exempt.  Comments and blank lines count toward
the total — wrapping a 600-line file in extra docstrings to dodge the
limit defeats the rule.

**Enforcement.**  A pre-commit / CI check counts non-empty + non-pure-comment
lines per Python file under ``evolve/`` and ``tests/`` and fails the
build on any file > 500.  The orchestrator's debug-retry diagnostics
also surface a ``FILE TOO LARGE:`` header when a round leaves any
``evolve/*.py`` file over the cap, so the next round picks up the split
as its target instead of piling more code on top.

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

### Test structure, smoke test, and drift tests (archived)

Test layout (`tests/test_*.py`), the end-to-end smoke test
(`test_smoke.py`), and path-agnostic drift tests
(`test_constant_drift.py`, `test_spec_prompt_sync.py`) are stable
reference material.

→ Full details: [`SPEC/archive/007-development-testing.md`]

### Hard rule: tests MUST NOT call the real Claude SDK

Every test that exercises code paths touching the Claude Agent SDK —
``analyze_and_fix``, ``run_claude_agent``, ``run_dry_run_agent``,
``run_validate_agent``, ``run_sync_readme_claude_agent``,
``_run_party_agent_async``, or any helper that builds a
``ClaudeAgentOptions`` — MUST mock the SDK before invocation.  A
test that reaches a live SDK call is a **correctness bug**, not a
cost concern: live calls add variable latency (seconds to minutes
per test), blow past the 20-second pytest ceiling
(§ "The --timeout flag"), burn tokens, require an API key in CI,
and make the suite non-deterministic.

**Required mocking patterns** (choose the smallest one that fits):

1. **Mock ``run_claude_agent`` directly** — cheapest.  For tests
   that exercise ``analyze_and_fix``'s retry or log-writing
   behaviour without caring about the SDK's internals:
   ```python
   with patch("evolve.agent.run_claude_agent", new=AsyncMock()):
       analyze_and_fix(...)
   ```
2. **Mock the ``ClaudeSDKClient`` / ``query`` import** — for tests
   that exercise the message-streaming path.  ``conftest.py``
   installs a ``claude_agent_sdk`` stub in ``sys.modules`` when
   the real SDK is not present; tests that need more control can
   ``patch.dict(sys.modules, {"claude_agent_sdk": fake_sdk})``
   with a bespoke fake exposing the exact classes and async
   iterators needed.
3. **Patch ``asyncio.run``** — for tests that care only about
   "was the agent invoked" and want to bypass the entire async
   stack:
   ```python
   with patch("asyncio.run", side_effect=lambda c: c.close()):
       ...
   ```

**Forbidden patterns:**

- ``import claude_agent_sdk; …`` without a ``sys.modules`` stub
  (the conftest stub is a safety net, not a blessing — relying on
  it to reach the real SDK when the stub isn't installed is the
  same bug).
- Tests that use the ``pytest.mark.skip_if_no_sdk`` marker to
  "run against real SDK locally, skip in CI" — a test that passes
  locally and skips in CI provides no guarantee about either.
- Helper fixtures that instantiate ``ClaudeSDKClient`` and call
  ``.query(...)`` in their ``setup`` — even if the test itself
  intends to only mock afterwards, the fixture has already paid
  the cost and broken the ceiling.

**How to spot a leak.**  If a test's runtime exceeds ~500 ms in
``pytest --durations=10``, it's either spawning a real subprocess
(legitimate only for ``test_entry_point_integrity.py`` and similar
deliberately-integration tests) or leaking into the SDK.  Audit
such tests on sight: run them in isolation under
``pytest --no-summary -q --timeout=5`` and if they hang or call
out, they've got a leak that needs mocking.
