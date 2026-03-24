# evolve

Self-improving evolution loop for any project, powered by Claude.

Point it at any git repo with a README → it reads the README as the specification,
iteratively fixes bugs and implements improvements, one at a time, until the project
fully converges to its spec.

## Usage

```bash
# Initialize a config file for your project
python evolve.py init <project-dir>

# Evolve a project (README = spec)
python evolve.py start <project-dir> [--rounds 10] [--check "pytest"] [--timeout 300] [--model claude-opus-4-6] [--yolo] [--json]

# Resume an interrupted session
python evolve.py start <project-dir> --resume

# Autonomous forever mode (runs on a separate branch)
python evolve.py start <project-dir> --forever [--check "pytest"]

# Check evolution status
python evolve.py status <project-dir>

# Clean up old session directories
python evolve.py clean <project-dir> [--keep 5]
```

## Examples

```bash
# Evolve a Python project, verify with pytest
python evolve.py start ~/projects/my-tool --check "pytest" --rounds 20

# Evolve a Node project, verify with npm test
python evolve.py start ~/projects/my-app --check "npm test"

# Evolve a Rust project
python evolve.py start ~/projects/my-cli --check "cargo test"

# Evolve without a check command (opus runs commands manually)
python evolve.py start ~/projects/my-lib

# Allow installing new packages
python evolve.py start ~/projects/my-tool --check "pytest" --yolo

# Use a different model
python evolve.py start ~/projects/my-tool --check "pytest" --model claude-sonnet-4-20250514

# Resume after interruption (continues from last completed round)
python evolve.py start ~/projects/my-tool --check "pytest" --resume

# Autonomous forever mode — evolves indefinitely on a separate branch
python evolve.py start ~/projects/my-tool --check "pytest" --forever

# JSON output for CI/CD pipelines
python evolve.py start ~/projects/my-tool --check "pytest" --json

# Initialize a config file with sensible defaults
python evolve.py init ~/projects/my-tool

# Clean up old sessions, keeping the 5 most recent
python evolve.py clean ~/projects/my-tool --keep 5
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
python evolve.py init ~/projects/my-tool
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
python evolve.py start ~/projects/my-tool --check "pytest" --json
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

Each `evolve start` creates a timestamped session. Each round runs as a **separate subprocess**
so code changes are picked up immediately.

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
│   │   ├── evolution_report.md        # post-session summary with timeline
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
12. Round subprocess stdout/stderr captured for failure diagnostics
13. Next round starts as fresh subprocess (reloaded code)

--- after convergence ---

14. Party mode: all agents brainstorm next evolution
15. Agents produce:
    - party_report.md — full discussion log with each agent's reasoning
    - README_proposal.md — proposed updated README
16. Operator reviews both files
17. If approved: replace README.md → new evolution loop
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

### The --check flag

The `--check` flag specifies how to verify the project works. Any shell command:

```bash
--check "pytest"                    # Python
--check "npm test"                  # Node
--check "cargo test"                # Rust
--check "go test ./..."             # Go
--check "make test && make lint"    # Multiple checks
```

If omitted, opus runs commands manually to verify. With `--check`, the orchestrator
runs it automatically before and after each round for objective verification.

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
python evolve.py start ~/projects/my-tool --check "pytest"
```

### The --resume flag

Resumes the most recent interrupted session instead of creating a new one. Detects the
last completed round from existing conversation logs and continues from the next round.

```bash
# Session interrupted at round 5 — resume from round 6
python evolve.py start ~/projects/my-tool --check "pytest" --resume
```

If no previous session exists, `--resume` starts a fresh session (same as without the flag).

### The --forever flag

Autonomous evolution mode. Runs indefinitely on a **separate git branch** until the
operator stops it (Ctrl+C or kill).

```bash
python evolve.py start ~/projects/my-tool --check "pytest" --forever
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
python evolve.py start ~/projects/my-tool --check "pytest" --forever --yolo
```

### The --json flag

Switches output from the interactive TUI to structured JSON events on stdout.
Each line is a valid JSON object. Designed for CI/CD pipelines, monitoring dashboards,
and programmatic consumption.

```bash
python evolve.py start ~/projects/my-tool --check "pytest" --json
```

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
python evolve.py clean ~/projects/my-tool --keep 5

# Keep only the latest session
python evolve.py clean ~/projects/my-tool --keep 1
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
python evolve.py start . --check "pytest" --rounds 20
if [ $? -eq 0 ]; then echo "Converged!"; fi
```

```bash
# Full CI/CD example with JSON output
python evolve.py start . --check "pytest" --rounds 20 --json > evolve-output.jsonl
EXIT_CODE=$?
if [ $EXIT_CODE -eq 0 ]; then
  echo "Converged! Creating PR..."
  # Parse evolve-output.jsonl for PR description
fi
```

## Development

Evolve has its own test suite. Run it with pytest:

```bash
# Run all tests
pytest tests/

# Run with coverage
pytest tests/ --cov=. --cov-report=term-missing
```

### Test structure

```
tests/
├── test_loop.py       # _is_needs_package, counters, _get_current_improvement
├── test_agent.py      # build_prompt, error helpers, retry logic
├── test_tui.py        # factory function, TUI Protocol parity, JsonTUI
└── test_evolve.py     # CLI arg parsing, _show_status
```

Tests cover all pure utility functions without requiring the Claude SDK. Integration
tests that need the SDK use mocked responses.

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
- **`--dry-run` mode** — preview what the agent would do without modifying files
- **Parallel analysis** — run read-only analysis in parallel before sequential implementation
- **Watch mode** — re-evolve automatically when README changes
- **Plugin system** — custom check commands, reporters, and post-convergence hooks
