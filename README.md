<p align="center">
  <img src="assets/imgs/logo_evolve.jpg" alt="evolve logo" width="400">
</p>

# evolve

Self-improving evolution loop for any project, powered by Claude.

Point it at any git repo with a spec file → it reads the spec (by default
`README.md`, or any file via `--spec`), iteratively fixes bugs and implements
improvements one at a time, and stops when the project fully matches its spec.

> **Looking for internals?** This README is the user-facing guide — install,
> quickstart, examples. The full behavioral contract (phases, gates,
> convergence rules, every CLI flag in depth, memory discipline, retry
> continuity, frame capture, etc.) lives in **[SPEC.md](SPEC.md)**. Evolve
> uses `SPEC.md` as its own convergence target.

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

# Evolve a project (README.md = spec by default, or use --spec)
evolve start <project-dir> [--rounds 10] [--check "pytest"] [--timeout 300] [--model claude-opus-4-6] [--allow-installs] [--json] [--spec SPEC.md]

# Preview what the agent would do (read-only, no file changes)
evolve start <project-dir> --dry-run [--check "pytest"]

# Validate spec compliance without evolving (pass/fail per spec claim)
evolve start <project-dir> --validate [--check "pytest"]

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

See [SPEC.md](SPEC.md#cli-flags) for the full behavior of every flag.

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
evolve start ~/projects/my-tool --check "pytest" --allow-installs

# Use a different model
evolve start ~/projects/my-tool --check "pytest" --model claude-sonnet-4-20250514

# Dry run — see what the agent would change without modifying files
evolve start ~/projects/my-tool --check "pytest" --dry-run

# Validate — check spec compliance without modifying files
evolve start ~/projects/my-tool --check "pytest" --validate

# Resume after interruption (continues from last completed round)
evolve start ~/projects/my-tool --check "pytest" --resume

# Autonomous forever mode — evolves indefinitely on a separate branch
evolve start ~/projects/my-tool --check "pytest" --forever

# JSON output for CI/CD pipelines
evolve start ~/projects/my-tool --check "pytest" --json

# Use a custom spec file instead of README.md
evolve start ~/projects/my-tool --check "pytest" --spec SPEC.md
evolve start ~/projects/my-tool --check "pytest" --spec docs/specification.md

# Initialize a config file with sensible defaults
evolve init ~/projects/my-tool

# Show evolution history across all sessions
evolve history ~/projects/my-tool

# Clean up old sessions, keeping the 5 most recent
evolve clean ~/projects/my-tool --keep 5
```

## Configuration

Evolve supports project-level configuration via `evolve.toml` or
`pyproject.toml`. This eliminates the need to repeat CLI flags on every run.

Create an `evolve.toml` in your project root:

```toml
# evolve.toml
check = "pytest"
rounds = 20
timeout = 300
model = "claude-opus-4-6"
allow_installs = false   # let the agent install new packages for [needs-package] items
spec = "README.md"       # path to the spec file (default: README.md)

[hooks]
on_round_end = "echo 'Round complete'"
on_converged = "curl -s -X POST https://hooks.slack.com/services/YOUR/WEBHOOK/URL -d '{\"text\": \"evolve converged!\"}'"
on_error = "notify-send 'evolve encountered an error'"
```

Or add an `[tool.evolve]` section to your existing `pyproject.toml`:

```toml
[tool.evolve]
check = "pytest"
rounds = 20
timeout = 300

[tool.evolve.hooks]
on_converged = "curl -s -X POST https://your-webhook-url"
```

Settings resolve in this order (first wins): **CLI flags → environment
variables → `evolve.toml` → `pyproject.toml [tool.evolve]` → defaults.** Full
field list and semantics in
[SPEC.md § Config resolution](SPEC.md#config-resolution).

Scaffold a config file quickly:

```bash
evolve init ~/projects/my-tool
# Creates ~/projects/my-tool/evolve.toml with default settings
```

## TUI preview

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

On completion:

```
╭──────────── Evolution Complete ─────────────╮
│ ✅ CONVERGED in 8 rounds (12m 34s)          │
│                                              │
│ 6 improvements completed                    │
│ 2 bugs fixed                                │
│ 47 tests passing                            │
│                                              │
│ Report: runs/20260325/evolution_report.md    │
╰──────────────────────────────────────────────╯
```

The TUI falls back to plain text when `rich` is not installed, and to
structured JSON events when `--json` is passed. It can optionally capture
visual frames (PNG) for party mode to reason about. Full details —
`TUIProtocol`, frame capture pipeline, JSON event schema — in
[SPEC.md § TUI](SPEC.md#tui).

## Writing specs for evolve

Evolve treats your spec file (`README.md` by default, or whatever `--spec`
points at) as the source of truth. The quality of your spec directly affects
how well evolve can converge your project. Guidelines:

### Be specific and verifiable

```markdown
# Good — evolve can verify this
"The CLI returns exit code 0 on success and exit code 1 on failure."
"Tests target 80% coverage minimum."

# Vague — evolve can't objectively verify this
"The CLI has good error handling."
"The code is well-tested."
```

### Include command examples

Evolve can literally run examples from your spec to verify they work:

```markdown
# Good — testable commands
$ my-tool parse input.json --format csv
$ my-tool validate schema.json

# Vague — not testable
"my-tool supports various input formats"
```

### Describe the architecture

Architectural descriptions help the agent make consistent design decisions:

```markdown
# Good — clear structure
| Module | Responsibility |
|--------|---------------|
| cli.py | Argument parsing, entry point |
| core.py | Business logic |
| io.py | File I/O, serialization |
```

### State test expectations

Explicit test targets give the agent a clear convergence criterion:

```markdown
# Good — measurable
"The project targets 80% test coverage minimum."
"All public functions have type annotations and docstrings."
```

### Keep it current

The spec should describe what the project *should* be, not what it was.
Update it when requirements change — evolve's **spec freshness gate** picks
that up automatically on the next round and rebuilds the improvement backlog
from the updated spec (see
[SPEC.md § Convergence](SPEC.md#convergence)).

### Split user docs from the spec on large projects

Evolve projects tend to grow two audiences: users (need a friendly
introduction) and the agent (needs a dense, exhaustive contract). When your
README crosses ~800 lines, consider splitting:

- `README.md` — user-facing docs, install, quickstart, examples, TUI preview
- `SPEC.md` — the formal contract; what evolve converges to

Run with `--spec SPEC.md` (or set `spec = "SPEC.md"` in `evolve.toml`).
Convergence becomes tractable because the agent only has to verify a stable,
scoped contract, not an ever-growing mix of docs and claims. Evolve itself
follows this pattern — see the [SPEC.md](SPEC.md) in this repository.

## Requirements

- Python 3.10+
- `claude-agent-sdk`: `pip install claude-agent-sdk`
- `rich` (optional): `pip install rich` — for the modern TUI
- `cairosvg` (optional): `pip install ".[vision]"` — for TUI frame capture
- Git repository
- Claude Code CLI installed and authenticated

Evolve works with any Claude model supported by the Agent SDK. Recommended:

| Model | Best for |
|-------|----------|
| `claude-opus-4-6` (default) | Maximum capability, complex projects |
| `claude-sonnet-4-20250514` | Faster iterations, simpler projects |

## Future directions

These are under consideration for future evolution cycles:

- **Multi-repo evolution** — evolve multiple related projects in coordination
- **Spec drift detection** — detect when code drifts from the spec over time
  and auto-fix
- **Parallel analysis** — run read-only analysis in parallel before sequential
  implementation
- **GitHub App / hosted service** — managed evolution service for teams

---

📖 For the full specification — every phase, every gate, every flag, the
memory discipline, retry continuity, frame capture, party mode, CI/CD,
exit codes, and the module architecture — see **[SPEC.md](SPEC.md)**.

<!-- checked-by-anatoly -->
[![Checked by Anatoly](https://img.shields.io/badge/checked%20by-Anatoly-blue)](https://github.com/r-via/anatoly)
<!-- /checked-by-anatoly -->
