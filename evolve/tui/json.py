"""Backward-compat shim — canonical location is evolve.interfaces.tui.json."""

import warnings as _w

_w.warn(
    "evolve.tui.json moved to evolve.interfaces.tui.json",
    DeprecationWarning,
    stacklevel=2,
)

from evolve.interfaces.tui.json import JsonTUI  # noqa: E402, F401

__all__ = ["JsonTUI"]
