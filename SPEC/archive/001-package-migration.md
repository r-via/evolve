# 001 — Package Migration (backward compatibility + migration strategy)

Archived from SPEC.md § Architecture on 2026-04-27.
Trigger: SPEC.md > 2000 lines (3075 lines at archival time).

---

**Backward compatibility.** During the migration from flat modules to the
package structure, root-level shim files (`loop.py`, `agent.py`, `tui.py`,
`hooks.py`) re-export all public names from their new locations via
`from evolve.orchestrator import *` etc. These shims exist for one release
cycle and emit `DeprecationWarning` on import. They will be removed in a
future version.

**Migration strategy.** The restructuring is designed to work within evolve's
"one granular improvement per round" model:

1. Create `evolve/` package skeleton with `__init__.py`
2. Move `hooks.py` first (smallest module, fewest dependencies)
3. Extract `git.py` from `loop.py` (self-contained git operations)
4. Extract `state.py` from `loop.py` (state management, convergence gates)
5. Extract `party.py` from `loop.py` (party mode orchestration)
6. Split `tui.py` into `tui/` subpackage
7. Move `agent.py` into the package
8. Move `evolve.py` → `evolve/cli.py`, update entry point
9. Remaining `loop.py` → `evolve/orchestrator.py`
10. Remove root-level shims once all imports updated

Each step is one round. Tests and imports are updated in the same round as
each move.
