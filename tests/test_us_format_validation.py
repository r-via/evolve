"""Tests for US format validation — US-050.

Covers:
- _detect_us_format_violation: detection with malformed item, no-op with
  well-formed US, no-op when no new items
- US FORMAT VIOLATION: prefix handler in prompt_diagnostics.py
  build_prev_crash_section
- Integration: round_lifecycle imports _detect_us_format_violation
"""

from __future__ import annotations

from pathlib import Path

import pytest

from evolve.infrastructure.diagnostics.detector import _detect_us_format_violation


# ---------------------------------------------------------------------------
# _detect_us_format_violation tests
# ---------------------------------------------------------------------------

class TestDetectUsFormatViolation:
    """Tests for the _detect_us_format_violation helper."""

    def test_no_new_items_returns_empty(self, tmp_path: Path):
        """No new unchecked items → no violations."""
        imp = tmp_path / "improvements.md"
        imp.write_text("- [x] [functional] US-001: done\n")
        pre_lines = ["- [x] [functional] US-001: done"]
        assert _detect_us_format_violation(imp, pre_lines) == []

    def test_well_formed_us_returns_empty(self, tmp_path: Path):
        """A properly formatted new US item → no violations."""
        pre_lines = []
        imp = tmp_path / "improvements.md"
        imp.write_text(
            "- [ ] [functional] US-001: Add feature X\n"
            "  **As** an operator, **I want** X **so that** Y.\n"
            "  **Acceptance criteria (must all pass before the "
            "item is [x]'d):**\n"
            "  1. Criterion one\n"
            "  2. Criterion two\n"
            "  **Definition of done:**\n"
            "  - Artifact one\n"
        )
        result = _detect_us_format_violation(imp, pre_lines)
        assert result == []

    def test_malformed_header_detected(self, tmp_path: Path):
        """Item missing US-NNN header format → violation."""
        pre_lines = []
        imp = tmp_path / "improvements.md"
        imp.write_text("- [ ] [functional] Add something vague\n")
        result = _detect_us_format_violation(imp, pre_lines)
        assert len(result) == 1
        assert "malformed header" in result[0]

    def test_missing_sections_detected(self, tmp_path: Path):
        """Item with valid header but missing required sections."""
        pre_lines = []
        imp = tmp_path / "improvements.md"
        imp.write_text(
            "- [ ] [functional] US-001: Add feature X\n"
            "  Some description without required sections.\n"
        )
        result = _detect_us_format_violation(imp, pre_lines)
        assert len(result) == 1
        assert "missing required sections" in result[0]
        assert "**As**" in result[0]
        assert "**Acceptance criteria" in result[0]
        assert "**Definition of done" in result[0]

    def test_partially_missing_sections(self, tmp_path: Path):
        """Item with header and some but not all required sections."""
        pre_lines = []
        imp = tmp_path / "improvements.md"
        imp.write_text(
            "- [ ] [functional] US-002: Partial item\n"
            "  **As** an operator, **I want** X **so that** Y.\n"
            "  But no acceptance criteria or definition of done.\n"
        )
        result = _detect_us_format_violation(imp, pre_lines)
        assert len(result) == 1
        assert "**Acceptance criteria" in result[0]
        assert "**Definition of done" in result[0]
        # **As** should NOT be listed since it's present
        assert result[0].count("**As**") == 0

    def test_existing_items_not_flagged(self, tmp_path: Path):
        """Pre-existing items (even malformed) are not flagged."""
        bad_line = "- [ ] [functional] Add something vague\n"
        pre_lines = [bad_line.rstrip("\n")]
        imp = tmp_path / "improvements.md"
        imp.write_text(bad_line)
        result = _detect_us_format_violation(imp, pre_lines)
        assert result == []

    def test_missing_file_returns_empty(self, tmp_path: Path):
        """Missing improvements.md → no violations."""
        imp = tmp_path / "improvements.md"
        result = _detect_us_format_violation(imp, [])
        assert result == []

    def test_multiple_new_items_mixed(self, tmp_path: Path):
        """Multiple new items: one well-formed, one malformed."""
        pre_lines = []
        imp = tmp_path / "improvements.md"
        imp.write_text(
            "- [ ] [functional] US-001: Good item\n"
            "  **As** an operator, **I want** X **so that** Y.\n"
            "  **Acceptance criteria (must all pass):**\n"
            "  1. C1\n"
            "  **Definition of done:**\n"
            "  - D1\n"
            "- [ ] [functional] Bad item without US format\n"
        )
        result = _detect_us_format_violation(imp, pre_lines)
        assert len(result) == 1
        assert "malformed header" in result[0]

    def test_multi_tag_header_accepted(self, tmp_path: Path):
        """Header with multiple tags is valid."""
        pre_lines = []
        imp = tmp_path / "improvements.md"
        imp.write_text(
            "- [ ] [functional] [P1] US-050: Multi-tag item\n"
            "  **As** an operator, **I want** X **so that** Y.\n"
            "  **Acceptance criteria (must all pass):**\n"
            "  1. C1\n"
            "  **Definition of done:**\n"
            "  - D1\n"
        )
        result = _detect_us_format_violation(imp, pre_lines)
        assert result == []

    def test_body_extends_to_next_item(self, tmp_path: Path):
        """Body collection stops at the next `- [` line."""
        pre_lines = [
            "- [x] [functional] US-001: Done item",
        ]
        imp = tmp_path / "improvements.md"
        imp.write_text(
            "- [x] [functional] US-001: Done item\n"
            "- [ ] [functional] US-002: New item\n"
            "  **As** an operator, **I want** X **so that** Y.\n"
            "  **Acceptance criteria:**\n"
            "  1. C1\n"
            "  **Definition of done:**\n"
            "  - D1\n"
            "- [x] [functional] US-003: Another done\n"
        )
        result = _detect_us_format_violation(imp, pre_lines)
        assert result == []


