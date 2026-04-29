"""Backward-compat shim — canonical location is evolve.interfaces.tui.plain."""

import warnings as _w

_w.warn(
    "evolve.tui.plain moved to evolve.interfaces.tui.plain",
    DeprecationWarning,
    stacklevel=2,
)

from evolve.interfaces.tui.plain import PlainTUI  # noqa: E402, F401

__all__ = ["PlainTUI"]
