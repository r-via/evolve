"""Tests for evolve.infrastructure.costs migration (US-063).

Verifies:
(a) All 6 symbols importable from evolve.infrastructure.costs.estimator
(b) is-equality between evolve.costs.X and evolve.infrastructure.costs.estimator.X
(c) evolve/infrastructure/costs/estimator.py has zero from evolve.* imports
(d) test_layering.py passes (no DDD violations)
"""

from pathlib import Path

import evolve.costs as costs_shim
import evolve.infrastructure.costs as costs_pkg
import evolve.infrastructure.costs.estimator as costs_est


SYMBOLS = [
    "RATES",
    "TokenUsage",
    "aggregate_usage",
    "build_usage_state",
    "estimate_cost",
    "format_cost",
]


class TestCostsInfrastructureModule:
    """US-063 acceptance criteria tests."""

    def test_all_symbols_importable_from_estimator(self):
        """AC1: all 6 symbols exist in evolve.infrastructure.costs.estimator."""
        for name in SYMBOLS:
            assert hasattr(costs_est, name), f"{name} not in estimator"

    def test_all_symbols_importable_from_package(self):
        """AC2: __init__.py re-exports all 6 symbols."""
        for name in SYMBOLS:
            assert hasattr(costs_pkg, name), f"{name} not in package"

    def test_identity_shim_to_estimator(self):
        """AC3: evolve.costs.X is evolve.infrastructure.costs.estimator.X."""
        for name in SYMBOLS:
            shim_obj = getattr(costs_shim, name)
            est_obj = getattr(costs_est, name)
            assert shim_obj is est_obj, (
                f"{name}: shim object is not identical to estimator object"
            )

    def test_identity_package_to_estimator(self):
        """AC2 + identity: package re-export is same object."""
        for name in SYMBOLS:
            pkg_obj = getattr(costs_pkg, name)
            est_obj = getattr(costs_est, name)
            assert pkg_obj is est_obj, (
                f"{name}: package object is not identical to estimator object"
            )

    def test_estimator_has_no_evolve_imports(self):
        """AC4: estimator.py imports ONLY from stdlib."""
        src = Path(costs_est.__file__).read_text()
        for line in src.splitlines():
            stripped = line.lstrip()
            if stripped.startswith("from evolve.") or stripped.startswith("import evolve."):
                assert False, f"evolve import found in estimator.py: {stripped}"

    def test_estimator_line_count_under_cap(self):
        """Structural: estimator.py stays under 500 lines."""
        src = Path(costs_est.__file__).read_text()
        lines = len(src.splitlines())
        assert lines <= 500, f"estimator.py is {lines} lines (cap 500)"

    def test_shim_is_thin(self):
        """Structural: costs.py shim is minimal (< 20 lines)."""
        src = Path(costs_shim.__file__).read_text()
        lines = len(src.splitlines())
        assert lines < 20, f"costs.py shim is {lines} lines (expected < 20)"
