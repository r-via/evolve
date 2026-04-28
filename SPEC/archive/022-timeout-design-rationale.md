# Timeout design rationale

*Archived from SPEC.md on 2026-04-28. Settled design rationale for
the 20-second default and timeout behavior.*

---

## Why 20 seconds as the default

A fast test suite is a quality invariant, not a target.  When tests
run in <= 20 s the agent can run, verify, fix, verify, iterate -- all
within a reasonable round budget.  When they creep past 20 s, the
evolve loop degrades: heartbeats stretch, the agent waits longer
between edit-and-verify cycles, the watchdog overhead grows, and
overall throughput drops.  The 20-second ceiling forces the agent to
investigate slowness (mark a flaky/slow test, tighten a fixture, drop
an expensive integration dep) rather than silently paper over it with
a bigger budget.

## What happens on TIMEOUT

The pre-check / post-check `subprocess.run(check_cmd, timeout=20)`
raises `TimeoutExpired`.  The orchestrator writes
`check_output = "TIMEOUT after 20s"` and passes that into the
agent's next prompt.  The agent recognises the TIMEOUT token and
switches into slowness-investigation mode: run `pytest --durations=5`
*outside* the watchdog (typically by asking the operator), identify
the offending test, apply the appropriate remedy (mark, fix, or
exclude), verify the suite comes back under 20 s, then resume the
original target.
