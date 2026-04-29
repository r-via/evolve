# Legacy Flat-Module Layout (archived from SPEC.md)

> **Archived:** 2026-04-29 | **Trigger:** Every 20 rounds (round 20)
> **Status:** Superseded by DDD layered architecture (see SPEC.md "Source code layout -- DDD")

---

## Module responsibilities (flat layout)

Evolve is organized as a Python package (`evolve/`) with clear module
responsibilities:

| Module | Responsibility |
|--------|---------------|
| `evolve/cli.py` | CLI entry point, argument parsing, config resolution |
| `evolve/orchestrator.py` | Round lifecycle, subprocess monitoring, watchdog, debug retries |
| `evolve/agent.py` | Claude SDK interface -- prompt building, agent execution, retry logic |
| `evolve/git.py` | Git operations -- commit, push, branch management, ensure-git |
| `evolve/state.py` | State management -- state.json, improvements parsing, convergence gates, backlog discipline |
| `evolve/party.py` | Party mode orchestration -- multi-agent brainstorming, proposal generation |
| `evolve/tui/__init__.py` | TUI protocol definition and `get_tui` factory |
| `evolve/tui/rich.py` | Rich-based TUI implementation with frame capture |
| `evolve/tui/plain.py` | Plain-text fallback TUI |
| `evolve/tui/json.py` | Structured JSON output TUI for CI/CD |
| `evolve/hooks.py` | Event hooks -- loading config, matching events, fire-and-forget execution |
| `evolve/costs.py` | Token tracking, cost estimation, budget enforcement |

## Package structure (flat layout)

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
