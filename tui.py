"""Backward-compatibility shim — imports from ``evolve.tui``.

.. deprecated::
    Import from ``evolve.tui`` instead.  This shim will be removed in a
    future version.
"""

import warnings as _warnings

_warnings.warn(
    "Importing from the root 'tui' module is deprecated. "
    "Use 'from evolve.tui import ...' instead.",
    DeprecationWarning,
    stacklevel=2,
)

from evolve.tui import (  # noqa: F401, E402
    TUIProtocol,
    RichTUI,
    PlainTUI,
    JsonTUI,
    get_tui,
    _has_rich,
    _CAIROSVG_MISSING_WARN,
)
from evolve.tui import _use_json  # noqa: F401, E402

# Re-export the module-level flag so ``tui._use_json = True`` still works.
# Because ``_use_json`` is a simple bool, the import above copies the value
# rather than binding to the original. Callers that SET ``tui._use_json``
# (the orchestrator does ``import tui as _tui_mod; _tui_mod._use_json = True``)
# need that assignment to propagate to ``evolve.tui._use_json``.  We achieve
# this by making *this* module's namespace delegate attribute writes to the
# canonical module:
import evolve.tui as _canonical_tui  # noqa: E402
import sys as _sys  # noqa: E402


class _ShimModule(_sys.modules[__name__].__class__):
    """Module subclass that forwards ``_use_json`` writes to ``evolve.tui``."""

    def __setattr__(self, name, value):
        if name == "_use_json":
            _canonical_tui._use_json = value
        super().__setattr__(name, value)


_sys.modules[__name__].__class__ = _ShimModule
