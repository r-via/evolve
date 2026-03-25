# Improvements
- [x] [functional] Increase test coverage from 67% to ≥80% by adding tests for analyze_and_fix retry logic, run_single_round, _resolve_config env var paths, and evolve_loop/resume/forever paths
- [x] [functional] Implement `evolve history` subcommand as described in the README — show evolution timeline across all sessions with round counts, status, and improvement stats
- [x] [functional] Implement auto-detection of test framework when --check is omitted — README says evolve should look for pytest, npm test, cargo test, go test, make test and use the first found
- [ ] [functional] Implement --dry-run flag — README describes read-only analysis mode that produces dry_run_report.md without modifying files
