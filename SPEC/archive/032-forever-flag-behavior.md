# --forever flag — detailed behavior

> Archived from SPEC.md § "The --forever flag" on 2026-04-29.
> Stub in SPEC.md preserves the normative summary.

---

**How it works:**

1. Creates a new branch `evolve/<timestamp>` from the current branch
2. Runs the normal evolution loop (Phase 1-4) until convergence
3. After convergence, launches party mode — agents brainstorm the next cycle
4. **Instead of waiting for operator approval**, automatically merges the
   `<spec>_proposal.md` into the spec file
5. Resets `improvements.md` and starts a new evolution loop against the
   updated spec
6. Repeats until stopped by the operator

```
main ──────────────────────────────────────────────────
       \
        evolve/20260324_220000 ─── round 1 ─── round 2 ─── CONVERGED
                                                                │
                                                          party mode
                                                                │
                                                     SPEC_proposal → SPEC.md
                                                                │
                                                          round 1 ─── round 2 ─── CONVERGED
                                                                                       │
                                                                                 party mode
                                                                                       │
                                                                                     ...
```

All work happens on the `evolve/*` branch — `main` is never touched. The
operator can:
- Watch progress in real-time via the TUI
- Review the branch at any time (`git log evolve/<timestamp>`)
- Merge when satisfied (`git merge evolve/<timestamp>`)
- Or discard the branch entirely (`git branch -D evolve/<timestamp>`)

Combines well with `--allow-installs` for fully autonomous evolution:

```bash
evolve start ~/projects/my-tool --check "pytest" --forever --allow-installs
```
