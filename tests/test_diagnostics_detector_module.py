"""Module-level tests for evolve.infrastructure.diagnostics.detector.

Verifies the DDD migration (US-066): all symbols importable from the
infrastructure module, re-export identity preserved via shim, and
leaf-module invariant (no evolve.agent/orchestrator/cli top-level imports).
"""

from pathlib import Path

import evolve.diagnostics
import evolve.infrastructure.diagnostics


# All symbols that should be importable from the infrastructure module
_EXPECTED_SYMBOLS = [
    "MAX_IDENTICAL_FAILURES",
    "_auto_detect_check",
    "_check_review_verdict",
    "_DDD_ALLOWED",
    "_DDD_LAYERS",
    "_DEFAULT_README_STALE_THRESHOLD_DAYS",
    "_detect_file_too_large",
    "_detect_layering_violation",
    "_detect_tdd_violation",
    "_detect_us_format_violation",
    "_emit_stale_readme_advisory",
    "_failure_signature",
    "_FILE_TOO_LARGE_LIMIT",
    "_is_circuit_breaker_tripped",
    "_README_STALE_ADVISORY_FMT",
    "_save_subprocess_diagnostic",
    "_US_HEADER_RE",
    "_US_REQUIRED_SECTIONS",
]


def test_all_symbols_importable_from_infrastructure():
    """Each extracted symbol is importable from evolve.infrastructure.diagnostics."""
    for name in _EXPECTED_SYMBOLS:
        assert hasattr(evolve.infrastructure.diagnostics, name), (
            f"{name} not importable from evolve.infrastructure.diagnostics"
        )


def test_all_symbols_importable_from_detector():
    """Each extracted symbol is importable from the detector module directly."""
    from evolve.infrastructure.diagnostics import detector
    for name in _EXPECTED_SYMBOLS:
        assert hasattr(detector, name), (
            f"{name} not importable from evolve.infrastructure.diagnostics.detector"
        )


def test_re_export_identity():
    """is-equality between evolve.diagnostics.X and evolve.infrastructure.diagnostics.X."""
    for name in _EXPECTED_SYMBOLS:
        shim_obj = getattr(evolve.diagnostics, name)
        infra_obj = getattr(evolve.infrastructure.diagnostics, name)
        assert shim_obj is infra_obj, (
            f"Identity mismatch for {name}: "
            f"evolve.diagnostics.{name} is not evolve.infrastructure.diagnostics.{name}"
        )


def test_detector_no_forbidden_top_level_imports():
    """detector.py source has no from evolve.agent/orchestrator/cli/oneshot top-level imports."""
    src = Path(__file__).resolve().parent.parent / "evolve" / "infrastructure" / "diagnostics" / "detector.py"
    assert src.is_file(), f"detector.py not found at {src}"
    content = src.read_text()
    import re
    # Check for forbidden top-level imports (not indented = column 0)
    for line in content.splitlines():
        if line.startswith("from evolve."):
            # Only evolve.domain and evolve.infrastructure are allowed
            # for DDD infrastructure layer — but this module should have NONE
            assert False, (
                f"detector.py has forbidden top-level import: {line.strip()}"
            )
        if line.startswith("import evolve."):
            assert False, (
                f"detector.py has forbidden top-level import: {line.strip()}"
            )


def test_detector_file_under_500_lines():
    """detector.py stays under the 500-line cap."""
    src = Path(__file__).resolve().parent.parent / "evolve" / "infrastructure" / "diagnostics" / "detector.py"
    line_count = len(src.read_text().splitlines())
    assert line_count <= 500, f"detector.py has {line_count} lines (cap: 500)"


def test_shim_file_under_500_lines():
    """evolve/diagnostics.py shim stays under the 500-line cap."""
    src = Path(__file__).resolve().parent.parent / "evolve" / "diagnostics.py"
    line_count = len(src.read_text().splitlines())
    assert line_count <= 500, f"diagnostics.py shim has {line_count} lines (cap: 500)"
