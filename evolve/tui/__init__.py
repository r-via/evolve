"""Backward-compat shim — canonical location is evolve.interfaces.tui.

All symbols re-exported so ``from evolve.tui import …`` continues to work.
"""

import warnings as _w

_w.warn(
    "evolve.tui moved to evolve.interfaces.tui",
    DeprecationWarning,
    stacklevel=2,
)

from evolve.interfaces.tui import (  # noqa: E402, F401
    TUIProtocol,
    RichTUI,
    PlainTUI,
    JsonTUI,
    get_tui,
    _has_rich,
    _use_json,
    _CAIROSVG_MISSING_WARN,
)

__all__ = [
    "TUIProtocol",
    "RichTUI",
    "PlainTUI",
    "JsonTUI",
    "get_tui",
    "_has_rich",
    "_use_json",
    "_CAIROSVG_MISSING_WARN",
]
