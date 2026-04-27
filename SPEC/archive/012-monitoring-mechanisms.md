# 012 — Monitoring Mechanisms (Prior Round Audit, Heartbeat, Circuit Breakers)

> Archived from SPEC.md § "Prior round audit", § "Round-wide heartbeat",
> and § "Circuit breakers" on 2026-04-27. Stable mechanisms — designed
> once, implemented, working as specified.

---

### Prior round audit

Every round (≥ 2) runs a pre-flight audit of the previous round's
artifacts before the agent touches the backlog.  The goal is a simple,
unavoidable rule: if round N-1 finished in a state that deserves a
second look, round N has to fix that second-look item *first*, not
carry on with whatever the improvements.md current target says.

**Signals scanned programmatically (by ``_detect_prior_round_anomalies``
in ``agent.py``):**

| Signal                         | Source                                                      |
|--------------------------------|-------------------------------------------------------------|
| orchestrator diagnostic present | ``subprocess_error_round_{N-1}.txt`` exists                  |
| post-fix check FAIL            | ``check_round_{N-1}.txt`` contains ``post-fix check: FAIL`` |
| watchdog stall / SIGKILL       | ``stalled (Ns without output) — killing subprocess`` in log |
| subprocess killed by signal    | ``Round N failed (exit -K)`` in log                         |
| pre-check TIMEOUT              | ``pre-check TIMEOUT after Ns`` in log                       |
| frame capture error            | ``Frame capture failed for X: not well-formed`` in log      |
| circuit breaker tripped (exit 4) | ``deterministic loop detected`` in log                      |

When any signal fires, ``build_prompt`` injects a dedicated
``## Prior round audit`` section at the top of the system prompt
(between ``target_section`` and ``prev_crash_section``) listing every
anomaly detected and the mandatory action sequence: read the three
artifacts named above, identify root cause, apply the fix, commit
with a ``fix(audit):`` prefix, *then* resume the current target.

**Interaction with the existing prev_crash / retry-continuity paths:**

- ``prev_crash_section`` (pre-existing) handles the *strong* signal of
  an orchestrator diagnostic file and tailors the message per crash
  type (MEMORY WIPED, BACKLOG VIOLATION, NO PROGRESS, PREMATURE
  CONVERGED, generic CRASH).  The audit section is *additive*: it
  lists the diagnostic alongside the softer signals (frame capture
  errors, watchdog warnings, circuit-breaker notices) that the
  prev_crash path doesn't surface.
- ``prev_attempt_section`` (retry continuity) handles within-round
  continuity when the agent is on attempt 2 or 3.  The audit section
  handles *cross-round* continuity — the previous round committed,
  but left behind evidence that something needs attention before the
  next target.

**Deferral escape hatch.** If an anomaly is genuinely unfixable (a
flaky external service, a platform-specific bug that doesn't affect
the evolve project itself), the agent is instructed to document it in
``runs/memory.md`` under a ``## Known anomalies`` section rather than
spend every round re-investigating the same known-benign signal.
Rounds audit against that log: if the signal matches a known-anomaly
entry, the section is still rendered (so the operator sees it) but
the agent may acknowledge and proceed.

### Round-wide heartbeat

The parent orchestrator watches each round subprocess with a
silence-based watchdog (`_run_monitored_subprocess`,
`WATCHDOG_TIMEOUT` = 120s of no stdout → SIGKILL).  Several operations
inside a round naturally buffer or suppress output:

- The pre-check / post-check running `pytest` silently while it
  collects or runs long-running tests;
- Agent tool calls that pipe output through `| tail`, redirect to
  `/dev/null`, or pass `-q`/`--quiet` flags;
- Long agent "thinking" gaps between streaming messages (Opus can
  spend tens of seconds on extended reasoning before emitting);
- Git operations on large repos;
- The Claude Agent SDK subprocess buffering at its own layer.

Without intervention, any of these would race the watchdog and lose
— the round gets SIGKILL'd mid-work, the agent never completes, the
debug retry re-enters the same buffering pattern and loses again,
and the circuit breaker eventually fires because three attempts
share the same "stalled" signature.

