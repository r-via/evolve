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

### Source code layout — Domain-Driven Design (DDD)

Evolve's source tree under ``evolve/`` MUST follow a Domain-Driven
Design layered architecture.  This is normative: evolve
self-corrects against this layout, and the import-graph linter
(see "Enforcement" below) rejects any inward-violating edge.

**Why DDD here.**  The legacy flat layout (``agent.py``,
``orchestrator.py``, ``state.py``, …) conflates domain
(business concepts: Round, US, Improvement, Verdict),
application (use cases: run_round, draft_us, review_round),
and infrastructure (Claude SDK adapter, git, filesystem) in
single multi-thousand-line modules.  The horizontal split
forced by § "Hard rule: source files MUST NOT exceed 500 lines"
keeps individual files under cap but does NOT enforce
separation of concerns — splitting ``orchestrator.py`` into
``orchestrator_helpers.py`` + ``orchestrator_constants.py`` +
``orchestrator_startup.py`` is layer-blind.  DDD imposes the
missing structural axis.

**Layered structure.**  Code MUST be organised into four layers,
implemented as sibling sub-packages under ``evolve/``:

```
evolve/
├── domain/              # pure business concepts, no I/O, no SDK
│   ├── round.py             # Round, RoundKind, RoundResult, RoundAttempt
│   ├── improvement.py       # USItem, BacklogState, Backlog
│   ├── agent_invocation.py  # AgentRole, AgentSubtype, AgentResult
│   ├── review_verdict.py    # ReviewVerdict (APPROVED/CHANGES_REQUESTED/BLOCKED), Finding
│   ├── convergence.py       # ConvergenceVerdict, ConvergenceGate
│   ├── memory.py            # MemoryEntry, MemoryLog, CompactionDecision
│   └── spec_compliance.py   # SpecClaim, ClaimVerification
├── application/         # use cases, orchestration; depends only on domain
│   ├── run_round.py         # the "run one round" use case
│   ├── run_loop.py          # the "run N rounds" use case
│   ├── draft_us.py          # the "draft one US" use case
│   ├── review_round.py      # the "adversarial-review one round" use case
│   ├── analyze_and_fix.py   # the implement use case
│   ├── curate_memory.py     # the "curate memory.md" use case
│   ├── archive_spec.py      # the "archive SPEC.md sections" use case
│   ├── retry_policy.py      # debug-retry / circuit-breaker decisions
│   ├── convergence_check.py # the "decide CONVERGED" use case
│   ├── party_session.py     # multi-agent party mode
│   └── {dry_run,validate,sync_readme,diff,update}.py  # one-shot use cases
├── infrastructure/      # adapters; depends on domain, never the reverse
│   ├── claude_sdk/          # SDK client, prompt builder, retries, runtime constants
│   ├── git/                 # git CLI adapter
│   ├── filesystem/          # run-dir, state.json, conversation logs, frames
│   ├── hooks/               # external-hook execution
│   ├── costs/               # token tracking, pricing, budget
│   ├── diagnostics/         # subprocess-error files
│   └── reporting/           # evolution_report.md
└── interfaces/          # entry points; depend on application
    ├── cli/                 # argparse, config resolution, subcommand dispatch
    ├── tui/                 # Rich / plain / JSON TUIs
    └── watcher.py           # the evolve-watch supervisor
```

**Dependency rule — strictly inward.**

- ``domain`` imports nothing from evolve.
- ``application`` imports from ``domain`` only.
- ``infrastructure`` imports from ``domain`` (and may implement
  domain ports / interfaces declared there).  It MUST NOT be
  imported by ``domain`` or ``application`` directly — those
  layers depend on abstractions, and receive infrastructure
  implementations via dependency injection at the
  ``interfaces`` boundary.
- ``interfaces`` import from ``application`` and from
  ``infrastructure`` (for wiring), and may import ``domain``
  for type signatures.  Nothing imports from ``interfaces``.

**Bounded contexts.**  The high-level contexts that group
related domain types and use cases are:

1. **Orchestration** — Round, RoundKind, retry policy, watchdog,
   convergence (``application/run_round.py``,
   ``application/run_loop.py``, ``application/retry_policy.py``,
   ``application/convergence_check.py``).
