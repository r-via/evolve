"""evolve-watch — auto-restart wrapper for ``evolve start``.

Watches for exit code 3 (``RESTART_REQUIRED`` structural change — see
SPEC § "Structural change self-detection" and § "Exit codes") and
respawns evolve with ``--resume`` so the next session picks up where
the previous one left off.  Any other exit code is propagated.

Usage
-----

::

    evolve-watch start . --check pytest --forever
    evolve-watch start . --check pytest --rounds 100

The wrapper passes every CLI argument through to ``evolve`` unchanged
on the first invocation; on every structural restart it injects
``--resume`` (idempotently) and respawns.

Safety net
----------

If evolve exits with code 3 more than ``MAX_RESTARTS_PER_WINDOW``
times in a ``RESTART_WINDOW_SECONDS`` window, the watcher gives up
with exit code 5 — assuming the structural-change loop is itself
broken (a round writes ``RESTART_REQUIRED`` every time, which would
otherwise restart forever).  Defaults: 5 restarts per 30 minutes.
"""

from __future__ import annotations

import signal
import subprocess
import sys
import time
from collections import deque

#: Exit code evolve uses when it commits a structural change and the
#: orchestrator must be restarted to reload its own modules
#: (SPEC § "Exit codes").
STRUCTURAL_RESTART_EXIT = 3

#: Exit code the watcher itself returns when it gives up after too many
#: structural restarts in a short window.
WATCHER_GIVE_UP_EXIT = 5

#: Maximum number of structural restarts allowed within
#: ``RESTART_WINDOW_SECONDS`` before the watcher bails.
MAX_RESTARTS_PER_WINDOW = 5

#: Sliding-window length (seconds) for the restart-rate safety net.
RESTART_WINDOW_SECONDS = 1800  # 30 minutes

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
    auto-restart on structural exits."""
    argv = list(argv if argv is not None else sys.argv[1:])
    if not argv:
        print(
            "usage: evolve-watch <evolve-args>\n"
            "example: evolve-watch start . --check pytest --forever",
            file=sys.stderr,
        )
        sys.exit(2)

    restart_history: deque[float] = deque()
    current_args = argv
    child: subprocess.Popen | None = None

    def _forward(sig: int, _frame: object) -> None:
        if child is not None and child.poll() is None:
            child.send_signal(sig)

    signal.signal(signal.SIGINT, _forward)
    signal.signal(signal.SIGTERM, _forward)

    while True:
        _log(f"spawning: evolve {' '.join(current_args)}")
        child = _spawn_evolve(current_args)
        try:
            rc = child.wait()
        except KeyboardInterrupt:
            # SIGINT was forwarded to child — wait for it to settle.
            try:
                rc = child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                child.kill()
                rc = child.wait()

        if rc != STRUCTURAL_RESTART_EXIT:
            _log(f"evolve exited with code {rc} — propagating")
            sys.exit(rc)

        now = time.time()
        restart_history.append(now)
        while restart_history and now - restart_history[0] > RESTART_WINDOW_SECONDS:
            restart_history.popleft()

        if len(restart_history) > MAX_RESTARTS_PER_WINDOW:
            _log(
                f"evolve restarted {len(restart_history)} times in "
                f"{RESTART_WINDOW_SECONDS // 60} min — giving up to "
                f"avoid an infinite loop (every round writes "
                f"RESTART_REQUIRED).  Inspect "
                f".evolve/runs/<latest>/RESTART_REQUIRED for the "
                f"structural reason; manual fix required."
            )
            sys.exit(WATCHER_GIVE_UP_EXIT)

        _log(
            f"structural change detected (exit 3) — restart "
            f"{len(restart_history)}/{MAX_RESTARTS_PER_WINDOW} in "
            f"window, respawning with --resume"
        )
        current_args = _add_resume(current_args)
        # Brief breathing room: let the OS release file handles, let
        # any lingering subprocess from the previous round settle, and
        # avoid hammering the SDK with back-to-back spawns.
        time.sleep(1)


if __name__ == "__main__":
    main()
