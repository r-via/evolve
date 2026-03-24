# Improvements

- [x] [functional] Fix _show_status glob pattern: uses `probe_round_*.txt` but files are named `check_round_*.txt`, causing status to always report 0 check results
- [ ] [functional] Add `--timeout` flag to README Usage section — it's implemented in code but not documented, and the default 30s is too low for most real test suites