# ---------------------------------------------------------------------------
# build_prev_crash_section US FORMAT VIOLATION prefix test
# ---------------------------------------------------------------------------

class TestBuildPrevCrashUsFormat:
    """Test the US FORMAT VIOLATION branch in build_prev_crash_section."""

    def test_us_format_violation_renders_section(self):
        from evolve.infrastructure.claude_sdk.prompt_diagnostics import build_prev_crash_section
        diag = (
            "US FORMAT VIOLATION: 1 item(s) lack required US "
            "template sections:\n  - Item at line 5 missing ..."
        )
        result = build_prev_crash_section(diag)
        assert "## CRITICAL" in result
        assert "US format violation" in result
        assert "**As**" in result
        assert "**Acceptance criteria" in result
        assert "**Definition of done" in result
        assert diag in result

    def test_us_format_violation_takes_priority_over_generic(self):
        """US FORMAT VIOLATION prefix is matched before the generic
        fallback."""
        from evolve.infrastructure.claude_sdk.prompt_diagnostics import build_prev_crash_section
        diag = "US FORMAT VIOLATION: test"
        result = build_prev_crash_section(diag)
        assert "CRASHED" not in result
        assert "US format violation" in result


# ---------------------------------------------------------------------------
# Integration: round_lifecycle imports _detect_us_format_violation
# ---------------------------------------------------------------------------

class TestRoundLifecycleIntegration:
    """Verify round_lifecycle.py uses _detect_us_format_violation."""

    def test_round_lifecycle_imports_detect_us_format_violation(self):
        src = (
            Path(__file__).resolve().parent.parent
            / "evolve" / "round_lifecycle.py"
        ).read_text()
        assert "_detect_us_format_violation" in src

    def test_orchestrator_re_exports_detect_us_format_violation(self):
        import evolve.application.run_loop as orch
        assert hasattr(orch, "_detect_us_format_violation")
        from evolve.infrastructure.diagnostics.detector import (
            _detect_us_format_violation as orig,
        )
        assert orch._detect_us_format_violation is orig
