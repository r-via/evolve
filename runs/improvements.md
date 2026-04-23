# Improvements

- [x] [functional] Add costs.py module with TokenUsage dataclass, RATES table, estimate_cost, format_cost, aggregate_usage, and build_usage_state — foundation for token tracking, cost estimation, and budget enforcement per SPEC.md
- [ ] [functional] [P1] Wire costs.py into orchestrator: add --max-cost CLI flag, write usage_round_N.json per round, aggregate usage into state.json, enforce budget cap with graceful pause
