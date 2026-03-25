<p align="center">
  <img src="assets/imgs/logo_evolve.jpg" alt="evolve logo" width="400">
</p>

# evolve

Self-improving evolution loop for any project, powered by Claude.

Point it at any git repo with a README → it reads the README as the specification,
iteratively fixes bugs and implements improvements, one at a time, until the project
fully converges to its spec.

## Installation

Install evolve as a Python package:

```bash
pip install .
```

Or with the optional rich TUI:

```bash
pip install ".[rich]"
```

For development:

```bash
pip install -e ".[rich,dev]"
```

After installation, the `evolve` command is available globally:

```bash
evolve start ~/projects/my-tool --check "pytest"
```

You can also run directly without installing:

```bash
python evolve.py start ~/projects/my-tool --check "pytest"
```

## Usage

```bash
# Initialize a config file for your project
evolve init <project-dir>

# Evolve a project (README = spec)
evolve start <project-dir> [--rounds 10] [--check "pytest"] [--timeout 300] [--model claude-opus-4-6] [--yolo] [--json]

# Preview what the agent would do (read-only, no file changes)
evolve start <project-dir> --dry-run [--check "pytest"]

# Resume an interrupted session
evolve start <project-dir> --resume

# Autonomous forever mode (runs on a separate branch)
evolve start <project-dir> --forever [--check "pytest"]

# Check evolution status
evolve status <project-dir>

# Show evolution timeline across all sessions
evolve history <project-dir>

# Clean up old session directories
evolve clean <project-dir> [--keep 5]
```

## Examples

```bash
# Evolve a Python project, verify with pytest
evolve start ~/projects/my-tool --check "pytest" --rounds 20

# Evolve a Node project, verify with npm test
evolve start ~/projects/my-app --check "npm test"

# Evolve a Rust project
evolve start ~/projects/my-cli --check "cargo test"

# Evolve without a check command (opus runs commands manually)
evolve start ~/projects/my-lib

# Allow installing new packages
evolve start ~/projects/my-tool --check "pytest" --yolo

# Use a different model
evolve start ~/projects/my-tool --check "pytest" --model claude-sonnet-4-20250514

# Dry run — see what the agent would change without modifying files
evolve start ~/projects/my-tool --check "pytest" --dry-run

# Resume after interruption (continues from last completed round)
evolve start ~/projects/my-tool --check "pytest" --resume

# Autonomous forever mode — evolves indefinitely on a separate branch
evolve start ~/projects/my-tool --check "pytest" --forever

# JSON output for CI/CD pipelines
evolve start ~/projects/my-tool --check "pytest" --json

# Initialize a config file with sensible defaults
evolve init ~/projects/my-tool

# Show evolution history across all sessions
evolve history ~/projects/my-tool

# Clean up old sessions, keeping the 5 most recent
evolve clean ~/projects/my-tool --keep 5
```

## Configuration

Evolve supports project-level configuration via `evolve.toml` or `pyproject.toml`.
This eliminates the need to repeat CLI flags on every run.

### Configuration file

Create an `evolve.toml` in your project root:

```toml
# evolve.toml
check = "pytest"
rounds = 20
timeout = 300
model = "claude-opus-4-6"
yolo = false
```

Or add an `[tool.evolve]` section to your existing `pyproject.toml`:

```toml
[tool.evolve]
check = "pytest"
rounds = 20
timeout = 300
```

### Resolution order

Settings are resolved in this order (first wins):

1. CLI flags (`--check "pytest"`)
2. Environment variables (`EVOLVE_MODEL`)
3. `evolve.toml` in project root
4. `pyproject.toml [tool.evolve]` section
5. Built-in defaults

### `evolve init`

Scaffold a config file with sensible defaults:

```bash
evolve init ~/projects/my-tool
# Creates ~/projects/my-tool/evolve.toml with default settings
```

## TUI

Evolve features a modern terminal UI powered by `rich`:

