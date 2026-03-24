# Improvements

- [x] [functional] Fix _show_status glob pattern: uses `probe_round_*.txt` but files are named `check_round_*.txt`, causing status to always report 0 check results
- [x] [functional] Add `--timeout` flag to README Usage section — it's implemented in code but not documented, and the default 30s is too low for most real test suites
- [ ] [functional] The evolve.py docstring shows `evolve <project-dir>` but the actual CLI requires `evolve start <project-dir>` — update docstring to match, and add `status` subcommand documentation to align with README Usage section
