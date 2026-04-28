# Check Command Ownership (archived from SPEC.md)

*Archived: 2026-04-28 — settled design constraint, rule encoded in prompts/system.md*

---

**Single-source-of-truth: agents must NOT run the check command
themselves.**

The orchestrator is the only actor that invokes the check command
— once in pre-check (before the agent runs) and once in post-check
(after the agent commits).  The agent receives both outputs in its
prompt and is **forbidden** from running the check command via its
own Bash tool.  Two reasons:

1. **Cost and time explosion.**  Each agent-side run is another
   full test-suite execution layered on top of the orchestrator's
   two.  A chatty agent that runs pytest after every edit turns one
   round's budget of ``2x20s`` into ``10x20s`` trivially.
2. **Watchdog / heartbeat budget.**  The agent's Bash calls run
   inside the round subprocess where the round-wide heartbeat
   keeps the parent watchdog quiet; a long agent-side pytest
   (especially piped through ``| tail``) still consumes real wall
   time, eating into the ``--max-cost`` budget and the operator's
   patience.
3. **Single authoritative signal.**  Two independent pytest runs
   can disagree (flaky test, different CWD, environment drift).
   One orchestrator-controlled run is the source of truth.

The agent reasons from the orchestrator's pre-check output
(``## Check results`` section in the prompt), makes targeted edits,
and trusts the orchestrator's post-check to verify.  If the agent
needs finer granularity (single-file test, ``--durations=5``,
``-x``), it MUST edit the test file or fixtures and let the
orchestrator's next round re-run — not spawn a separate suite.
The system prompt in ``prompts/system.md`` encodes this as the
default rule.

**Narrow escape hatch: ``timeout``-wrapped verification.**

There is one narrow case where the agent genuinely needs fresh
pytest output mid-turn: it has just edited one or more test files
and the orchestrator's post-check (running at round end) cannot
help because the agent must decide whether to proceed with the
current commit or revert.  In that case the agent is permitted to
run the check command ONCE, but **only when wrapped in the
system-level ``timeout`` utility** with the same budget the
orchestrator uses:

```bash
timeout {check_timeout} pytest tests/test_foo.py -x -q
```

(The ``{check_timeout}`` placeholder is substituted into the
system prompt at round start from the resolved ``--timeout`` /
``EVOLVE_TIMEOUT`` / ``evolve.toml`` value — whatever the
orchestrator itself uses.)

A bare ``pytest`` / ``npm test`` / ``cargo test`` call without the
``timeout`` prefix is forbidden regardless of justification — it
bypasses the quality invariant and can stall the round for
minutes.  The agent is instructed to prefer ``-x`` plus a single-
file scope to keep the run well under the ceiling.

**Future enforcement.**  A permission-callback hook on the SDK's
Bash tool could auto-reject or auto-wrap check-like commands that
arrive without ``timeout``; this is deferred to a separate backlog
item (the current implementation is prompt-level only).
