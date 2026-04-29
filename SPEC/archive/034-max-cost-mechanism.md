# --max-cost flag — mechanism, config, and TUI

> Archived from SPEC.md § "The --max-cost flag" on 2026-04-29.
> Stub in SPEC.md preserves the normative summary.

---

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