```
╭──────────────────── evolve ─────────────────────╮
│ EVOLUTION ROUND 3/10                            │
│ TARGET: [functional] Add input validation       │
│ PROGRESS: ██████░░░░ 5/9 improvements done      │
╰─────────────────────────────────────────────────╯

  [check] pytest ─────────────────────────────────
  ✓ 42 passed · 0 failed · 1.2s

  [agent] Claude opus working...
  [opus] Read → src/parser.py
  [opus] Edit → src/parser.py (edit)
  [opus] Bash → pytest tests/test_parser.py
  [opus] Edit → runs/improvements.md (edit)

  [verify] pytest ────────────────────────────────
  ✓ 43 passed · 0 failed · 1.3s

  [git] feat(parser): add input validation → pushed

  Progress: 6 done, 3 remaining
```

Features:
- Colored panels for round headers with progress bars
- Real-time agent activity feed (tools used, files edited)
- Check command results with pass/fail indicators
- Git commit + push status
- Graceful fallback to plain text when `rich` is not installed
- TUI interface enforced via Protocol — RichTUI and PlainTUI both implement the same `TUIProtocol`, guaranteeing method parity at type-check time

### JSON output mode

For CI/CD integration, use `--json` to emit structured JSON events to stdout
instead of the interactive TUI:

```bash
evolve start ~/projects/my-tool --check "pytest" --json
```

Each line is a JSON object with a `type`, `timestamp`, and event-specific fields:

```json
{"type": "round_start", "timestamp": "2026-03-24T16:00:00Z", "round": 1, "max_rounds": 10}
{"type": "check_result", "timestamp": "2026-03-24T16:00:05Z", "label": "check", "cmd": "pytest", "passed": true}
{"type": "agent_tool", "timestamp": "2026-03-24T16:01:00Z", "tool": "Edit", "input": "src/parser.py"}
{"type": "improvement_completed", "timestamp": "2026-03-24T16:02:00Z", "description": "Add input validation"}
{"type": "converged", "timestamp": "2026-03-24T16:05:00Z", "round": 3, "reason": "All README claims verified"}
```

The `JsonTUI` class implements the same `TUIProtocol` as `RichTUI` and `PlainTUI`,
ensuring all output methods are available in JSON mode with zero changes to business logic.

## How it works

Each `evolve start` creates a timestamped session. Each round runs as a **monitored
subprocess** so code changes are picked up immediately and stalled processes are
automatically detected and killed.

```
<project>/
├── README.md                          # THE SPEC — evolve converges to this
├── evolve.toml                        # (optional) project-level config
├── runs/
│   ├── improvements.md                # shared — one improvement added per round
│   ├── memory.md                      # shared — cumulative error log, compacted each round
│   ├── 20260324_160000/               # session 1
│   │   ├── conversation_loop_1.md     # full opus conversation log
│   │   ├── conversation_loop_2.md
│   │   ├── check_round_1.txt          # post-fix check results
│   │   ├── subprocess_error_round_3.txt  # diagnostic from crashed/stalled round
│   │   ├── evolution_report.md        # post-session summary with timeline
│   │   ├── dry_run_report.md          # (dry-run only) read-only analysis
│   │   ├── COMMIT_MSG                 # (transient) commit message from opus
│   │   └── CONVERGED                  # written by opus when done
│   └── 20260324_170000/               # session 2
│       ├── ...
│       ├── party_report.md            # multi-agent discussion log
│       └── README_proposal.md         # proposed next README
└── prompts/
    └── evolve-system.md               # (optional) project-specific prompt override
```

**Each round — one improvement at a time:**