2. **Authoring** — drafting and reviewing US items
   (``application/draft_us.py``, ``application/review_round.py``,
   ``application/analyze_and_fix.py``).
3. **Memory & SPEC lifecycle** — memory curation, SPEC archival
   (``application/curate_memory.py``,
   ``application/archive_spec.py``).
4. **Cost & budget** — token usage, pricing, budget enforcement
   (``infrastructure/costs/``).
5. **Operator interface** — CLI args, TUI rendering, hooks,
   watcher (``interfaces/cli/``, ``interfaces/tui/``,
   ``interfaces/watcher.py``, ``infrastructure/hooks/``).

Cross-context calls go through ``application`` use cases, never
direct module-to-module imports across contexts.

**Ubiquitous language.**  Every domain concept that appears in
``SPEC.md`` (Round, US, Improvement, Convergence, Memory,
Backlog, Subtype, Verdict, Carve-out, …) MUST map to a single
named type in ``evolve/domain/``.  No synonyms across modules.
No primitive obsession (``str`` standing in for an
``AgentSubtype`` literal, ``dict`` standing in for a
``ReviewVerdict``).  When the SPEC introduces a new term, the
implementing round introduces a matching domain type in the
same commit.

**Enforcement.**  A CI-enforced test (``tests/test_layering.py``)
parses the import graph of every ``*.py`` under ``evolve/``
using the ``ast`` module and fails the build on any
inward-violating edge.  Same mechanism that enforces § "Hard
rule: source files MUST NOT exceed 500 lines".  The
orchestrator's debug-retry diagnostic surfaces a
``LAYERING VIOLATION:`` header when a round leaves any
violating import behind, so the next attempt picks the fix as
its target.

**Migration carve-out.**  Restructuring proceeds incrementally,
one bounded context per round, with the legacy flat layout
(``evolve/agent.py``, ``evolve/orchestrator.py``,
``evolve/round_lifecycle.py``, …) and the DDD layout coexisting
during migration.  Shims at the legacy paths re-export the new
locations for backward compatibility — same playbook as the
legacy ``loop.py`` → ``evolve/`` package migration archived
above.  Shims are deleted once the migration of a context is
complete; until then they are explicitly whitelisted in the
import-graph linter.  The migration is **not** a single mega-PR:
each round picks ONE module from the flat layout, identifies
its layer + context, splits accordingly, updates imports,
delivers passing tests, then hands off to the next round.

**Self-correction loop.**  When the orchestrator detects a
layering violation (import-graph test fails), it emits a
``LAYERING VIOLATION:`` diagnostic with the offending
edge(s).  ``build_prompt`` recognises the prefix and surfaces a
``## CRITICAL — DDD layering violation`` section in the next
attempt's prompt.  Combined with the TDD self-correction loop
(§ "Test-Driven Development (TDD)" — ``TDD VIOLATION:``
diagnostic), evolve has two normative engineering disciplines
it can verify and roll back against round-by-round.

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

**Rationale (archived).** `.evolve/` follows the dotfile convention
(`.git/`, `.vscode/`) to avoid polluting third-party project roots.

→ Full rationale: [`SPEC/archive/029-dotevolve-directory-rationale.md`]

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

**Implementation wiring (archived).** Each prompt file (`draft.md`,
`system.md`, `review.md`) is scoped to its single persona. The
orchestrator's `_run_single_round_body` follows the pipeline
literally. Retry targets the failed call, not the whole round.

→ Full prompt scopes + orchestrator contract + retry semantics: [`SPEC/archive/028-multi-call-implementation-wiring.md`]

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

## README as a user-level summary (archived)

When `--spec` points at a separate file, README serves as a user-level
summary maintained by the operator. `evolve sync-readme` is the only
sanctioned way evolve writes to README. A stale-README advisory fires
at startup when the spec is >30 days newer.

→ Full policy + stale-README check: [`SPEC/archive/024-readme-separation-policy.md`]

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

**Design rationale and timeout behavior (archived).** The 20-second
default is a quality invariant forcing fast test suites. On timeout,
the orchestrator injects `"TIMEOUT after 20s"` into the agent's
prompt, triggering slowness-investigation mode.

→ Full rationale + behavior: [`SPEC/archive/022-timeout-design-rationale.md`]

