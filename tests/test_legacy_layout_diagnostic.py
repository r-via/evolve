"""Tests for LEGACY LAYOUT NOT EMPTY diagnostic — US-088.

Covers:
- Detection with a synthetic unmigrated file
- No-op with all-shim layout
- Prompt rendering of ``## CRITICAL — DDD migration not complete`` header
"""

from __future__ import annotations

import ast
import textwrap
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# 1. Detection helper — _detect_legacy_layout_violation
# ---------------------------------------------------------------------------


class TestDetectLegacyLayoutViolation:
    """Test ``_detect_legacy_layout_violation`` in diagnostics detector."""

    def test_detects_unmigrated_file(self, tmp_path: Path) -> None:
        """A file with FunctionDef at top level is flagged."""
        from evolve.diagnostics import _detect_legacy_layout_violation

        evolve_dir = tmp_path / "evolve"
        evolve_dir.mkdir()
        # __init__.py — whitelisted
        (evolve_dir / "__init__.py").write_text("# package\n")
        # __main__.py — whitelisted
        (evolve_dir / "__main__.py").write_text("# entry\n")
        # pure shim — should pass
        (evolve_dir / "costs.py").write_text(
            '"""Shim."""\nfrom evolve.infrastructure.costs import estimate_cost\n'
        )
        # unmigrated production — should FAIL
        (evolve_dir / "agent.py").write_text(
            '"""Agent module."""\n\ndef analyze():\n    pass\n'
        )

        violations = _detect_legacy_layout_violation(tmp_path)
        assert len(violations) >= 1
        # Check structure: (filename, node_kind, line_number)
        filenames = [v[0] for v in violations]
        assert "agent.py" in filenames
        # Should not flag the shim
        assert "costs.py" not in filenames
        # Check the offending node kind
        agent_viol = [v for v in violations if v[0] == "agent.py"][0]
        assert agent_viol[1] == "FunctionDef"
        assert isinstance(agent_viol[2], int)

    def test_noop_with_all_shims(self, tmp_path: Path) -> None:
        """All pure shims → empty list."""
        from evolve.diagnostics import _detect_legacy_layout_violation

        evolve_dir = tmp_path / "evolve"
        evolve_dir.mkdir()
        (evolve_dir / "__init__.py").write_text("# package\n")
        (evolve_dir / "costs.py").write_text(
            '"""Shim."""\nfrom evolve.infrastructure.costs import estimate_cost\n'
        )
        (evolve_dir / "hooks.py").write_text(
            'from evolve.infrastructure.hooks import load_hooks\n'
        )

        violations = _detect_legacy_layout_violation(tmp_path)
        assert violations == []

    def test_detects_class_def(self, tmp_path: Path) -> None:
        """ClassDef at top level is also flagged."""
        from evolve.diagnostics import _detect_legacy_layout_violation

        evolve_dir = tmp_path / "evolve"
        evolve_dir.mkdir()
        (evolve_dir / "__init__.py").write_text("")
        (evolve_dir / "foo.py").write_text("class Foo:\n    pass\n")

        violations = _detect_legacy_layout_violation(tmp_path)
        assert len(violations) == 1
        assert violations[0][0] == "foo.py"
        assert violations[0][1] == "ClassDef"

    def test_whitelists_init_and_main(self, tmp_path: Path) -> None:
        """__init__.py and __main__.py are never scanned."""
        from evolve.diagnostics import _detect_legacy_layout_violation

        evolve_dir = tmp_path / "evolve"
        evolve_dir.mkdir()
        # Even if these have production code, they're whitelisted
        (evolve_dir / "__init__.py").write_text("def foo(): pass\n")
        (evolve_dir / "__main__.py").write_text("def main(): pass\n")

        violations = _detect_legacy_layout_violation(tmp_path)
        assert violations == []

    def test_allows_warnings_warn_in_shim(self, tmp_path: Path) -> None:
        """Shims with warnings.warn() calls are allowed."""
        from evolve.diagnostics import _detect_legacy_layout_violation

        evolve_dir = tmp_path / "evolve"
        evolve_dir.mkdir()
        (evolve_dir / "__init__.py").write_text("")
        (evolve_dir / "old.py").write_text(textwrap.dedent("""\
            \"\"\"Backward-compat shim.\"\"\"
            import warnings
            from evolve.new_module import something
            warnings.warn("moved", DeprecationWarning, stacklevel=2)
        """))

        violations = _detect_legacy_layout_violation(tmp_path)
        assert violations == []

    def test_no_evolve_dir(self, tmp_path: Path) -> None:
        """Missing evolve/ dir → empty list (no crash)."""
        from evolve.diagnostics import _detect_legacy_layout_violation

        violations = _detect_legacy_layout_violation(tmp_path)
        assert violations == []


# ---------------------------------------------------------------------------
# 2. Prompt rendering — build_prev_crash_section
# ---------------------------------------------------------------------------


class TestLegacyLayoutPromptRendering:
    """Test that ``build_prev_crash_section`` handles LEGACY LAYOUT prefix."""

    def test_renders_critical_header(self) -> None:
        """LEGACY LAYOUT NOT EMPTY prefix → correct header."""
        from evolve.prompt_diagnostics import build_prev_crash_section

        diag = (
            "LEGACY LAYOUT NOT EMPTY: 2 file(s) at evolve/ top level "
            "still contain production code: agent.py, cli.py"
        )
        result = build_prev_crash_section(diag)
        assert "## CRITICAL" in result
        assert "DDD migration not complete" in result
        assert "smallest first" in result
        assert diag in result

    def test_does_not_match_other_prefix(self) -> None:
        """Other prefixes don't trigger legacy layout handler."""
        from evolve.prompt_diagnostics import build_prev_crash_section

        result = build_prev_crash_section("FILE TOO LARGE: foo.py 600 lines")
        assert "DDD migration not complete" not in result


# ---------------------------------------------------------------------------
# 3. Integration — round_success.py calls detection + writes diagnostic
# ---------------------------------------------------------------------------


class TestLegacyLayoutIntegration:
    """Test integration in ``_handle_round_success`` pipeline."""

    def test_detect_legacy_layout_violation_importable_from_diagnostics(
        self,
    ) -> None:
        """The helper is importable from the evolve.diagnostics shim."""
        from evolve.diagnostics import _detect_legacy_layout_violation

        assert callable(_detect_legacy_layout_violation)

    def test_detect_legacy_layout_violation_importable_from_infrastructure(
        self,
    ) -> None:
        """The helper is importable from the infrastructure module."""
        from evolve.infrastructure.diagnostics import (
            _detect_legacy_layout_violation,
        )

        assert callable(_detect_legacy_layout_violation)

    def test_is_identity_across_shim(self) -> None:
        """Same object via shim and infrastructure."""
        from evolve.diagnostics import (
            _detect_legacy_layout_violation as via_shim,
        )
        from evolve.infrastructure.diagnostics import (
            _detect_legacy_layout_violation as via_infra,
        )

        assert via_shim is via_infra
