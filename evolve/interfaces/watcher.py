"""evolve-watch — relentless auto-restart wrapper for ``evolve start``.

Wraps ``evolve start`` and respawns it on **any** non-zero exit code
until the project converges (exit 0).  There is no restart cap —
operator-issued ``SIGINT``/``SIGTERM`` is the only way to stop the
loop short of a successful convergence.

Use cases
---------

- ``--forever`` runs that should self-heal across structural commits
  (exit 3), max-rounds boundaries (exit 1), transient errors (exit 2),
  and circuit-breaker trips (exit 4).
- Long unattended sessions (overnight, CI nightly) where the operator
  wants a single command that "runs until done".

Usage
-----

::

    evolve-watch start . --check pytest --forever
    evolve-watch start . --check pytest --rounds 100

Every CLI argument is forwarded to ``evolve`` unchanged on the first
invocation; on every restart the wrapper injects ``--resume``
(idempotently) so the next session picks up the previous one's state.

Stop conditions
---------------

The wrapper exits in **only** two cases:

1. ``evolve`` exits with code 0 — convergence reached, the wrapper
   propagates 0 and stops.
2. The operator sends ``SIGINT`` (Ctrl+C) or ``SIGTERM`` to the
   wrapper — the signal is forwarded to the running ``evolve``
   child, the wrapper waits up to 10s for the child to settle (then
   ``SIGKILL``s if needed), and propagates the child's exit code
   without restarting.

Every other exit code (1, 2, 3, 4, anything else) triggers a restart.
There is no rate-limit cap by design: the operator explicitly chose a
relentless wrapper, and a deterministic-failure loop is the
orchestrator's circuit-breaker territory (§ "Circuit breakers"), not
the wrapper's.  If you need a bounded version, run plain ``evolve``
without the wrapper.
"""

from __future__ import annotations

import signal
import subprocess
import sys
import time

#: Convergence is the only natural stop condition for the watcher.
CONVERGED_EXIT = 0

#: Subcommands of evolve that accept ``--resume``.  The watcher injects
#: ``--resume`` immediately after the subcommand token so it lands in
#: the correct argparse subparser.
RESUMABLE_SUBCOMMANDS = ("start", "_round")


def _add_resume(args: list[str]) -> list[str]:
    """Return *args* with ``--resume`` injected after the subcommand.

    Idempotent: if ``--resume`` is already in *args*, returns unchanged.
    If no recognised subcommand is found, appends ``--resume`` at the
    end (best-effort fallback).
    """
    if "--resume" in args:
        return list(args)
    out = list(args)
    for i, tok in enumerate(out):
        if tok in RESUMABLE_SUBCOMMANDS:
            return out[: i + 1] + ["--resume"] + out[i + 1 :]
    return out + ["--resume"]


def _spawn_evolve(args: list[str]) -> subprocess.Popen:
    """Spawn ``python -m evolve <args>`` inheriting the watcher's stdio."""
    cmd = [sys.executable, "-m", "evolve"] + list(args)
    return subprocess.Popen(cmd)


def _log(msg: str) -> None:
    """Emit a timestamped line on stderr — never stdout, so evolve's
    own ``--json`` mode stays parseable."""
    ts = time.strftime("%Y-%m-%dT%H:%M:%S")
    print(f"[evolve-watch {ts}] {msg}", file=sys.stderr, flush=True)


def main(argv: list[str] | None = None) -> None:
    """Entry point — pass every CLI arg through to ``evolve`` and
    restart on every non-zero exit until convergence (exit 0)."""
    argv = list(argv if argv is not None else sys.argv[1:])
    if not argv:
        print(
            "usage: evolve-watch <evolve-args>\n"
            "example: evolve-watch start . --check pytest --forever",
            file=sys.stderr,
        )
        sys.exit(2)

    current_args = argv
    child: subprocess.Popen | None = None
    operator_signaled = False

    def _forward(sig: int, _frame: object) -> None:
        nonlocal operator_signaled
        operator_signaled = True
        if child is not None and child.poll() is None:
            child.send_signal(sig)

    signal.signal(signal.SIGINT, _forward)
    signal.signal(signal.SIGTERM, _forward)

    restart_count = 0
    while True:
        if restart_count == 0:
            _log(f"spawning: evolve {' '.join(current_args)}")
        else:
            _log(
                f"restart #{restart_count} — respawning with --resume "
                f"after non-zero exit"
            )
        child = _spawn_evolve(current_args)
        try:
            rc = child.wait()
        except KeyboardInterrupt:
            # Defensive: the signal handler should have set the flag
            # and forwarded the signal already, but Python may also
            # raise KeyboardInterrupt here on the main thread.
            operator_signaled = True
            try:
                rc = child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                child.kill()
                rc = child.wait()

        if operator_signaled:
            _log(
                f"operator signal received — evolve exited with code "
                f"{rc}, propagating without restart"
            )
            sys.exit(rc)

        if rc == CONVERGED_EXIT:
            _log("evolve converged (exit 0) — stopping watcher")
            sys.exit(CONVERGED_EXIT)

        restart_count += 1
        _log(
            f"evolve exited with code {rc} — restarting (no cap, only "
            f"convergence stops the watcher)"
        )
        current_args = _add_resume(current_args)
        # Brief breathing room: let the OS release file handles, let
        # any lingering subprocess from the previous round settle, and
        # avoid hammering the SDK with back-to-back spawns.
        time.sleep(1)


if __name__ == "__main__":
    main()
