# Improvements

- [x] [functional] Add costs.py module with TokenUsage dataclass, RATES table, estimate_cost, format_cost, aggregate_usage, and build_usage_state — foundation for token tracking, cost estimation, and budget enforcement per SPEC.md
- [x] [functional] [P1] Wire costs.py into orchestrator: add --max-cost CLI flag, write usage_round_N.json per round, aggregate usage into state.json, enforce budget cap with graceful pause
- [x] [functional] [P1] Add cost display to TUI round_header and completion_summary (estimated_cost_usd param), and add Cost Summary table to evolution_report.md per SPEC § "Cost in evolution report" and § "TUI cost display"
- [x] [functional] [P2] Implement `evolve diff` subcommand: CLI parser, read-only agent with --effort low, produce diff_report.md with per-section compliance, exit codes 0/1/2 per SPEC § "evolve diff"
- [x] [functional] [P2] Begin package restructuring per SPEC § "Architecture": create evolve/ package skeleton with __init__.py, move hooks.py into package (step 1-2 of migration strategy), update imports and add backward-compat shim at root
- [x] [functional] [P1] Implement agent-side structural change self-detection per SPEC.md § "Structural change self-detection" (new section). The agent must, during Phase 3.5 (newly inserted before COMMIT_MSG), detect whether its edit is structural via the documented heuristics (git diff rename filter, pyproject.toml section touches, __init__.py/__main__.py changes, conftest changes, imports of created/deleted files) and if so: (a) prefix COMMIT_MSG's first line with `STRUCTURAL: `, (b) write `{run_dir}/RESTART_REQUIRED` with five fields (reason/verify/resume/round/timestamp), (c) SKIP Phase 4 (no CONVERGED). Implementation is prompt-driven — no new code in agent.py. Tests in tests/test_agent.py verifying the system-prompt template contains the Phase 3.5 block with all the documented trigger conditions, the STRUCTURAL prefix rule, the RESTART_REQUIRED schema, and the Phase 4 skip directive
- [x] [functional] [P1] Implement orchestrator-side RESTART_REQUIRED handling per SPEC.md § "Structural change self-detection". In loop.py's `_run_rounds` / round-end pipeline: after commit + push + check + state.json, check for `{run_dir}/RESTART_REQUIRED`. If present: (a) fire new `on_structural_change` hook (extend hooks.py event list) with marker fields as env vars, (b) render a blocking red panel via a new `ui.structural_change_required(marker)` method on all three TUIs (RichTUI / PlainTUI / JsonTUI per TUIProtocol parity), (c) have `evolve start` exit with code 3. `--forever` MUST NOT bypass — structural changes always pause autonomy. Tests: mock a round that writes RESTART_REQUIRED and verify evolve_loop returns 3, panel renders on all three TUIs, on_structural_change hook fires with the documented env vars
- [x] [functional] [P1] Add entry-point integrity backup tests per SPEC.md § "Detection confidence" (the reactive layer): create tests/test_entry_point_integrity.py with two tests that spawn a REAL (not mocked) subprocess — one runs `[sys.executable, "-m", "evolve", "--help"]` with timeout=10 and asserts returncode==0 + "usage" in stdout, the other runs `[sys.executable, "-m", "evolve", "_round", "--help"]` with same assertions. These catch the ~5% of structural changes that self-detection would miss (false negatives). Must run as part of the default `pytest tests/` suite
- [x] [functional] [P2] Continue package restructuring step 3: extract git.py from loop.py into evolve/git.py (self-contained git operations — _ensure_git, _git_commit, _git_push, branch management), update imports, add backward-compat shim at root. BLOCKED on the three P1 items above — DO NOT start this until structural-change self-detection + orchestrator RESTART_REQUIRED handling + entry-point integrity tests are all [x], because this item WILL trigger self-detection (creates evolve/git.py + mutates loop.py imports, both structural signals). Once the P1 guards exist, this produces a STRUCTURAL commit + RESTART_REQUIRED marker, the run stops cleanly at exit 3, operator verifies `python -m evolve --help` still works, then resumes with `--resume`
- [x] [functional] [P2] Continue package restructuring step 4: extract state.py from loop.py into evolve/state.py (state management — state.json, improvements parsing, convergence gates, backlog discipline), update imports, keep backward-compat via loop.py re-exports
- [x] [functional] [P2] Continue package restructuring: move costs.py into evolve/costs.py, update imports in loop.py and agent.py, add backward-compat shim at root with DeprecationWarning — per SPEC § "Architecture" package structure
- [x] [functional] [P2] Continue package restructuring step 6: split tui.py into evolve/tui/ subpackage (evolve/tui/__init__.py with TUIProtocol + get_tui factory, evolve/tui/rich.py with RichTUI, evolve/tui/plain.py with PlainTUI, evolve/tui/json.py with JsonTUI), update imports in loop.py and agent.py, add backward-compat shim at root tui.py with DeprecationWarning — per SPEC § "Architecture" package structure step 6
- [x] [functional] [P2] US-014: Move agent.py into evolve/agent.py (migration step 7)
  **As** an evolve operator, **I want** the agent module inside the evolve package **so that** `pip install .` produces a clean package layout per SPEC § Architecture.
  **Acceptance criteria (must all pass before the item is [x]'d):**
  1. `evolve/agent.py` exists and contains all code currently in root `agent.py`
  2. Root `agent.py` becomes a backward-compat shim with DeprecationWarning on import
  3. `pytest tests/` passes 100% with zero new failures
  4. `python -m evolve --help` returns exit code 0
  **Definition of done:**
  - `evolve/agent.py` created with full module contents
  - Root `agent.py` converted to shim with DeprecationWarning
  - All imports updated or working via shim
  - STRUCTURAL commit with RESTART_REQUIRED marker
  **Architect notes (Winston):** Same shim pattern as hooks.py/costs.py. ~1573 lines, heavy imports from loop.py. Test imports via shim keep working.
  **PM notes (John):** P2 infrastructure. Blocks steps 8-10. NOT removing shim (step 10). NOT changing public API.
- [x] [functional] [P2] US-015: Move CLI into evolve/cli.py and update entry point (migration step 8)
  **As** an evolve operator, **I want** the CLI entry point at `evolve.cli:main` **so that** the package layout matches SPEC § Architecture and `evolve/__init__.py` is a clean package marker.
  **Acceptance criteria (must all pass before the item is [x]'d):**
  1. `evolve/cli.py` exists with all CLI parsing, config resolution, and `main()` function
  2. `evolve/__init__.py` is a package marker with re-exports for backward compat, not the CLI itself
  3. `pyproject.toml` entry point is `evolve.cli:main`
  4. `pytest tests/` passes 100% with zero new failures
  5. `python -m evolve --help` returns exit code 0
  **Definition of done:**
  - `evolve/cli.py` created from current `evolve/__init__.py` CLI code
  - `evolve/__init__.py` trimmed to package marker + re-exports
  - `pyproject.toml` entry point updated
  - STRUCTURAL commit with RESTART_REQUIRED marker
  **Architect notes (Winston):** Largest move — __init__.py is 800+ lines of CLI. Risk: entry point change in pyproject.toml is structural. Must verify `pip install -e .` + `evolve --help` still works.
  **PM notes (John):** P2 infrastructure. Blocks step 9. NOT refactoring CLI internals — pure move.
- [x] [functional] [P2] US-016: Move loop.py into evolve/orchestrator.py (migration step 9)
  **As** an evolve operator, **I want** the orchestrator module at `evolve/orchestrator.py` **so that** the package layout matches SPEC § Architecture and all core logic lives inside `evolve/`.
  **Acceptance criteria (must all pass before the item is [x]'d):**
  1. `evolve/orchestrator.py` exists and contains all code currently in root `loop.py` — DONE (2093 lines)
  2. Root `loop.py` becomes a backward-compat shim with DeprecationWarning on import — DONE (77-line shim)
  3. `pytest tests/` passes 100% with zero new failures — DONE (1079 pass)
  4. `python -m evolve --help` returns exit code 0 — DONE
  **Definition of done:**
  - `evolve/orchestrator.py` created with full module contents
  - Root `loop.py` converted to shim
  - All imports updated or working via shim
  - STRUCTURAL commit with RESTART_REQUIRED marker
  **Architect notes (Winston):** loop.py is ~2093 lines, the largest module. Heavy cross-imports with agent.py. After agent.py is in-package (US-014), internal imports become `from evolve.agent import ...`. Test patches must be updated for new module path.
  **PM notes (John):** P2 infrastructure. Blocks step 10 (shim removal). NOT refactoring orchestrator internals.
- [x] [functional] [P2] US-019: Remove root-level backward-compat shims (migration step 10)
  **As** an evolve operator, **I want** the root-level shim files removed **so that** the project is clean with all code inside `evolve/` per SPEC § Architecture and the project root is uncluttered.
  **Acceptance criteria (must all pass before the item is [x]'d):**
  1. Root-level `agent.py`, `loop.py`, `tui.py`, `hooks.py`, `costs.py` are deleted
  2. `pyproject.toml` `py-modules` key removed from `[tool.setuptools]`
  3. All test imports use `from evolve.X import` paths — no root module imports remain
  4. `pytest tests/` passes 100% with zero new failures
  5. `python -m evolve --help` returns exit code 0
  **Definition of done:**
  - All 5 root shim files deleted
  - `pyproject.toml` `[tool.setuptools]` `py-modules` removed
  - All test `from X import` updated to `from evolve.X import`
  - STRUCTURAL commit with RESTART_REQUIRED marker
  **Architect notes (Winston):** 24 import statements across 19 test files need updating. Patch targets already use `evolve.*` paths (476 occurrences) — no patch changes needed. File deletions trigger structural detection. DeprecationWarnings have been active for one release cycle per SPEC.
  **PM notes (John):** P2 — completes the 10-step migration strategy. NOT adding features. NOT changing behavior. Depends on US-014, US-015, US-016 all [x] (they are).
- [x] [functional] [P2] US-020: Implement .evolve/ directory layout and legacy runs/ migration
  **As** an evolve operator, **I want** all evolve artifacts under `.evolve/runs/` **so that** the tool follows the dotfile convention per SPEC § "The .evolve/ directory" and doesn't pollute the target project's root.
  **Acceptance criteria (must all pass before the item is [x]'d):**
  1. New sessions create artifacts under `<project>/.evolve/runs/` not `<project>/runs/`
  2. Legacy `runs/` detected on startup triggers `git mv runs .evolve/runs` migration with `[migrate]` notice
  3. Both `runs/` and `.evolve/runs/` existing triggers a clear error refusing to start
  4. All path resolution uses a centralized helper, no scattered `"runs"` literals
  5. `pytest tests/` passes 100% with zero new failures
  **Definition of done:**
  - Centralized `_runs_base(project_dir)` helper returning `.evolve/runs/`
  - All path resolution in orchestrator, agent, party, state uses helper
  - Migration logic in startup for legacy `runs/`
  - Ambiguous-state detection and error
  - Tests covering new, legacy-migrate, and ambiguous cases
  **Architect notes (Winston):** ~30 path references across 4 source modules, ~400 in tests. Introduce centralized helper to minimize future drift. STRUCTURAL change — must write RESTART_REQUIRED.
  **PM notes (John):** P2 — user-facing improvement (cleaner project roots). NOT changing artifact format, only location.