```
1. Run check command (pytest, npm test, cargo test, etc.) → results
2. Opus receives: README + improvements.md + memory.md + check results
   + crash diagnostic from previous round (if any)
3. Opus reads run directory and memory.md for context
4. Phase 1 — ERRORS: fix any failures from check command (mandatory)
5. Phase 2 — IMPROVEMENT: implement one item, verify, check it off
   Then add exactly one new improvement (most impactful next issue)
6. Phase 3 — CONVERGENCE: only when README 100% implemented + best practices
7. Opus logs errors to memory.md, compacts it
8. Opus verifies every file it wrote by reading it back
9. Opus writes COMMIT_MSG with conventional commit message
10. Git commit + push
11. Orchestrator re-runs check → saves check_round_N.txt
12. Next round starts as fresh subprocess (reloaded code)

--- watchdog & debug retry ---

If a subprocess crashes, stalls (no output for 120s), or makes no progress,
the orchestrator:
  a. Saves a diagnostic file (subprocess_error_round_N.txt)
  b. Retries the round (up to 2 debug retries per round)
  c. The retry receives the crash diagnostic in its prompt
  d. In --forever mode, exhausted retries skip to the next round

--- after convergence ---

13. Party mode: all agents brainstorm next evolution
14. Agents produce:
    - party_report.md — full discussion log with each agent's reasoning
    - README_proposal.md — proposed updated README
15. Operator reviews both files
16. If approved: replace README.md → new evolution loop
```

### Evolution report

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

## Summary
- 6 improvements completed
- 2 bugs fixed
- 12 files modified
```

The report is generated by parsing conversation logs, commit messages, and check
results from the session directory. It serves both human review (post-run summary)
and CI/CD integration (PR description content).

### Subprocess monitoring & debug retries

Every round runs as a monitored subprocess. The orchestrator streams stdout in
real-time via a reader thread and enforces a **watchdog timer** — if the
subprocess produces no output for 120 seconds, it is considered stalled and
killed.

When a round fails (crash, stall, or zero progress), the orchestrator enters a
**debug retry loop**:

1. Writes `subprocess_error_round_N.txt` with full diagnostic (exit code,
   last 3000 chars of output, reason for failure)
2. Retries the round — the agent receives the diagnostic in its prompt under a
   "CRITICAL — Previous round CRASHED" header and fixes the root cause
3. Up to 2 debug retries per round (3 total attempts)
4. In `--forever` mode, exhausted retries skip to the next round instead of
   exiting

The agent is aware of the watchdog via the system prompt and is instructed to:
- Print progress lines as it works (silence = kill)
- Add logging/probes in delivered code for runtime observability
- Print a status line before long-running commands

### The --check flag

The `--check` flag specifies how to verify the project works. Any shell command:

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
instead. In both cases, the check is run automatically before and after each round
for objective verification.

### The --timeout flag

Sets the maximum time (in seconds) the check command is allowed to run before being
killed. Defaults to 300 seconds (5 minutes). Increase for slow test suites:

```bash
--timeout 600    # 10 minutes
```

### The --model flag

Sets the Claude model to use for evolution. Defaults to `claude-opus-4-6`.
Can also be set via the `EVOLVE_MODEL` environment variable (CLI flag takes precedence).

```bash
--model claude-opus-4-6             # Default — most capable
--model claude-sonnet-4-20250514    # Faster, lower cost
```

```bash
# Or via environment variable
export EVOLVE_MODEL=claude-sonnet-4-20250514
evolve start ~/projects/my-tool --check "pytest"
```

### The --dry-run flag

Runs the agent in **read-only analysis mode** — it examines the project and produces
a report of what it *would* change, without actually modifying any files.

```bash
evolve start ~/projects/my-tool --check "pytest" --dry-run
```

**How it works:**

1. Runs the check command (if provided) to see current state
2. Launches the agent with write-related tools disabled (no Edit, Write, or Bash)
3. Agent analyzes the README, code, and check results using only Read, Grep, and Glob
4. Produces `runs/<session>/dry_run_report.md` with:
   - Identified gaps between README spec and implementation
   - Proposed improvements (what would be added to `improvements.md`)
   - Estimated number of rounds to convergence
5. No files are modified, no git commits are created

Useful for:
- Previewing evolution scope before committing to a full run
- Auditing what the agent considers "missing" from the spec
- Estimating effort for a new project
- CI/CD gates that check spec compliance without modifying code

### The --resume flag

Resumes the most recent interrupted session instead of creating a new one. Detects the
last completed round from existing conversation logs and continues from the next round.

```bash
# Session interrupted at round 5 — resume from round 6
evolve start ~/projects/my-tool --check "pytest" --resume
```

If no previous session exists, `--resume` starts a fresh session (same as without the flag).

### The --forever flag

Autonomous evolution mode. Runs indefinitely on a **separate git branch** until the
operator stops it (Ctrl+C or kill).

```bash
evolve start ~/projects/my-tool --check "pytest" --forever
```

**How it works:**

1. Creates a new branch `evolve/<timestamp>` from the current branch
2. Runs the normal evolution loop (Phase 1-3) until convergence
3. After convergence, launches party mode — agents brainstorm the next evolution
4. **Instead of waiting for operator approval**, automatically merges the
   `README_proposal.md` into `README.md`
5. Resets `improvements.md` and starts a new evolution loop against the updated README
6. Repeats until stopped by the operator

```
main ──────────────────────────────────────────────────
       \
        evolve/20260324_220000 ─── round 1 ─── round 2 ─── CONVERGED
                                                                │
                                                          party mode
                                                                │
                                                     README_proposal → README.md
                                                                │
                                                          round 1 ─── round 2 ─── CONVERGED
                                                                                       │
                                                                                 party mode
                                                                                       │
                                                                                     ...
