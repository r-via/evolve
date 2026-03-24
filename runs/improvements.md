# Improvements

- [x] [functional] Fix _show_status glob pattern: uses `probe_round_*.txt` but files are named `check_round_*.txt`, causing status to always report 0 check results
- [x] [functional] Add `--timeout` flag to README Usage section — it's implemented in code but not documented, and the default 30s is too low for most real test suites
- [x] [functional] The evolve.py docstring shows `evolve <project-dir>` but the actual CLI requires `evolve start <project-dir>` — update docstring to match, and add `status` subcommand documentation to align with README Usage section
- [x] [functional] In agent.py `run_claude_agent`, the conversation log file is opened without a context manager (`log = open(...)`) — if an exception occurs the file handle leaks. Refactor to use `with open(...) as log:` for proper resource cleanup
- [x] [functional] In `loop.py` `_run_party_mode`, the party agent conversation is saved as `conversation_loop_0.md` (round_num=0) which is misleading and could collide with real round numbering — use a distinct filename like `party_conversation.md` to clearly distinguish the party mode session
- [ ] [functional] The `_get_current_improvement` function in `loop.py` returns the first unchecked item regardless of `[needs-package]` tag — when `--yolo` is not set, items tagged `[needs-package]` should be skipped by the orchestrator so the agent doesn't get stuck trying to implement a blocked improvement
