"""Subprocess monitoring with watchdog-based stall detection.

Infrastructure layer — diagnostics bounded context.
"""

from __future__ import annotations

import subprocess
import sys
import threading
import time

__mod = __import__("evolve.tui", fromlist=["TUIProtocol"])
TUIProtocol = __mod.TUIProtocol

# Seconds of silence before the watchdog considers a subprocess stalled.
WATCHDOG_TIMEOUT = 120


def _run_monitored_subprocess(
    cmd: list[str],
    cwd: str,
    ui: TUIProtocol,
    round_num: int,
    watchdog_timeout: int = WATCHDOG_TIMEOUT,
) -> tuple[int, str, bool]:
    """Run a subprocess with real-time output streaming and stall detection."""
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
        nonlocal last_activity
        assert proc.stdout is not None
        for line in proc.stdout:
            with lock:
                output_lines.append(line)
                last_activity = time.monotonic()
            ui.subprocess_output(line)

    reader_thread = threading.Thread(target=_reader, daemon=True)
    reader_thread.start()

    _wait_interval = min(1.0, max(0.1, watchdog_timeout / 10.0))
    stalled = False
    while True:
        try:
            proc.wait(timeout=_wait_interval)
            break
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
