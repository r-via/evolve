# Improvements

- [x] [functional] Add costs.py module with TokenUsage dataclass, RATES table, estimate_cost, format_cost, aggregate_usage, and build_usage_state — foundation for token tracking, cost estimation, and budget enforcement per SPEC.md
- [x] [functional] [P1] Wire costs.py into orchestrator: add --max-cost CLI flag, write usage_round_N.json per round, aggregate usage into state.json, enforce budget cap with graceful pause
- [x] [functional] [P1] Add cost display to TUI round_header and completion_summary (estimated_cost_usd param), and add Cost Summary table to evolution_report.md per SPEC § "Cost in evolution report" and § "TUI cost display"
- [ ] [functional] [P2] Implement `evolve diff` subcommand: CLI parser, read-only agent with --effort low, produce diff_report.md with per-section compliance, exit codes 0/1/2 per SPEC § "evolve diff"
