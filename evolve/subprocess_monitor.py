"""Subprocess monitoring with watchdog-based stall detection.

Extracted from ``evolve/orchestrator.py`` (US-043) to satisfy the
SPEC § "Hard rule: source files MUST NOT exceed 500 lines" cap.

This is a leaf module: imports only stdlib + ``evolve.tui`` at the
top level.  ``evolve/orchestrator.py`` re-exports ``WATCHDOG_TIMEOUT``
and ``_run_monitored_subprocess`` so existing
``patch("evolve.orchestrator._run_monitored_subprocess")`` test
targets and ``from evolve.orchestrator import WATCHDOG_TIMEOUT``
sibling-module imports continue to work unchanged.
"""

from __future__ import annotations

import subprocess
import sys
import threading
import time

from evolve.tui import TUIProtocol


# Seconds of silence before the watchdog considers a subprocess stalled.
WATCHDOG_TIMEOUT = 120


def _run_monitored_subprocess(
    cmd: list[str],
    cwd: str,
    ui: TUIProtocol,
    round_num: int,
    watchdog_timeout: int = WATCHDOG_TIMEOUT,
) -> tuple[int, str, bool]:
    """Run a subprocess with real-time output streaming and stall detection.

    Spawns the command, streams stdout in real-time, and monitors for
    inactivity.  If no output is produced for ``watchdog_timeout`` seconds
    the process is killed.

    Args:
        cmd: Command list to execute.
        cwd: Working directory for the subprocess.
        ui: TUI instance for status messages.
        round_num: Current round number (for diagnostic messages).
        watchdog_timeout: Seconds of silence before killing the process.

    Returns:
        A tuple ``(returncode, output, stalled)`` where *stalled* is True
        when the watchdog killed the process due to inactivity.
    """
    # -u ensures Python doesn't buffer stdout/stderr in the child process.
    if cmd[0] == sys.executable and "-u" not in cmd:
        cmd = [cmd[0], "-u"] + cmd[1:]

    proc = subprocess.Popen(
        cmd, cwd=cwd,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, bufsize=1,
    )

    output_lines: list[str] = []
    last_activity = time.monotonic()
    lock = threading.Lock()

    def _reader():
        """Read subprocess stdout line-by-line, updating the watchdog timer.

        Runs in a daemon thread.  Each line is appended to *output_lines*
        (under *lock*) and echoed to ``sys.stdout`` so the orchestrator's
        watchdog sees continuous activity.  Updates *last_activity* on every
        line to prevent the watchdog from killing an active process.
        """
        nonlocal last_activity
        assert proc.stdout is not None
        for line in proc.stdout:
            with lock:
                output_lines.append(line)
                last_activity = time.monotonic()
            # Route through the TUI so (a) subprocess output lands in the
            # Rich record buffer for frame capture, and (b) JsonTUI emits
            # a structured event per line. RichTUI preserves ANSI codes via
            # console.out(markup=False, highlight=False); PlainTUI falls
            # back to sys.stdout.write for parity with the old behavior.
            ui.subprocess_output(line)

    reader_thread = threading.Thread(target=_reader, daemon=True)
    reader_thread.start()

    # Check-in interval scales with ``watchdog_timeout`` — a 2-second
    # test watchdog wakes up every 200ms while a 120-second production
    # watchdog checks in every 1s.  We use ``proc.wait(timeout=...)``
    # rather than ``time.sleep + poll``: wait() returns *immediately*
    # when the subprocess exits (saving up to one interval per call)
    # and raises ``TimeoutExpired`` only when the process is still
    # alive at the deadline — at which point we check the silence
    # watchdog.  Capped at 1.0s so CPU overhead stays negligible.
    _wait_interval = min(1.0, max(0.1, watchdog_timeout / 10.0))
    stalled = False
    while True:
        try:
            proc.wait(timeout=_wait_interval)
            break  # subprocess exited cleanly
        except subprocess.TimeoutExpired:
            with lock:
                idle = time.monotonic() - last_activity
            if idle > watchdog_timeout:
                stalled = True
                ui.warn(
                    f"Round {round_num} stalled ({int(idle)}s without output) "
                    "— killing subprocess"
                )
                proc.kill()
                break

    reader_thread.join(timeout=5)
    output = "".join(output_lines)
    rc = proc.returncode if proc.returncode is not None else -9
    return rc, output, stalled
