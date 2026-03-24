# evolve

Self-improving evolution loop for any project, powered by Claude.

Point it at any git repo with a README → it reads the README as the specification,
iteratively fixes bugs and implements improvements, one at a time, until the project
fully converges to its spec.

## Usage

```bash
# Evolve a project (README = spec)
python evolve.py start <project-dir> [--rounds 10] [--check "pytest"] [--timeout 300] [--model claude-opus-4-6] [--yolo]

# Resume an interrupted session
python evolve.py start <project-dir> --resume

# Check evolution status
python evolve.py status <project-dir>
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

## How it works

Each `evolve start` creates a timestamped session. Each round runs as a **separate subprocess**
so code changes are picked up immediately.

```
<project>/
├── README.md                          # THE SPEC — evolve converges to this
├── runs/
│   ├── improvements.md                # shared — one improvement added per round
│   ├── memory.md                      # shared — cumulative error log, compacted each round
│   ├── 20260324_160000/               # session 1
│   │   ├── conversation_loop_1.md     # full opus conversation log
│   │   ├── conversation_loop_2.md
│   │   ├── check_round_1.txt          # post-fix check results
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
├── test_tui.py        # factory function, TUI Protocol parity
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

- **Configuration file** (`.evolverc` or `evolve.toml`) — project-level defaults for all CLI flags
- **JSON output mode** — machine-readable reports for CI/CD pipelines
- **Log rotation** — automatic archival/pruning of old session directories
- **Enhanced status** — per-round summaries showing what each round accomplished
- **Multi-repo evolution** — evolve multiple related projects in coordination
