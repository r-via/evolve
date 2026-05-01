"""Tests for evolve.infrastructure.filesystem.state_manager improvement parsing — _is_needs_package, counters, _get_current_improvement."""

import textwrap
from pathlib import Path

from evolve.infrastructure.filesystem.improvement_parser import _count_blocked
from evolve.infrastructure.filesystem.improvement_parser import (
    _count_checked,
    _count_unchecked,
    _get_current_improvement,
    _is_needs_package,
)

# ---------------------------------------------------------------------------
# _is_needs_package
# ---------------------------------------------------------------------------

class TestIsNeedsPackage:
    def test_functional_needs_package(self):
        assert _is_needs_package("[functional] [needs-package] Install foo") is True

    def test_performance_needs_package(self):
        assert _is_needs_package("[performance] [needs-package] Add caching") is True

    def test_no_tag(self):
        assert _is_needs_package("[functional] Regular improvement") is False

    def test_needs_package_in_description_only(self):
        # [needs-package] appears in the body, not as a leading tag
        assert _is_needs_package("[functional] Mention [needs-package] in docs") is False

    def test_plain_text(self):
        assert _is_needs_package("just some text") is False


# ---------------------------------------------------------------------------
# Counter helpers
# ---------------------------------------------------------------------------

class TestCounters:
    def test_count_checked(self, tmp_path: Path):
        f = tmp_path / "improvements.md"
        f.write_text(textwrap.dedent("""\
            # Improvements
            - [x] done one
            - [ ] pending one
            - [x] done two
        """))
        assert _count_checked(f) == 2

    def test_count_unchecked(self, tmp_path: Path):
        f = tmp_path / "improvements.md"
        f.write_text(textwrap.dedent("""\
            # Improvements
            - [x] done
            - [ ] pending one
            - [ ] pending two
        """))
        assert _count_unchecked(f) == 2

    def test_count_checked_missing_file(self, tmp_path: Path):
        assert _count_checked(tmp_path / "nope.md") == 0

    def test_count_unchecked_missing_file(self, tmp_path: Path):
        assert _count_unchecked(tmp_path / "nope.md") == 0

    def test_count_blocked(self, tmp_path: Path):
        f = tmp_path / "improvements.md"
        f.write_text(textwrap.dedent("""\
            # Improvements
            - [x] [functional] done
            - [ ] [functional] [needs-package] blocked one
            - [ ] [functional] normal pending
            - [ ] [performance] [needs-package] blocked two
        """))
        assert _count_blocked(f) == 2

    def test_count_blocked_none(self, tmp_path: Path):
        f = tmp_path / "improvements.md"
        f.write_text("- [ ] [functional] normal\n")
        assert _count_blocked(f) == 0


# ---------------------------------------------------------------------------
# _get_current_improvement
# ---------------------------------------------------------------------------

class TestGetCurrentImprovement:
    def test_returns_first_unchecked(self, tmp_path: Path):
        f = tmp_path / "improvements.md"
        f.write_text(textwrap.dedent("""\
            - [x] done
            - [ ] [functional] first pending
            - [ ] [functional] second pending
        """))
        assert _get_current_improvement(f) == "[functional] first pending"

    def test_skips_needs_package_without_yolo(self, tmp_path: Path):
        f = tmp_path / "improvements.md"
        f.write_text(textwrap.dedent("""\
            - [ ] [functional] [needs-package] blocked
            - [ ] [functional] normal
        """))
        assert _get_current_improvement(f, allow_installs=False) == "[functional] normal"

    def test_returns_needs_package_with_yolo(self, tmp_path: Path):
        f = tmp_path / "improvements.md"
        f.write_text(textwrap.dedent("""\
            - [ ] [functional] [needs-package] blocked
            - [ ] [functional] normal
        """))
        result = _get_current_improvement(f, allow_installs=True)
        assert result == "[functional] [needs-package] blocked"

    def test_returns_none_when_all_done(self, tmp_path: Path):
        f = tmp_path / "improvements.md"
        f.write_text("- [x] done\n")
        assert _get_current_improvement(f) is None

    def test_returns_none_missing_file(self, tmp_path: Path):
        assert _get_current_improvement(tmp_path / "nope.md") is None

    def test_returns_none_all_blocked(self, tmp_path: Path):
        f = tmp_path / "improvements.md"
        f.write_text("- [ ] [functional] [needs-package] blocked\n")
        assert _get_current_improvement(f, allow_installs=False) is None