**Single-source-of-truth: agents must NOT run the check command
themselves (archived).** The orchestrator is the sole actor that
runs pre-check and post-check. Agents reason from check output in
their prompt. One narrow escape hatch: a `timeout`-wrapped
single-file verification when the agent needs mid-turn feedback.

→ Full rationale + escape hatch + future enforcement: [`SPEC/archive/017-check-command-ownership.md`]

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

### `evolve sync-readme` (archived)

One-shot subcommand that refreshes `README.md` to reflect the current
spec. Default mode writes `README_proposal.md`; `--apply` commits
directly. Exit codes: 0 (written), 1 (already in sync), 2 (error).
Never runs during rounds.

→ Full spec + usage: [`SPEC/archive/025-sync-readme-subcommand.md`]

### `evolve diff` (archived)

One-shot subcommand showing spec-vs-implementation delta. Lighter than
`--validate`: `--effort low`, major-feature presence check, no test
run. Exit codes: 0 (compliant), 1 (gaps found), 2 (error).

→ Full spec + differences from --validate: [`SPEC/archive/026-diff-subcommand.md`]

### `evolve update` (archived)

One-shot subcommand that pulls the latest evolve commit from upstream.
Handles editable (`git merge --ff-only`) and non-editable (`pip install
--upgrade`) installs. Safety: refuses dirty trees, active sessions,
non-fast-forward. Exit codes: 0 (updated), 1 (blocked), 2 (error).

→ Full spec + safety rails + use cases: [`SPEC/archive/019-evolve-update-subcommand.md`]

### Project-specific prompts

Projects can override the default system prompt by creating
`prompts/evolve-system.md` in their project directory. Evolve will use it
instead of the default.

---

## Cost and token tracking (archived)

Every round produces `usage_round_N.json` with token counts. The
`estimate_cost` function in `costs.py` converts tokens to USD via a
built-in rate table (custom rates configurable in `evolve.toml`).
Aggregated in `state.json` `usage` field and `evolution_report.md`.
TUI displays per-round and session-total cost.

→ Full schema + rate table + TUI display: [`SPEC/archive/015-cost-tracking-observability.md`]

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

### Dedicated memory curation — Mira (archived)

A dedicated curator agent (Mira, `agents/curator.md`) triages
`memory.md` between rounds when it exceeds 300 lines or every 10
rounds. Four passes: duplicate detection, rediscoverability audit,
historical archival, section hygiene. Verdicts: CURATED, SKIPPED,
ABORTED (>80% shrink), SDK FAIL.

→ Full protocol + safeguards: [`SPEC/archive/014-memory-curation-protocol.md`]

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

**Carve-outs: scope creep and backlog drained (archived).** The
orchestrator detects two special cases in zero-progress analysis:
(1) rebuild + implement mixed in one round (`SCOPE CREEP:` diagnostic),
(2) all items checked off but CONVERGED not written (`BACKLOG DRAINED:`
diagnostic). Both trigger targeted retry prompts instead of generic
no-progress retries.

→ Full protocols: [`SPEC/archive/020-zero-progress-diagnostic-carveouts.md`]

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

### Authoritative termination signal from the SDK (archived)

The SDK's `ResultMessage.subtype` (`success`, `error_max_turns`,
`error_during_execution`) is the authoritative source for round
termination cause. All callsites capture and log it; the orchestrator
branches retry logic on the precise cause.

→ Full contract + routing rules: [`SPEC/archive/027-sdk-termination-signal.md`]

### Complete LLM stream capture (archived)

Every SDK invocation persists its complete stream to a per-agent,
per-round file under the session directory. Files are flushed in
real-time (no buffering). Format is human-readable Markdown.

→ Full capture spec + file naming + format rules: [`SPEC/archive/008-stream-capture-spec.md`]

### Agent-side self-monitoring (archived)

The agent inspects the last two rounds' conversation logs at round
start, detects stuck loops (same target, no Edit/Write calls), and
splits or blocks the target. First line of defense before orchestrator
zero-progress retry.

→ Full protocol: [`SPEC/archive/021-agent-self-monitoring.md`]

### Retry continuity and Phase 1 escape hatch (archived)

