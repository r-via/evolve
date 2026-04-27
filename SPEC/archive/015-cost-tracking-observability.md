# 015 — Cost Tracking, State File, and Evolution Report

> Archived from SPEC.md § "Cost and token tracking", § "Real-time state
> file", and § "Evolution report" on 2026-04-27. Stable schemas and
> formats — implemented, working as specified.

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
   +-------------------- evolve ---------------------+
   | EVOLUTION ROUND 3/10                     ~$3.80 |
   ```

2. **Completion summary** — total session cost:
   ```
   +------------ Evolution Complete -------------+
   | CONVERGED in 8 rounds (12m 34s)             |
   |                                              |
   | 6 improvements completed                    |
   | 47 tests passing                            |
   | ~$12.40 estimated cost                       |
   +----------------------------------------------+
   ```

`PlainTUI` shows cost as a simple text line. `JsonTUI` emits
`{"type": "usage", ...}` events (see § "The --json flag").

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
