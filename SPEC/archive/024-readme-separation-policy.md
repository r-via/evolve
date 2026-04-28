# README as a user-level summary (when `--spec` is set)

> Archived from SPEC.md on 2026-04-28 (round 20, Sid).
> Trigger: every 20 rounds; settled separation policy, fully implemented.

---

When `--spec` points at a file other than `README.md`, the two documents
serve **orthogonal purposes** and evolve separately:

- **SPEC.md** — the contract evolve converges to. Exhaustive, dense,
  may include internal implementation details. Changes often as the
  system grows.
- **README.md** — a user-level **summary** that helps a reader discover
  what the software does and how to use it. Deliberately incomplete
  relative to SPEC. Changes slowly, in response to user-visible
  behavior changes, not to internal refactors.

**The evolution loop never writes to `README.md`.** Party mode only
produces `<spec>_proposal.md` (never a README proposal). README is
authored and maintained by the human operator.

When the operator wants to refresh README to reflect the current spec
(e.g. after a batch of user-visible feature adds), they invoke the
dedicated one-shot subcommand `evolve sync-readme` (see CLI flags §
"evolve sync-readme"). This is never automatic and never runs as part
of a round — it is an explicit, human-initiated action.

### Stale-README pre-flight check (lightweight observability)

At the start of every `evolve start` (before any round), the orchestrator
compares `mtime(spec_file)` and `mtime(README.md)`. If the spec is
significantly newer — default threshold **30 days** — the TUI prints a
single-line advisory:

```
ℹ️  README has not been updated in 42 days — consider `evolve sync-readme`
```

This is pure observability. It does not block anything, does not modify
any file, and does not appear during rounds (only once at startup). It
is the only automated reference the evolution loop makes to the README.
Threshold configurable via `evolve.toml`:

```toml
[tool.evolve]
readme_stale_threshold_days = 30   # or 0 to disable the advisory entirely
```

(When `--spec` is unset, README IS the spec — this section does not
apply, and no advisory is ever emitted.)
