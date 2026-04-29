"""Backward-compat shim — canonical location is evolve.interfaces.tui.rich."""

import warnings as _w

_w.warn(
    "evolve.tui.rich moved to evolve.interfaces.tui.rich",
    DeprecationWarning,
    stacklevel=2,
)

from evolve.interfaces.tui.rich import RichTUI  # noqa: E402, F401

__all__ = ["RichTUI"]
