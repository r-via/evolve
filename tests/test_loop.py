"""Tests for loop.py — _is_needs_package, counters, _get_current_improvement."""

import subprocess
import textwrap
from pathlib import Path
from unittest.mock import patch, MagicMock

from loop import (
    _is_needs_package,
    _count_checked,
    _count_unchecked,
    _count_blocked,
    _get_current_improvement,
    _setup_forever_branch,
    _forever_restart,
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


# ---------------------------------------------------------------------------
# _setup_forever_branch
# ---------------------------------------------------------------------------

class TestSetupForeverBranch:
    def _init_git(self, path: Path):
        """Initialize a git repo with an initial commit."""
        subprocess.run(["git", "init"], cwd=str(path), capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=str(path), capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=str(path), capture_output=True)
        (path / "README.md").write_text("# Test\n")
        subprocess.run(["git", "add", "-A"], cwd=str(path), capture_output=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=str(path), capture_output=True)

    def test_creates_branch(self, tmp_path: Path):
        self._init_git(tmp_path)
        _setup_forever_branch(tmp_path)
        result = subprocess.run(
            ["git", "branch", "--show-current"],
            cwd=str(tmp_path), capture_output=True, text=True,
        )
        branch = result.stdout.strip()
        assert branch.startswith("evolve/forever-")

    def test_exits_on_failure(self, tmp_path: Path):
        """Exits with code 2 if git checkout -b fails (not a git repo)."""
        import pytest
        with pytest.raises(SystemExit) as exc_info:
            _setup_forever_branch(tmp_path)
        assert exc_info.value.code == 2


# ---------------------------------------------------------------------------
# _forever_restart
# ---------------------------------------------------------------------------

class TestForeverRestart:
    def test_adopts_readme_proposal(self, tmp_path: Path):
        """README_proposal.md replaces README.md when present."""
        run_dir = tmp_path / "runs" / "session1"
        run_dir.mkdir(parents=True)
        improvements = tmp_path / "runs" / "improvements.md"
        improvements.write_text("# Improvements\n- [x] done\n- [ ] pending\n")

        proposal = run_dir / "README_proposal.md"
        proposal.write_text("# New README\nProposed content.\n")
        readme = tmp_path / "README.md"
        readme.write_text("# Old README\n")

        ui = MagicMock()
        _forever_restart(tmp_path, run_dir, improvements, ui)

        assert readme.read_text() == "# New README\nProposed content.\n"
        assert improvements.read_text() == "# Improvements\n"
        ui.info.assert_any_call("  Forever mode: adopting README_proposal.md as new README.md")
        ui.info.assert_any_call("  Forever mode: resetting improvements.md for next cycle")

    def test_no_proposal_warns(self, tmp_path: Path):
        """Warns and continues when no README_proposal.md exists."""
        run_dir = tmp_path / "runs" / "session1"
        run_dir.mkdir(parents=True)
        improvements = tmp_path / "runs" / "improvements.md"
        improvements.write_text("# Improvements\n- [x] done\n")

        readme = tmp_path / "README.md"
        readme.write_text("# Original README\n")

        ui = MagicMock()
        _forever_restart(tmp_path, run_dir, improvements, ui)

        # README unchanged
        assert readme.read_text() == "# Original README\n"
        # improvements still reset
        assert improvements.read_text() == "# Improvements\n"
        ui.warn.assert_called_once_with(
            "No README_proposal.md produced — restarting with current README"
        )