Debug retries reuse prior attempt's work via per-attempt log files
and enriched diagnostic prompts. When pre-existing test failures
block progress on the final retry attempt, the agent may bypass
Phase 1 and proceed with the target (logging the bypass).

→ Full protocols: [`SPEC/archive/009-retry-escape-hatches.md`]

### Structural change self-detection (archived)

When evolve is self-evolving, the agent detects structural changes
(renames, entry-point moves, `__init__.py` edits) and writes a
`RESTART_REQUIRED` marker. The orchestrator exits with code 3.
Skipped when driving third-party projects.

→ Full protocol: [`SPEC/archive/010-structural-change-protocol.md`]

### Adversarial round review — Phase 3.6 (archived)

After each implement round, Zara (reviewer persona) runs a four-pass
adversarial audit. Verdicts: APPROVED (proceed), CHANGES REQUESTED
(auto-retry), BLOCKED (auto-retry). Draft rounds skip review.
Minimum 3 findings per review. Auto-fix invariant: no manual
operator arbitration.

→ Full protocol + verdict routing: [`SPEC/archive/011-adversarial-review-protocol.md`]

### Prior round audit, heartbeat, and circuit breakers (archived)

Every round ≥ 2 audits the previous round's artifacts for anomalies.
A 30s heartbeat thread prevents watchdog false kills. The circuit
breaker exits with code 4 after 3 identical failure signatures.

→ Full mechanisms: [`SPEC/archive/012-monitoring-mechanisms.md`]

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

## Real-time state file (archived)

Each session maintains `state.json` updated after every round with
version, round, status, improvements, backlog, usage, and last_check
fields. Schema versioned (currently v2). Queryable by external tools.

→ Full schema + versioning: [`SPEC/archive/015-cost-tracking-observability.md`]

---

## Evolution report (archived)

`evolution_report.md` is written after each session with timeline,
cost summary, and statistics. Generated from conversation logs,
commits, check results, and usage files.

→ Full format: [`SPEC/archive/015-cost-tracking-observability.md`]

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

## Prompt caching (archived)

The SDK's native caching fires on stable leading-prefix system
prompts. Evolve passes `system_prompt` as a single string, keeps
static content first, per-round content last. No explicit
`cache_control` markers needed.

→ Full contract + wrong patterns + verification criteria: [`SPEC/archive/013-prompt-caching-contract.md`]

---

## SPEC archival (Sid) (archived)

A dedicated archivist agent (Sid, `agents/archivist.md`) extracts
stable/historical sections from SPEC.md to `SPEC/archive/NNN-<slug>.md`,
leaving summary stubs. Runs between rounds (not during them), using
centralized `MODEL` + `effort=low`.

**Trigger conditions:** SPEC.md > 2000 lines, OR every 20 rounds,
OR explicit operator request.

**Read discipline.** `SPEC/archive/*.md` are NOT current contract.
Agents MUST NOT read them unless: (1) the current US references an
archived concept, (2) the stub is insufficient, (3) non-archive
sources already read. Archive reads logged to `memory.md`; >1 per
round without justification = Zara finding.

→ Full protocol + acceptance criteria + migration bootstrap: [`SPEC/archive/016-spec-archival-implementation.md`]

---

## Exit codes

`evolve start` returns meaningful exit codes for CI/CD integration:

| Exit Code | Meaning |
|-----------|---------|
| 0 | Converged — project fully matches spec |
| 1 | Max rounds reached or budget reached — improvements remain |
| 2 | Error — agent failure, missing deps, or invalid args |
| 3 | Structural change — restart required (see § "Structural change self-detection" and § "evolve-watch auto-restart wrapper" below) |
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

## evolve-watch auto-restart wrapper (archived)

External supervisor that wraps `evolve start` and respawns on every
non-zero exit until convergence. Two stop conditions: exit 0 (converged)
or operator signal (SIGINT/SIGTERM). No restart cap. stderr-only logging.
Entry point: `evolve/watcher.py`, ~120 lines.

→ Full spec: [`SPEC/archive/018-evolve-watch-wrapper.md`]

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

### Test-Driven Development (TDD)

Evolve follows TDD as a normative engineering discipline.  This is
not a recommendation — these rules are part of the spec evolve
self-corrects against.  An implement round (Amelia) that violates
any of them is treated as a failed round and rolled back on retry.

