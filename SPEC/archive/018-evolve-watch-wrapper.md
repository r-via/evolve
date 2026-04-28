# evolve-watch Auto-Restart Wrapper (archived from SPEC.md)

*Archived: 2026-04-28 — stable wrapper mechanism, fully implemented*

---

## evolve-watch auto-restart wrapper

``evolve-watch`` is a relentless external supervisor that wraps
``evolve start`` and respawns it on **every** non-zero exit until
the project converges.  It is the canonical way to run evolve
unattended (overnight, ``--forever`` sessions, CI nightlies):
without it, every ``RESTART_REQUIRED`` marker (exit 3),
``--rounds`` boundary (exit 1), transient error (exit 2), and
circuit-breaker trip (exit 4) forces an operator to re-issue the
command manually, which defeats long-running self-evolution.  The
wrapper is shipped as a separate entry point so it is
**out-of-process**: it survives the orchestrator respawn even
when the structural change moves or renames the ``evolve``
package's own files.

**Usage.**  Drop-in replacement for ``evolve``:

```bash
# Forever-run with auto-restart until convergence
evolve-watch start . --check pytest --forever

# Bounded-rounds run (the wrapper restarts past each round budget
# until convergence — exit 1 is just another restart trigger)
evolve-watch start . --check pytest --rounds 100
```

Every CLI argument is forwarded to ``evolve`` unchanged on the
first invocation.  On every non-zero exit the wrapper injects
``--resume`` (idempotently — already-present ``--resume`` is not
duplicated) and respawns ``python -m evolve <args>``.

**Stop conditions — exactly two.**  The wrapper exits in only
these cases:

1. **Convergence (exit 0).**  ``evolve`` reports the project
   matches the spec.  The wrapper propagates 0 and stops.
2. **Operator signal.**  ``SIGINT`` (Ctrl+C) or ``SIGTERM``
   received by the wrapper is forwarded to the running ``evolve``
   child; the wrapper waits up to 10s for clean exit (then
   ``SIGKILL``s), and propagates the child's exit code without
   restarting.  This is how the operator interrupts an unattended
   session.

**No restart cap by design.**  Earlier versions of the wrapper
shipped with a sliding-window cap (5 restarts per 30 minutes,
exit 5 on cap hit).  That cap was removed because it conflicted
with the wrapper's stated purpose: long unattended self-evolution
runs that survive structural commits, hit ``--rounds`` budgets,
encounter transient SDK errors, and even trip orchestrator
circuit-breakers — *all of which* the operator wants the wrapper
to recover from.  Deterministic-failure-loop detection is the
orchestrator's circuit-breaker territory (SS "Circuit breakers"),
not the wrapper's.  An operator who needs a bounded version
simply runs plain ``evolve`` without the wrapper.

**stderr-only logging.**  All wrapper messages
(``[evolve-watch <ts>] ...``) go to stderr, never stdout.  This
keeps ``evolve``'s ``--json`` mode parseable when piped:

```bash
evolve-watch start . --check pytest --json > evolve-output.jsonl
# stderr carries the wrapper events; stdout stays pure JSONL
```

**Implementation.**  Single self-contained module
(``evolve/watcher.py``, ~120 lines), no dependencies beyond
stdlib, entry point ``evolve-watch`` registered in
``pyproject.toml``.  Installed automatically by
``pip install -e .`` alongside the main ``evolve`` command.