# ---------------------------------------------------------------------------
# Edge cases for improvements.md parsing
# ---------------------------------------------------------------------------

class TestImprovementsParsingEdgeCases:
    """Edge cases for parsing improvements.md: malformed syntax, mixed
    indentation, empty lines, special characters, and blocked counting."""

    # -- Malformed checkbox syntax --

    def test_count_checked_ignores_lowercase_x_variants(self, tmp_path: Path):
        """Only '- [x]' is matched; '- [X]' (uppercase) is not counted."""
        f = tmp_path / "improvements.md"
        f.write_text("- [X] uppercase\n- [x] lowercase\n")
        assert _count_checked(f) == 1

    def test_count_unchecked_ignores_extra_spaces_in_bracket(self, tmp_path: Path):
        """Only '- [ ]' is matched; '- [  ]' (double space) is not."""
        f = tmp_path / "improvements.md"
        f.write_text("- [  ] double space\n- [ ] single space\n")
        assert _count_unchecked(f) == 1

    def test_count_ignores_no_dash_prefix(self, tmp_path: Path):
        """Lines without '- ' prefix are not counted."""
        f = tmp_path / "improvements.md"
        f.write_text("[x] no dash\n[ ] also no dash\n- [x] valid\n")
        assert _count_checked(f) == 1
        assert _count_unchecked(f) == 0

    def test_count_ignores_star_checkbox(self, tmp_path: Path):
        """'* [x]' (star instead of dash) is not counted."""
        f = tmp_path / "improvements.md"
        f.write_text("* [x] star prefix\n- [x] dash prefix\n")
        assert _count_checked(f) == 1

    def test_get_current_ignores_malformed_checkboxes(self, tmp_path: Path):
        """_get_current_improvement skips lines without proper '- [ ] '."""
        f = tmp_path / "improvements.md"
        f.write_text("- [] no space\n-[ ] no space after dash\n- [ ] valid item\n")
        assert _get_current_improvement(f) == "valid item"

    # -- Mixed indentation --

    def test_count_checked_ignores_indented_lines(self, tmp_path: Path):
        """Indented checkboxes are not at line start, so not counted by regex."""
        f = tmp_path / "improvements.md"
        f.write_text("  - [x] indented\n- [x] not indented\n")
        assert _count_checked(f) == 1

    def test_count_unchecked_ignores_tab_indented(self, tmp_path: Path):
        """Tab-indented checkboxes are not at line start."""
        f = tmp_path / "improvements.md"
        f.write_text("\t- [ ] tab indented\n- [ ] not indented\n")
        assert _count_unchecked(f) == 1

    def test_get_current_handles_indented_items(self, tmp_path: Path):
        """_get_current_improvement strips lines, so indented items are found."""
        f = tmp_path / "improvements.md"
        f.write_text("- [x] done\n  - [ ] [functional] indented pending\n")
        assert _get_current_improvement(f) == "[functional] indented pending"

    # -- Empty lines between items --

    def test_count_with_empty_lines_between(self, tmp_path: Path):
        """Empty lines between items don't affect counting."""
        f = tmp_path / "improvements.md"
        f.write_text("- [x] done one\n\n\n- [ ] pending one\n\n- [x] done two\n")
        assert _count_checked(f) == 2
        assert _count_unchecked(f) == 1

    def test_get_current_with_empty_lines(self, tmp_path: Path):
        """Empty lines between items don't prevent finding pending."""
        f = tmp_path / "improvements.md"
        f.write_text("- [x] done\n\n\n- [ ] [functional] pending\n")
        assert _get_current_improvement(f) == "[functional] pending"

    def test_count_empty_file(self, tmp_path: Path):
        """Empty file returns zero for all counters."""
        f = tmp_path / "improvements.md"
        f.write_text("")
        assert _count_checked(f) == 0
        assert _count_unchecked(f) == 0
        assert _count_blocked(f) == 0

    def test_count_only_header(self, tmp_path: Path):
        """File with only a header line has zero counts."""
        f = tmp_path / "improvements.md"
        f.write_text("# Improvements\n")
        assert _count_checked(f) == 0
        assert _count_unchecked(f) == 0
        assert _count_blocked(f) == 0

    # -- Special characters in improvement text --

    def test_count_with_special_characters(self, tmp_path: Path):
        """Special chars (parens, quotes, backticks) in description are counted."""
        f = tmp_path / "improvements.md"
        f.write_text(
            '- [x] [functional] Add `_parse()` helper (see #42)\n'
            '- [ ] [functional] Fix "edge case" in <parser>\n'
        )
        assert _count_checked(f) == 1
        assert _count_unchecked(f) == 1

    def test_get_current_with_special_characters(self, tmp_path: Path):
        """Improvement text with special characters is returned verbatim."""
        f = tmp_path / "improvements.md"
        f.write_text('- [ ] [functional] Fix `_parse()` — handles "quoted" <args>\n')
        result = _get_current_improvement(f)
        assert result == '[functional] Fix `_parse()` — handles "quoted" <args>'

    def test_count_with_unicode(self, tmp_path: Path):
        """Unicode characters in descriptions are handled correctly."""
        f = tmp_path / "improvements.md"
        f.write_text("- [x] [functional] Add résumé support 🎉\n- [ ] [functional] Fix naïve parsing\n")
        assert _count_checked(f) == 1
        assert _count_unchecked(f) == 1

    # -- Blocked / needs-package counting edge cases --

    def test_count_blocked_ignores_checked_needs_package(self, tmp_path: Path):
        """Checked [needs-package] items are not counted as blocked."""
        f = tmp_path / "improvements.md"
        f.write_text(
            "- [x] [functional] [needs-package] already installed\n"
            "- [ ] [functional] [needs-package] still blocked\n"
        )
        assert _count_blocked(f) == 1

    def test_count_blocked_missing_file(self, tmp_path: Path):
        """Missing file returns 0 blocked."""
        assert _count_blocked(tmp_path / "nonexistent.md") == 0

    def test_count_blocked_needs_package_in_description(self, tmp_path: Path):
        """[needs-package] in description body (not tag position) is not blocked."""
        f = tmp_path / "improvements.md"
        f.write_text("- [ ] [functional] Mention [needs-package] in docs\n")
        assert _count_blocked(f) == 0

    def test_count_blocked_with_mixed_items(self, tmp_path: Path):
        """Mix of blocked, unblocked, and checked items counts correctly."""
        f = tmp_path / "improvements.md"
        f.write_text(textwrap.dedent("""\
            - [x] [functional] done
            - [ ] [functional] [needs-package] blocked 1
            - [ ] [performance] not blocked
            - [ ] [performance] [needs-package] blocked 2
            - [x] [functional] [needs-package] done and was blocked
            - [ ] [functional] also not blocked
        """))
        assert _count_blocked(f) == 2
        assert _count_checked(f) == 2
        assert _count_unchecked(f) == 4

    def test_get_current_skips_multiple_blocked(self, tmp_path: Path):
        """Multiple blocked items are all skipped without yolo."""
        f = tmp_path / "improvements.md"
        f.write_text(textwrap.dedent("""\
            - [ ] [functional] [needs-package] blocked 1
            - [ ] [performance] [needs-package] blocked 2
            - [ ] [functional] first available
        """))
        assert _get_current_improvement(f, allow_installs=False) == "[functional] first available"
        # With yolo, the first blocked item is returned
        assert _get_current_improvement(f, allow_installs=True) == "[functional] [needs-package] blocked 1"

    # -- Whitespace-only and newline-only files --

    def test_whitespace_only_file(self, tmp_path: Path):
        """File with only whitespace returns zero counts."""
        f = tmp_path / "improvements.md"
        f.write_text("   \n\n  \n")
        assert _count_checked(f) == 0
        assert _count_unchecked(f) == 0
        assert _count_blocked(f) == 0
        assert _get_current_improvement(f) is None