**Tests-first per round.**  Every round that introduces or
modifies behaviour MUST land at least one test that exercises the
new / changed behaviour, in the same commit as the implementation.
"Behaviour" is observable: a function output, a CLI exit code, a
file written, a TUI line emitted, a probe message, an exception
class.  A round whose diff touches non-test code under
``evolve/`` but adds zero lines to ``tests/`` is rejected by the
post-round US-format check (advisory in the current round, hard
gate on the retry).  Pure renames / file moves with no behaviour
change are exempt — those are structural commits (§ "Structural
change self-detection") and ship without new tests.

**Red → green → refactor.**  The implement agent's prompt
(``prompts/system.md`` Phase 3) instructs Amelia to write the
failing test FIRST, run the check command to confirm it fails the
expected way, then write the production code that turns the test
green.  Refactoring (3rd step) only touches tested code.  This
order is observable in the conversation log: the
``Bash`` / ``pytest`` invocation that runs RED tests SHOULD appear
before the corresponding ``Edit`` on the production file.

**Pre-check + post-check are mandatory gates.**  Every round runs
``check_cmd`` (``pytest`` by default) before AND after the agent
turn:

- **Pre-check FAILED** routes to implement (§ "Routing invariant:
  broken pre-check always routes to implement"), Phase 1 fixes the
  break before any new work.  Drafting on a broken test suite is
  forbidden.
- **Post-check FAILED** flips the round's verdict: even if the
  US's nominal AC's are met, the round is treated as a regression
  and Zara's adversarial review surfaces a HIGH finding tagged
  ``[regression-risk]``.  Auto-retry kicks in (§ "Verdict →
  orchestrator action").

**Mock the SDK, never the unit under test.**  Tests mock the
Claude Agent SDK boundary (see § "Hard rule: tests MUST NOT call
the real Claude SDK") because hitting it makes the suite slow,
flaky, and money-burning.  But tests MUST NOT mock the function
they are nominally testing — over-mocking produces tautological
green tests that pass even when the production code is gutted.
The rule of thumb: mock external resources (SDK, network, time,
subprocess), exercise the real evolve function.

**Coverage as a forcing function, not a target.**  The 95%
coverage target (§ "Test coverage target" below) is a
**ratchet**: coverage is allowed to stay at 95% indefinitely, but
a round that drops coverage below 95% is rejected.  Lines that
are genuinely unreachable (e.g. ``if TYPE_CHECKING:`` blocks)
are exempted via ``[tool.coverage.report] exclude_lines`` in
``pyproject.toml`` — adding a line to that exclude list is itself
a tracked change that requires a justification in the commit
message.

**Test naming and arrangement.**  Tests live under ``tests/`` in
files named ``test_<module>.py``, matching the production module
they cover.  Within a file, tests are grouped by behaviour, named
``test_<verb>_<expected_outcome>`` (e.g.
``test_routing_pre_check_failed_routes_to_implement``), and
follow the Arrange / Act / Assert structure with comments where
the boundary isn't obvious.  Tests that need the same fixture
share it via ``conftest.py``; one-off setups stay inline.

**TDD self-correction loop.**  The orchestrator emits a
``TDD VIOLATION:`` diagnostic when:

- A round commits production code under ``evolve/`` with zero
  ``tests/`` additions (and is not a structural commit).
- A round drops coverage below the 95% ratchet without a
  matching ``[tool.coverage.report] exclude_lines`` entry.
- A round's conversation log shows ``Edit`` on production code
  without a prior ``Bash`` / ``pytest`` call that ran the RED
  test (the "tests-first" check, advisory: hard gate on retry).

The diagnostic is fed back into the next attempt's prompt under
the ``## CRITICAL — TDD violation`` header, which instructs the
agent to back the violation out and redo the round with the test
written first.

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

### Hard rule: tests MUST NOT call the real Claude SDK (archived)

Every test touching SDK code paths MUST mock the SDK boundary before
invocation. Three approved patterns (mock `run_claude_agent`, mock
`ClaudeSDKClient`/`query`, patch `asyncio.run`); three forbidden
patterns. Leak detection: any test >500ms in `pytest --durations=10`.

→ Full patterns + forbidden patterns + leak detection: [`SPEC/archive/023-sdk-mock-testing-rule.md`]
