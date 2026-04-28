# `evolve sync-readme` subcommand

> Archived from SPEC.md on 2026-04-28 (round 20, Sid).
> Trigger: every 20 rounds; complete subcommand spec, fully designed and implemented.

---

One-shot subcommand that refreshes `README.md` to reflect the current spec.
Never runs as part of the evolution loop — always invoked explicitly by the
operator:

```bash
# Produce a proposal for review (default; does not modify README.md)
evolve sync-readme [<project-dir>] [--spec SPEC.md]

# Apply the refresh directly, committing the updated README
evolve sync-readme [<project-dir>] --apply [--spec SPEC.md]
```

**How it works:**

1. Loads the spec file (resolved from `--spec`, `evolve.toml`, `EVOLVE_SPEC`,
   or default `README.md` — same resolution order as every other flag).
2. Loads the current `README.md`.
3. Launches the agent in a dedicated one-shot session with a sync-focused
   prompt: *"Update the README to reflect the current spec. Preserve the
   README's tutorial voice — brevity, examples, links to the spec for
   internals. Do not copy the spec verbatim. Do not invent features that
   aren't in the spec."*
4. Writes the output to `README_proposal.md` at the project root (default
   mode) or directly to `README.md` with a git commit (`--apply` mode).
5. Exits.

**Exit codes:**

| Exit Code | Meaning |
|-----------|---------|
| 0 | Proposal written (or applied) successfully |
| 1 | README already in sync — no changes proposed |
| 2 | Error — spec file missing, agent failure, etc. |

**When to use it:**

- After adopting a batch of SPEC changes that introduced user-visible
  features and the README is now misleading
- After a `--forever` run accumulated many cycles and the README has
  drifted from the current behavior
- When the startup advisory (`ℹ️  README has not been updated in N days`)
  prompts you

**What it does NOT do:**

- Run during rounds
- Block convergence
- Add items to `improvements.md`
- Touch any file other than `README.md` (and `README_proposal.md` in
  default mode)

The subcommand is the **only** sanctioned way evolve ever writes to
`README.md` when `--spec` points at a separate file. This separation —
evolution loop touches spec + code, `sync-readme` touches README — is
intentional: it keeps the two concerns orthogonal and avoids the
failure mode where automated sync creates silent drift between user
docs and actual behavior.