```

All work happens on the `evolve/*` branch — `main` is never touched. The operator can:
- Watch progress in real-time via the TUI
- Review the branch at any time (`git log evolve/<timestamp>`)
- Merge when satisfied (`git merge evolve/<timestamp>`)
- Or discard the branch entirely (`git branch -D evolve/<timestamp>`)

Combines well with `--yolo` for fully autonomous evolution:

```bash
# Full autonomy — installs packages, updates README, loops forever
evolve start ~/projects/my-tool --check "pytest" --forever --yolo
```

### The --json flag

Switches output from the interactive TUI to structured JSON events on stdout.
Each line is a valid JSON object. Designed for CI/CD pipelines, monitoring dashboards,
and programmatic consumption.

```bash
evolve start ~/projects/my-tool --check "pytest" --json
```

### `evolve history`

Show the evolution timeline across all sessions for a project:

```bash
evolve history ~/projects/my-tool
```

Output:

```
  Evolution History: ~/projects/my-tool
  ──────────────────────────────────────

  Session              Rounds   Status      Improvements
  20260324_160000      8/20     CONVERGED   6 done, 0 remaining
  20260324_170000      3/10     CONVERGED   3 done, 0 remaining
  20260325_072223      1/10     CONVERGED   28 done, 0 remaining

  Total: 3 sessions, 12 rounds, 37 improvements
```

Shows each session's round count, convergence status, and improvement statistics.
Parses `evolution_report.md` and `CONVERGED` markers from each session directory.

### improvements.md — the convergence tracker

One improvement added per round:
- A checkbox (`[ ]` pending, `[x]` done)
- A type tag: `[functional]` or `[performance]`
- Optional `[needs-package]` flag — skipped unless `--yolo`

### memory.md — cumulative error log

Each agent reads it to avoid repeating mistakes. Each agent compacts it at end of turn.

### Convergence

Opus decides convergence. It must verify line-by-line that every README claim is implemented
and functional. When certain, it writes `CONVERGED` with justification.

### Phase 4 — Party mode (post-convergence)

After convergence, all agents from `agents/` brainstorm the next evolution:

**Inputs:**
- Agent personas from `agents/*.md`
- Workflow from `workflows/party-mode/`
- Current README, improvements history, memory

**Outputs:**
- `party_report.md` — full discussion explaining each agent's reasoning
- `README_proposal.md` — complete updated README for the next cycle

The operator reviews both files and decides whether to accept the proposal.

### Git convention

Every commit follows conventional commits:

```
<type>(<scope>): <short description>

<body>
```

Types: `fix`, `feat`, `refactor`, `perf`, `docs`, `test`, `chore`

### --yolo mode

By default, improvements requiring new packages are blocked. Use `--yolo` to allow.

### Project-specific prompts

Projects can override the default system prompt by creating `prompts/evolve-system.md`
in their project directory. Evolve will use it instead of the default.

### `evolve clean`

Remove old session directories to free disk space:

```bash
# Keep the 5 most recent sessions, delete the rest
evolve clean ~/projects/my-tool --keep 5

# Keep only the latest session
evolve clean ~/projects/my-tool --keep 1
```

Sessions are sorted by timestamp. The `--keep` flag specifies how many recent
sessions to retain (default: 5). Committed code changes are preserved in git
history regardless of session cleanup.

### Exit codes

`evolve start` returns meaningful exit codes for CI/CD integration:

| Exit Code | Meaning |
|-----------|---------|
| 0 | Converged — project fully matches README spec |
| 1 | Max rounds reached — improvements remain |
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

## Architecture

Evolve is organized into four modules with clear responsibilities:

| Module | Responsibility |
|--------|---------------|
| `evolve.py` | CLI entry point, argument parsing, config resolution |
| `loop.py` | Evolution orchestrator — monitored subprocesses, watchdog, debug retries, party mode |
| `agent.py` | Claude SDK interface — prompt building, agent execution, retry logic |
| `tui.py` | Terminal UI — `TUIProtocol` with Rich, Plain, and JSON implementations |

### Config resolution

Settings are resolved via a data-driven loop over field definitions, with each
field checking CLI → environment variable → config file → default in order.
This eliminates per-field duplication and makes adding new settings trivial.

### Retry and error handling

**Agent-level retries** — `analyze_and_fix` and `_run_party_mode` share the same
retry helpers for:
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

## Development

Evolve has its own test suite. Run it with pytest:

```bash
# Run all tests
pytest tests/

# Run with coverage
pytest tests/ --cov=. --cov-report=term-missing
```

### Test coverage target

The project targets **80% test coverage** minimum. Current coverage should be
verified before merging any changes:

```bash
pytest tests/ --cov=. --cov-report=term-missing --cov-fail-under=80
```

### Test structure

```
tests/
├── test_loop.py            # _is_needs_package, counters, _get_current_improvement, _detect_last_round, _count_blocked
├── test_loop_extended.py   # _git_commit, _run_monitored_subprocess, _save_subprocess_diagnostic, resume, reports
├── test_agent.py           # build_prompt, error helpers, retry logic
├── test_tui.py             # factory function, TUI Protocol parity, JsonTUI
└── test_evolve.py          # CLI arg parsing, _show_status, config resolution, init, clean, history
```

Tests cover all pure utility functions without requiring the Claude SDK. Integration
tests that need the SDK use mocked responses. Error-path tests verify graceful
degradation under failure conditions (corrupted files, timeouts, missing dependencies).

## Requirements

- Python 3.10+
- `claude-agent-sdk`: `pip install claude-agent-sdk`
- `rich` (optional): `pip install rich` — for the modern TUI (fallback to plain text without it)
- Git repository
- Claude Code CLI installed and authenticated

### Model compatibility

Evolve works with any Claude model supported by the Agent SDK. Recommended:

| Model | Best for |
|-------|----------|
| `claude-opus-4-6` (default) | Maximum capability, complex projects |
| `claude-sonnet-4-20250514` | Faster iterations, simpler projects |

## Future directions

These are under consideration for future evolution cycles:

- **Multi-repo evolution** — evolve multiple related projects in coordination
- **Watch mode** — re-evolve automatically when README changes
- **Parallel analysis** — run read-only analysis in parallel before sequential implementation
- **Plugin system** — custom check commands, reporters, and post-convergence hooks

<!-- checked-by-anatoly -->
[![Checked by Anatoly](https://img.shields.io/badge/checked%20by-Anatoly-blue)](https://github.com/r-via/anatoly)
<!-- /checked-by-anatoly -->
