# Improvements

- [x] [functional] Implement hooks.py module and integrate event hooks (on_round_start, on_round_end, on_converged, on_error) into the evolution loop as specified in the README
- [x] [functional] Add completion summary panel to TUI that displays improvements completed, bugs fixed, tests passing, and report path as described in README's "Completion summary" section
- [x] [functional] Implement real-time state.json file in session directory, updated after every round with version, session, project, round, max_rounds, phase, status, improvements counts, last_check results, and timestamps as described in README's "Real-time state file" section
- [x] [functional] Implement --validate flag for spec compliance checking: add CLI argument, create run_validate() function in loop.py, produce validate_report.md with pass/fail per README claim, return exit code 0 (all pass) or 1 (failures) as described in README's "--validate flag" section
- [x] [performance] Reduce code duplication in agent.py by extracting shared agent runner logic from _run_dry_run_claude_agent, _run_validate_claude_agent, and run_claude_agent into a reusable helper, since all three share nearly identical SDK streaming, deduplication, and logging code
- [x] [performance] Extract duplicated README/improvements loading logic from build_prompt, build_validate_prompt, and build_dry_run_prompt into a shared _load_project_context helper to reduce repetition and centralize file-loading logic
- [ ] [performance] Extract duplicated check_section building logic from build_validate_prompt and build_dry_run_prompt into a shared _build_check_section helper, since both use the identical pattern for generating check command output sections
