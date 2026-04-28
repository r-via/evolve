"""Backward-compat shim — real code in evolve.infrastructure.reporting.generator.

See SPEC.md § "Source code layout — DDD", migration step 13.
"""

from evolve.infrastructure.reporting import _generate_evolution_report

__all__ = ["_generate_evolution_report"]
