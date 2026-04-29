"""Backward-compat shim — real code lives in evolve.application.

Round success handling migrated to the application layer.
"""

from evolve.application.run_loop_lifecycle import _handle_round_success

__all__ = ["_handle_round_success"]
