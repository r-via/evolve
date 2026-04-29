"""Backward-compat shim — real code lives in evolve.application.

Session startup entry point migrated to the application layer.
"""

from evolve.application.run_loop_startup import evolve_loop

__all__ = ["evolve_loop"]
