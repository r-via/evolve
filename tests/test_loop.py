"""Tests for loop.py — _is_needs_package, counters, _get_current_improvement."""

import textwrap
from pathlib import Path

from loop import (
    _is_needs_package,
    _count_checked,
    _count_unchecked,
    _count_blocked,
    _get_current_improvement,
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
        assert _get_current_improvement(f, yolo=False) == "[functional] normal"

    def test_returns_needs_package_with_yolo(self, tmp_path: Path):
        f = tmp_path / "improvements.md"
        f.write_text(textwrap.dedent("""\
            - [ ] [functional] [needs-package] blocked
            - [ ] [functional] normal
        """))
        result = _get_current_improvement(f, yolo=True)
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
        assert _get_current_improvement(f, yolo=False) is None
