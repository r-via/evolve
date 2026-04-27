# 007 — Development: Test Structure, Smoke Test, and Drift Tests

Archived from SPEC.md § Development on 2026-04-27.
Trigger: SPEC.md > 2000 lines (3075 lines at archival time).

---

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
command passed. This catches regressions in the full subprocess -> agent ->
git -> state pipeline that unit tests miss.

### Path-agnostic drift tests

Constant-drift tests (`test_constant_drift.py`) and spec-prompt-sync tests
(`test_spec_prompt_sync.py`) scan source files for magic strings and
invariants. These tests use dynamic package discovery (`importlib` or
`glob`) to find source files rather than hardcoding paths like `"loop.py"`.
This ensures the tests survive the package restructuring without per-file
updates.
