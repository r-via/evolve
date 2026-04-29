import subprocess
import sys
from pathlib import Path

"""Backward-compat shim — real code lives in evolve.application.update.

All symbols re-exported so existing ``from evolve.updater import run_update``
etc. continue to work unchanged.
"""

from evolve.application.update import (
    run_update,
    _run,
    _ACTIVE_STATUSES,
    _default_ref,
    _detect_active_session,
    _detect_install_location,
    _git_can_fast_forward,
    _git_dirty,
)

__all__ = [
    "_ACTIVE_STATUSES",
    "_default_ref",
    "_detect_active_session",
    "_detect_install_location",
    "_git_can_fast_forward",
    "_git_dirty",
    "run_update",
    "_run",
]
