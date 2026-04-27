# 003 — Migration from Legacy `runs/` Directory

Archived from SPEC.md § Session layout on 2026-04-27.
Trigger: SPEC.md > 2000 lines (3075 lines at archival time).

---

**Migration from legacy `runs/`.**  Projects that predate this layout
have a top-level `runs/` directory.  On first encounter, evolve MUST:

1. Detect the ambiguous state: both `<project>/runs/` and
   `<project>/.evolve/runs/` exist, or only legacy `<project>/runs/`.
2. If only legacy exists -> migrate in-place: `git mv runs .evolve/
   runs` (so git history is preserved and the commit lands in the
   current session), emit an operator-facing notice `[migrate]
   moved runs/ -> .evolve/runs/`, continue.
3. If both exist -> refuse to start with a clear error pointing at
   one of:
   - `mv runs/* .evolve/runs/ && rmdir runs` (merge legacy into new)
   - `rm -rf runs` (discard legacy; for projects where the state is
     not worth preserving)
   The operator must resolve before the next run — evolve will not
   pick a winner automatically.