To prevent that, `run_single_round` starts a daemon heartbeat thread
that prints `[probe] round N alive — Ns elapsed` every 30s for the
entire round duration.  The heartbeat is cheap (one print per
30 seconds), safely terminated via a `threading.Event` in a
`try/finally`, and covers every phase: pre-check, agent invocation,
agent tool calls, git commit, post-check.  Total round duration is
still bounded by the user's budget (`--max-cost`), round count
(`--rounds`), and convergence — the heartbeat removes only the
120-second silence-based cudgel, not those higher-level bounds.

Pre-check and post-check still use their own `subprocess.run(...,
timeout=timeout)` to catch genuinely hung commands.  When that
timeout fires, `check_output` becomes `"TIMEOUT after Ns"`, the
agent is invoked (or the post-check result recorded) normally, and
the agent receives the timeout message in its prompt — so it can
investigate (skip a flaky test, fix a slow fixture, adjust the
check command) rather than watching the round get murdered.

### Circuit breakers

The debug-retry loop retries each failure up to `MAX_DEBUG_RETRIES` times
within a round, and in `--forever` mode the orchestrator skips to the next
round if retries are exhausted. That design is right for **transient**
failures (a flaky test, an agent timeout that clears on retry) but wrong
for **deterministic** ones (a pre-check command that hangs on every round,
an irrecoverable bug that produces the same stack trace every time). Left
unchecked, forever mode would spin on a deterministic failure forever,
burning tokens without recovery.

**The rule.** When the same failure signature repeats across
`MAX_IDENTICAL_FAILURES` (=3) consecutive failed *attempts* — whether
those attempts are the three debug retries of a single round or span
multiple rounds in `--forever` — the orchestrator exits with **exit
code 4** ("deterministic failure loop detected").  Per-attempt (not
per-round) registration is deliberate: the classic pathology is a
pre-check command (e.g. `pytest`) that hangs identically on every
retry, and the first round already exposes three identical failures
that deserve a fast bail-out rather than burning two more rounds
before firing.

**Failure signature.** A short SHA-256 digest of:
1. Failure kind — `"stalled"`, `"crashed"`, or `"no-progress:<prefix>"`
   where `<prefix>` is one of `NO PROGRESS`, `MEMORY WIPED`, `BACKLOG
   VIOLATION`, or `silent`.
2. Subprocess returncode (negative values indicate kill signals).
3. The trailing 500 bytes of subprocess output (stripped), so that
   mostly-deterministic failures with varying prefixes (timestamps,
   round counters) still hash-match on their stable tail.

**When the counter resets.** Any successful round clears the accumulated
signatures, so a single recovery between otherwise-identical failures
resets the threshold. This makes the breaker specific to *sustained*
deterministic failures — it does not fire on occasional repeats
interleaved with progress.

**Relation to exit code 2.** Exit code 2 fires when a round's retries
are exhausted with *heterogeneous* failure signatures — e.g. attempt 1
crashes, attempt 2 stalls, attempt 3 makes no progress.  Mixed
failures are not strong evidence of a deterministic loop (they might
just be flaky infrastructure), so non-`--forever` still exits 2 and
`--forever` still skips to the next round.  Exit code 4 is reserved
for the *homogeneous* case — three attempts with the same signature,
which is the real signal that retrying further cannot help.  A
supervisor (systemd unit, `while true; do evolve start --forever;
done`, operator tmux loop) can distinguish the two and react
differently: restart cleanly on 4, alert-and-stop on 2 (or vice
versa, depending on deployment).

**What to do when you see exit 4.**
1. Check `runs/<session>/subprocess_error_round_*.txt` for the three
   most recent rounds — they will contain the same failure reason.
2. If the failure is in a pre-check command (`pytest`, `npm test`),
   fix the command or its environment before restarting.
3. If the failure is structural (the agent itself is broken), use
   `git log` to find the last round that committed a change, revert
   if needed, and restart.
4. A supervisor restart is safe only after root-cause remediation —
   evolve cannot break its own deterministic loop without human or
   scripted intervention.
