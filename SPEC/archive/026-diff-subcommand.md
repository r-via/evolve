# `evolve diff` subcommand

> Archived from SPEC.md on 2026-04-28 (round 20, Sid).
> Trigger: every 20 rounds; complete subcommand spec, fully designed and implemented.

---

One-shot subcommand that shows the delta between the current spec and the
implementation. Lighter-weight than `--validate` — focused on quickly
identifying gaps rather than exhaustive claim-by-claim verification.

```bash
evolve diff [<project-dir>] [--spec SPEC.md]
```

**How it works:**

1. Loads the spec file (same resolution as every other flag)
2. Launches the agent in read-only mode with `--effort low` and a
   gap-detection prompt: *"Scan the spec for major features and
   architectural claims. For each one, check whether it is present in
   the codebase. Report gaps — do not verify exhaustively."*
3. Produces `runs/<session>/diff_report.md` with:
   - Each major spec section with ✅ (present) or ❌ (missing)
   - Overall compliance percentage
   - Specific gaps identified with brief descriptions
4. No files are modified, no git commits are created

**Exit codes:**

| Exit Code | Meaning |
|-----------|---------|
| 0 | All major spec sections present — compliant |
| 1 | One or more gaps found |
| 2 | Error — spec file missing, agent failure, etc. |

**Differences from `--validate`:**
- Uses `--effort low` by default (cheaper, faster)
- Checks for presence/absence of major features, not line-by-line verification
- Does not run the check command
- Produces a shorter, more actionable report
- Designed for quick "how far are we?" checks, not formal validation
