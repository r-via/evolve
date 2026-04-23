"""Tests for loop.py — _is_needs_package, counters, _get_current_improvement, _auto_detect_check."""

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
    _auto_detect_check,
    _parse_check_output,
    _setup_forever_branch,
    _forever_restart,
    _write_state_json,
    _check_spec_freshness,
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


# ---------------------------------------------------------------------------
# _auto_detect_check
# ---------------------------------------------------------------------------

class TestAutoDetectCheck:
    """Test auto-detection of test framework from project files."""

    def test_detects_pytest_from_pyproject(self, tmp_path: Path):
        """Detects pytest when pyproject.toml exists and pytest is on PATH."""
        (tmp_path / "pyproject.toml").write_text("[project]\nname = 'foo'\n")
        with patch("loop.shutil.which", side_effect=lambda x: "/usr/bin/pytest" if x == "pytest" else None):
            assert _auto_detect_check(tmp_path) == "pytest"

    def test_detects_pytest_from_setup_py(self, tmp_path: Path):
        (tmp_path / "setup.py").write_text("from setuptools import setup\n")
        with patch("loop.shutil.which", side_effect=lambda x: "/usr/bin/pytest" if x == "pytest" else None):
            assert _auto_detect_check(tmp_path) == "pytest"

    def test_detects_pytest_from_test_files(self, tmp_path: Path):
        """Detects pytest when tests/ dir has test_*.py files."""
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        (tests_dir / "test_foo.py").write_text("def test_x(): pass\n")
        with patch("loop.shutil.which", side_effect=lambda x: "/usr/bin/pytest" if x == "pytest" else None):
            assert _auto_detect_check(tmp_path) == "pytest"

    def test_detects_npm_test(self, tmp_path: Path):
        (tmp_path / "package.json").write_text('{"name": "foo"}\n')
        with patch("loop.shutil.which", side_effect=lambda x: "/usr/bin/npm" if x == "npm" else None):
            assert _auto_detect_check(tmp_path) == "npm test"

    def test_detects_cargo_test(self, tmp_path: Path):
        (tmp_path / "Cargo.toml").write_text("[package]\nname = 'foo'\n")
        with patch("loop.shutil.which", side_effect=lambda x: "/usr/bin/cargo" if x == "cargo" else None):
            assert _auto_detect_check(tmp_path) == "cargo test"

    def test_detects_go_test(self, tmp_path: Path):
        (tmp_path / "go.mod").write_text("module example.com/foo\n")
        with patch("loop.shutil.which", side_effect=lambda x: "/usr/bin/go" if x == "go" else None):
            assert _auto_detect_check(tmp_path) == "go test ./..."

    def test_detects_make_test(self, tmp_path: Path):
        (tmp_path / "Makefile").write_text("test:\n\t@echo running tests\n")
        with patch("loop.shutil.which", side_effect=lambda x: "/usr/bin/make" if x == "make" else None):
            assert _auto_detect_check(tmp_path) == "make test"

    def test_make_without_test_target(self, tmp_path: Path):
        """Makefile without a 'test' target should not match."""
        (tmp_path / "Makefile").write_text("build:\n\t@echo building\n")
        with patch("loop.shutil.which", side_effect=lambda x: "/usr/bin/make" if x == "make" else None):
            assert _auto_detect_check(tmp_path) is None

    def test_returns_none_empty_dir(self, tmp_path: Path):
        """Empty directory returns None."""
        with patch("loop.shutil.which", return_value=None):
            assert _auto_detect_check(tmp_path) is None

    def test_pytest_not_on_path(self, tmp_path: Path):
        """Python project but pytest not installed — skip to next."""
        (tmp_path / "pyproject.toml").write_text("[project]\n")
        with patch("loop.shutil.which", return_value=None):
            assert _auto_detect_check(tmp_path) is None

    def test_priority_order_python_over_node(self, tmp_path: Path):
        """Python project takes priority over Node project."""
        (tmp_path / "pyproject.toml").write_text("[project]\n")
        (tmp_path / "package.json").write_text("{}\n")
        def which_side(x):
            return f"/usr/bin/{x}" if x in ("pytest", "npm") else None
        with patch("loop.shutil.which", side_effect=which_side):
            assert _auto_detect_check(tmp_path) == "pytest"

    def test_falls_through_to_npm_when_no_pytest(self, tmp_path: Path):
        """If pytest not available, falls through to npm test."""
        (tmp_path / "pyproject.toml").write_text("[project]\n")
        (tmp_path / "package.json").write_text("{}\n")
        def which_side(x):
            return f"/usr/bin/{x}" if x == "npm" else None
        with patch("loop.shutil.which", side_effect=which_side):
            assert _auto_detect_check(tmp_path) == "npm test"


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
        assert branch.startswith("evolve/")

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
    def setup_method(self):
        """Fresh UI mock per test — avoids per-test MagicMock() boilerplate."""
        self.ui = MagicMock()

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

        _forever_restart(tmp_path, run_dir, improvements, self.ui)

        assert readme.read_text() == "# New README\nProposed content.\n"
        assert improvements.read_text() == "# Improvements\n"
        self.ui.info.assert_any_call("  Forever mode: adopting README_proposal.md as new README.md")
        self.ui.info.assert_any_call("  Forever mode: resetting improvements.md for next cycle")

    def test_no_proposal_warns(self, tmp_path: Path):
        """Warns and continues when no README_proposal.md exists."""
        run_dir = tmp_path / "runs" / "session1"
        run_dir.mkdir(parents=True)
        improvements = tmp_path / "runs" / "improvements.md"
        improvements.write_text("# Improvements\n- [x] done\n")

        readme = tmp_path / "README.md"
        readme.write_text("# Original README\n")

        _forever_restart(tmp_path, run_dir, improvements, self.ui)

        # README unchanged
        assert readme.read_text() == "# Original README\n"
        # improvements still reset
        assert improvements.read_text() == "# Improvements\n"
        self.ui.warn.assert_called_once_with(
            "No README_proposal.md produced — restarting with current README.md"
        )


# ---------------------------------------------------------------------------
# _write_state_json
# ---------------------------------------------------------------------------

class TestWriteStateJson:
    """Tests for the real-time state.json writer."""

    def test_basic_state_json(self, tmp_path: Path):
        """Write state.json and verify all required fields."""
        run_dir = tmp_path / "session"
        run_dir.mkdir()
        improvements = tmp_path / "improvements.md"
        improvements.write_text(
            "# Improvements\n"
            "- [x] [functional] done one\n"
            "- [x] [functional] done two\n"
            "- [ ] [functional] pending one\n"
        )
        project_dir = tmp_path / "myproject"
        project_dir.mkdir()

        _write_state_json(
            run_dir=run_dir,
            project_dir=project_dir,
            round_num=3,
            max_rounds=10,
            phase="improvement",
            status="running",
            improvements_path=improvements,
            check_passed=True,
            check_tests=42,
            check_duration_s=1.234,
            started_at="2026-03-25T15:00:00Z",
        )

        import json
        state = json.loads((run_dir / "state.json").read_text())
        assert state["version"] == 2
        assert state["session"] == "session"
        assert state["project"] == "myproject"
        assert state["round"] == 3
        assert state["max_rounds"] == 10
        assert state["phase"] == "improvement"
        assert state["status"] == "running"
        assert state["improvements"] == {"done": 2, "remaining": 1, "blocked": 0}
        assert state["last_check"]["passed"] is True
        assert state["last_check"]["tests"] == 42
        assert state["last_check"]["duration_s"] == 1.2
        assert state["started_at"] == "2026-03-25T15:00:00Z"
        assert "updated_at" in state

    def test_state_json_no_check(self, tmp_path: Path):
        """State.json with no check results has empty last_check."""
        run_dir = tmp_path / "session"
        run_dir.mkdir()
        improvements = tmp_path / "improvements.md"
        improvements.write_text("# Improvements\n")
        project_dir = tmp_path / "proj"
        project_dir.mkdir()

        _write_state_json(
            run_dir=run_dir,
            project_dir=project_dir,
            round_num=1,
            max_rounds=5,
            phase="error",
            status="running",
            improvements_path=improvements,
            started_at="2026-03-25T15:00:00Z",
        )

        import json
        state = json.loads((run_dir / "state.json").read_text())
        assert state["last_check"] == {}
        assert state["improvements"] == {"done": 0, "remaining": 0, "blocked": 0}

    def test_state_json_preserves_started_at(self, tmp_path: Path):
        """When started_at is None, reads from existing state.json."""
        run_dir = tmp_path / "session"
        run_dir.mkdir()
        improvements = tmp_path / "improvements.md"
        improvements.write_text("# Improvements\n- [ ] [functional] todo\n")
        project_dir = tmp_path / "proj"
        project_dir.mkdir()

        import json
        # Write initial state with a known started_at
        (run_dir / "state.json").write_text(json.dumps({
            "started_at": "2026-01-01T00:00:00Z",
        }))

        _write_state_json(
            run_dir=run_dir,
            project_dir=project_dir,
            round_num=2,
            max_rounds=10,
            phase="improvement",
            status="running",
            improvements_path=improvements,
            started_at=None,  # should read from existing
        )

        state = json.loads((run_dir / "state.json").read_text())
        assert state["started_at"] == "2026-01-01T00:00:00Z"

    def test_state_json_blocked_count(self, tmp_path: Path):
        """Blocked items are counted separately in improvements."""
        run_dir = tmp_path / "session"
        run_dir.mkdir()
        improvements = tmp_path / "improvements.md"
        improvements.write_text(
            "# Improvements\n"
            "- [x] [functional] done\n"
            "- [ ] [functional] [needs-package] blocked item\n"
            "- [ ] [functional] regular pending\n"
        )
        project_dir = tmp_path / "proj"
        project_dir.mkdir()

        _write_state_json(
            run_dir=run_dir,
            project_dir=project_dir,
            round_num=1,
            max_rounds=5,
            phase="improvement",
            status="running",
            improvements_path=improvements,
            started_at="2026-03-25T15:00:00Z",
        )

        import json
        state = json.loads((run_dir / "state.json").read_text())
        assert state["improvements"]["done"] == 1
        assert state["improvements"]["remaining"] == 2
        assert state["improvements"]["blocked"] == 1

    def test_state_json_converged_status(self, tmp_path: Path):
        """State.json reflects converged status correctly."""
        run_dir = tmp_path / "session"
        run_dir.mkdir()
        improvements = tmp_path / "improvements.md"
        improvements.write_text(
            "# Improvements\n- [x] [functional] all done\n"
        )
        project_dir = tmp_path / "proj"
        project_dir.mkdir()

        _write_state_json(
            run_dir=run_dir,
            project_dir=project_dir,
            round_num=5,
            max_rounds=20,
            phase="convergence",
            status="converged",
            improvements_path=improvements,
            check_passed=True,
            check_tests=100,
            check_duration_s=2.5,
            started_at="2026-03-25T15:00:00Z",
        )

        import json
        state = json.loads((run_dir / "state.json").read_text())
        assert state["status"] == "converged"
        assert state["phase"] == "convergence"
        assert state["improvements"]["done"] == 1
        assert state["improvements"]["remaining"] == 0

    def test_state_json_missing_improvements_file(self, tmp_path: Path):
        """State.json works even when improvements.md doesn't exist."""
        run_dir = tmp_path / "session"
        run_dir.mkdir()
        project_dir = tmp_path / "proj"
        project_dir.mkdir()
        missing = tmp_path / "nonexistent_improvements.md"

        _write_state_json(
            run_dir=run_dir,
            project_dir=project_dir,
            round_num=1,
            max_rounds=10,
            phase="error",
            status="running",
            improvements_path=missing,
            started_at="2026-03-25T15:00:00Z",
        )

        import json
        state = json.loads((run_dir / "state.json").read_text())
        assert state["improvements"] == {"done": 0, "remaining": 0, "blocked": 0}

    def test_state_json_no_existing_generates_started_at(self, tmp_path: Path):
        """When no existing state.json and no started_at, generates current time."""
        run_dir = tmp_path / "session"
        run_dir.mkdir()
        improvements = tmp_path / "improvements.md"
        improvements.write_text("# Improvements\n")
        project_dir = tmp_path / "proj"
        project_dir.mkdir()

        _write_state_json(
            run_dir=run_dir,
            project_dir=project_dir,
            round_num=1,
            max_rounds=10,
            phase="improvement",
            status="running",
            improvements_path=improvements,
            started_at=None,
        )

        import json
        state = json.loads((run_dir / "state.json").read_text())
        # Should have a valid ISO timestamp
        assert "T" in state["started_at"]
        assert state["started_at"].endswith("Z")

    def test_state_json_corrupted_existing_generates_started_at(self, tmp_path: Path):
        """When existing state.json is corrupted, generates a fresh started_at."""
        run_dir = tmp_path / "session"
        run_dir.mkdir()
        improvements = tmp_path / "improvements.md"
        improvements.write_text("# Improvements\n")
        project_dir = tmp_path / "proj"
        project_dir.mkdir()

        # Write corrupted JSON
        (run_dir / "state.json").write_text("{not valid json!!")

        _write_state_json(
            run_dir=run_dir,
            project_dir=project_dir,
            round_num=2,
            max_rounds=10,
            phase="error",
            status="running",
            improvements_path=improvements,
            started_at=None,
        )

        import json
        state = json.loads((run_dir / "state.json").read_text())
        # Should have generated a new timestamp despite corrupted file
        assert "T" in state["started_at"]
        assert state["started_at"].endswith("Z")
        assert state["round"] == 2

    def test_state_json_overwrites_previous(self, tmp_path: Path):
        """Writing state.json twice overwrites the first with updated values."""
        run_dir = tmp_path / "session"
        run_dir.mkdir()
        improvements = tmp_path / "improvements.md"
        improvements.write_text("# Improvements\n- [ ] [functional] todo\n")
        project_dir = tmp_path / "proj"
        project_dir.mkdir()

        import json

        _write_state_json(
            run_dir=run_dir,
            project_dir=project_dir,
            round_num=1,
            max_rounds=10,
            phase="error",
            status="running",
            improvements_path=improvements,
            started_at="2026-01-01T00:00:00Z",
        )
        state1 = json.loads((run_dir / "state.json").read_text())
        assert state1["round"] == 1

        # Update improvements and write again
        improvements.write_text(
            "# Improvements\n- [x] [functional] done\n- [ ] [functional] next\n"
        )
        _write_state_json(
            run_dir=run_dir,
            project_dir=project_dir,
            round_num=2,
            max_rounds=10,
            phase="improvement",
            status="running",
            improvements_path=improvements,
            started_at="2026-01-01T00:00:00Z",
        )
        state2 = json.loads((run_dir / "state.json").read_text())
        assert state2["round"] == 2
        assert state2["improvements"]["done"] == 1
        assert state2["improvements"]["remaining"] == 1

    def test_state_json_partial_check_results(self, tmp_path: Path):
        """Only provided check fields appear in last_check."""
        run_dir = tmp_path / "session"
        run_dir.mkdir()
        improvements = tmp_path / "improvements.md"
        improvements.write_text("# Improvements\n")
        project_dir = tmp_path / "proj"
        project_dir.mkdir()

        import json

        # Only check_passed, no tests or duration
        _write_state_json(
            run_dir=run_dir,
            project_dir=project_dir,
            round_num=1,
            max_rounds=5,
            phase="error",
            status="running",
            improvements_path=improvements,
            check_passed=False,
            started_at="2026-03-25T15:00:00Z",
        )
        state = json.loads((run_dir / "state.json").read_text())
        assert state["last_check"] == {"passed": False}
        assert "tests" not in state["last_check"]
        assert "duration_s" not in state["last_check"]

    def test_state_json_duration_rounding(self, tmp_path: Path):
        """check_duration_s is rounded to 1 decimal place."""
        run_dir = tmp_path / "session"
        run_dir.mkdir()
        improvements = tmp_path / "improvements.md"
        improvements.write_text("# Improvements\n")
        project_dir = tmp_path / "proj"
        project_dir.mkdir()

        import json

        _write_state_json(
            run_dir=run_dir,
            project_dir=project_dir,
            round_num=1,
            max_rounds=5,
            phase="improvement",
            status="running",
            improvements_path=improvements,
            check_passed=True,
            check_duration_s=3.6789,
            started_at="2026-03-25T15:00:00Z",
        )
        state = json.loads((run_dir / "state.json").read_text())
        assert state["last_check"]["duration_s"] == 3.7

    def test_state_json_all_status_values(self, tmp_path: Path):
        """All documented status values are written correctly."""
        run_dir = tmp_path / "session"
        run_dir.mkdir()
        improvements = tmp_path / "improvements.md"
        improvements.write_text("# Improvements\n")
        project_dir = tmp_path / "proj"
        project_dir.mkdir()

        import json

        for status in ("running", "converged", "max_rounds", "error", "party_mode"):
            _write_state_json(
                run_dir=run_dir,
                project_dir=project_dir,
                round_num=1,
                max_rounds=5,
                phase="improvement",
                status=status,
                improvements_path=improvements,
                started_at="2026-03-25T15:00:00Z",
            )
            state = json.loads((run_dir / "state.json").read_text())
            assert state["status"] == status

    def test_state_json_updated_at_is_valid_iso(self, tmp_path: Path):
        """updated_at is a valid ISO-format UTC timestamp."""
        run_dir = tmp_path / "session"
        run_dir.mkdir()
        improvements = tmp_path / "improvements.md"
        improvements.write_text("# Improvements\n")
        project_dir = tmp_path / "proj"
        project_dir.mkdir()

        import json
        from datetime import datetime, timezone

        _write_state_json(
            run_dir=run_dir,
            project_dir=project_dir,
            round_num=1,
            max_rounds=5,
            phase="improvement",
            status="running",
            improvements_path=improvements,
            started_at="2026-03-25T15:00:00Z",
        )
        state = json.loads((run_dir / "state.json").read_text())
        # Should parse without error
        dt = datetime.strptime(state["updated_at"], "%Y-%m-%dT%H:%M:%SZ")
        assert dt.year >= 2026

    def test_state_json_existing_without_started_at_key(self, tmp_path: Path):
        """Existing state.json missing started_at generates a new timestamp."""
        run_dir = tmp_path / "session"
        run_dir.mkdir()
        improvements = tmp_path / "improvements.md"
        improvements.write_text("# Improvements\n")
        project_dir = tmp_path / "proj"
        project_dir.mkdir()

        import json
        # Existing state without started_at key
        (run_dir / "state.json").write_text(json.dumps({"version": 1}))

        _write_state_json(
            run_dir=run_dir,
            project_dir=project_dir,
            round_num=3,
            max_rounds=10,
            phase="improvement",
            status="running",
            improvements_path=improvements,
            started_at=None,
        )
        state = json.loads((run_dir / "state.json").read_text())
        # Should have generated a fresh timestamp
        assert "T" in state["started_at"]
        assert state["started_at"].endswith("Z")


# ---------------------------------------------------------------------------
# _parse_check_output
# ---------------------------------------------------------------------------

class TestParseCheckOutput:
    """Tests for _parse_check_output — extracting pass/fail, test count, duration."""

    def test_standard_pytest_output(self):
        """Parse standard pytest PASS output with test count and duration."""
        text = (
            "Round 1 post-fix check: PASS\n"
            "Command: pytest\n"
            "Exit code: 0\n\n"
            "stdout:\n"
            "============================= test session starts ==============================\n"
            "collected 42 items\n\n"
            "tests/test_foo.py ..........................................\n\n"
            "============================= 42 passed in 1.23s ==============================\n"
        )
        passed, tests, duration = _parse_check_output(text)
        assert passed is True
        assert tests == 42
        assert duration == 1.23

    def test_pytest_failure_output(self):
        """Parse pytest output with failures (no PASS marker)."""
        text = (
            "Round 2 post-fix check: FAIL\n"
            "Command: pytest\n"
            "Exit code: 1\n\n"
            "stdout:\n"
            "============================= test session starts ==============================\n"
            "collected 10 items\n\n"
            "tests/test_foo.py ..F..F....\n\n"
            "============================= 8 passed, 2 failed in 0.45s =====================\n"
        )
        passed, tests, duration = _parse_check_output(text)
        assert passed is False
        assert tests == 8
        assert duration == 0.45

    def test_empty_text(self):
        """Empty text returns all None."""
        passed, tests, duration = _parse_check_output("")
        assert passed is None
        assert tests is None
        assert duration is None

    def test_whitespace_only(self):
        """Whitespace-only text returns all None."""
        passed, tests, duration = _parse_check_output("   \n\n  ")
        assert passed is None
        assert tests is None
        assert duration is None

    def test_no_test_count_or_duration(self):
        """Output with PASS but no pytest-style test count or duration."""
        text = "Round 1 post-fix check: PASS\nCommand: make test\nExit code: 0\n\nAll good.\n"
        passed, tests, duration = _parse_check_output(text)
        assert passed is True
        assert tests is None
        assert duration is None

    def test_npm_test_output(self):
        """Non-pytest output (npm test) — no 'N passed' pattern."""
        text = (
            "Round 1 post-fix check: PASS\n"
            "Command: npm test\n"
            "Exit code: 0\n\n"
            "Test Suites: 3 passed, 3 total\n"
            "Tests:       15 passed, 15 total\n"
            "Time:        2.345 s\n"
        )
        passed, tests, duration = _parse_check_output(text)
        assert passed is True
        # "3 passed" from Test Suites line matches first
        assert tests == 3
        # No "in N.Ns" pattern — npm uses different format
        assert duration is None

    def test_cargo_test_output(self):
        """Cargo test output format."""
        text = (
            "Round 1 post-fix check: PASS\n"
            "Command: cargo test\n"
            "Exit code: 0\n\n"
            "running 23 tests\n"
            "test result: ok. 23 passed; 0 failed; 0 ignored; 0 measured; 0 filtered out; "
            "finished in 0.82s\n"
        )
        passed, tests, duration = _parse_check_output(text)
        assert passed is True
        assert tests == 23
        assert duration == 0.82

    def test_large_test_count(self):
        """Large test counts are parsed correctly."""
        text = "PASS\n560 passed in 6.93s\n"
        passed, tests, duration = _parse_check_output(text)
        assert passed is True
        assert tests == 560
        assert duration == 6.93

    def test_duration_without_test_count(self):
        """Duration present but no 'N passed' pattern."""
        text = "PASS\nAll checks completed in 12.5s\n"
        passed, tests, duration = _parse_check_output(text)
        assert passed is True
        assert tests is None
        assert duration == 12.5

    def test_multiple_passed_patterns_takes_first(self):
        """When multiple 'N passed' patterns exist, first match is used."""
        text = "PASS\n10 passed in 1.0s\nAlso 5 passed in 0.5s\n"
        passed, tests, duration = _parse_check_output(text)
        assert tests == 10
        assert duration == 1.0

    def test_pass_marker_case_sensitive(self):
        """PASS detection is case-sensitive — 'pass' does not match."""
        text = "All tests pass\n42 passed in 1.0s\n"
        passed, tests, duration = _parse_check_output(text)
        assert passed is False
        assert tests == 42

    def test_pass_marker_in_word(self):
        """PASS substring in other words still triggers True."""
        text = "PASSED all checks\n10 passed in 2.0s\n"
        passed, tests, duration = _parse_check_output(text)
        assert passed is True


# ---------------------------------------------------------------------------
# _setup_forever_branch — edge cases
# ---------------------------------------------------------------------------

class TestSetupForeverBranchEdgeCases:
    """Edge-case tests for _setup_forever_branch."""

    def _init_git(self, path: Path):
        """Initialize a git repo with an initial commit."""
        subprocess.run(["git", "init"], cwd=str(path), capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=str(path), capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=str(path), capture_output=True)
        (path / "README.md").write_text("# Test\n")
        subprocess.run(["git", "add", "-A"], cwd=str(path), capture_output=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=str(path), capture_output=True)

    def test_branch_name_uses_timestamp_format(self, tmp_path: Path):
        """Branch name follows evolve/YYYYMMDD_HHMMSS pattern."""
        self._init_git(tmp_path)
        with patch("loop.datetime") as mock_dt:
            mock_dt.now.return_value.strftime.return_value = "20260325_120000"
            _setup_forever_branch(tmp_path)

        result = subprocess.run(
            ["git", "branch", "--show-current"],
            cwd=str(tmp_path), capture_output=True, text=True,
        )
        assert result.stdout.strip() == "evolve/20260325_120000"

    def test_exits_code_2_on_git_failure(self, tmp_path: Path):
        """sys.exit(2) when git checkout -b fails (no git repo)."""
        import pytest
        with pytest.raises(SystemExit) as exc_info:
            _setup_forever_branch(tmp_path)
        assert exc_info.value.code == 2

    def test_exits_code_2_when_branch_already_exists(self, tmp_path: Path):
        """sys.exit(2) when branch name collides with existing branch."""
        import pytest
        self._init_git(tmp_path)
        # Create branch first time
        fixed_ts = "20260101_000000"
        with patch("loop.datetime") as mock_dt:
            mock_dt.now.return_value.strftime.return_value = fixed_ts
            _setup_forever_branch(tmp_path)

        # Switch back to main so the second call tries to create same branch
        subprocess.run(["git", "checkout", "-b", "temp"], cwd=str(tmp_path), capture_output=True)

        with pytest.raises(SystemExit) as exc_info:
            with patch("loop.datetime") as mock_dt2:
                mock_dt2.now.return_value.strftime.return_value = fixed_ts
                _setup_forever_branch(tmp_path)
        assert exc_info.value.code == 2

    def test_error_message_logged_on_failure(self, tmp_path: Path):
        """Error message is emitted via ui.error on git failure."""
        import pytest
        with patch("loop.get_tui") as mock_get_tui:
            mock_ui = MagicMock()
            mock_get_tui.return_value = mock_ui
            with pytest.raises(SystemExit):
                _setup_forever_branch(tmp_path)
            mock_ui.error.assert_called_once()
            assert "Failed to create branch" in mock_ui.error.call_args[0][0]


# ---------------------------------------------------------------------------
# _forever_restart — edge cases
# ---------------------------------------------------------------------------

class TestForeverRestartEdgeCases:
    """Edge-case tests for _forever_restart."""

    def setup_method(self):
        self.ui = MagicMock()

    def test_malformed_readme_proposal_adopted_as_is(self, tmp_path: Path):
        """Malformed README_proposal.md content is still copied verbatim."""
        run_dir = tmp_path / "runs" / "session1"
        run_dir.mkdir(parents=True)
        improvements = tmp_path / "runs" / "improvements.md"
        improvements.write_text("# Improvements\n- [x] done\n")

        # Malformed content — not valid markdown, binary-like garbage
        malformed = "<<<\x00\x01 broken {{{ unclosed\n\n\n###"
        (run_dir / "README_proposal.md").write_text(malformed)
        readme = tmp_path / "README.md"
        readme.write_text("# Old\n")

        _forever_restart(tmp_path, run_dir, improvements, self.ui)

        assert readme.read_text() == malformed
        self.ui.info.assert_any_call(
            "  Forever mode: adopting README_proposal.md as new README.md"
        )

    def test_empty_readme_proposal(self, tmp_path: Path):
        """Empty README_proposal.md results in empty README.md."""
        run_dir = tmp_path / "runs" / "session1"
        run_dir.mkdir(parents=True)
        improvements = tmp_path / "runs" / "improvements.md"
        improvements.write_text("# Improvements\n- [ ] pending\n")

        (run_dir / "README_proposal.md").write_text("")
        readme = tmp_path / "README.md"
        readme.write_text("# Old\n")

        _forever_restart(tmp_path, run_dir, improvements, self.ui)

        assert readme.read_text() == ""

    def test_improvements_reset_with_complex_content(self, tmp_path: Path):
        """improvements.md is reset regardless of how complex it was."""
        run_dir = tmp_path / "runs" / "session1"
        run_dir.mkdir(parents=True)
        improvements = tmp_path / "runs" / "improvements.md"
        complex_content = textwrap.dedent("""\
            # Improvements

            - [x] [functional] First improvement
            - [x] [performance] Second improvement
            - [x] [functional] [needs-package] Third improvement
            - [ ] [functional] Fourth improvement
            - [ ] [performance] Fifth improvement
        """)
        improvements.write_text(complex_content)
        readme = tmp_path / "README.md"
        readme.write_text("# README\n")

        _forever_restart(tmp_path, run_dir, improvements, self.ui)

        assert improvements.read_text() == "# Improvements\n"

    def test_converged_file_not_deleted(self, tmp_path: Path):
        """CONVERGED file in run_dir is preserved after restart."""
        run_dir = tmp_path / "runs" / "session1"
        run_dir.mkdir(parents=True)
        improvements = tmp_path / "runs" / "improvements.md"
        improvements.write_text("# Improvements\n")

        converged = run_dir / "CONVERGED"
        converged.write_text("Converged: all claims verified")
        readme = tmp_path / "README.md"
        readme.write_text("# Old\n")

        _forever_restart(tmp_path, run_dir, improvements, self.ui)

        assert converged.is_file()
        assert converged.read_text() == "Converged: all claims verified"

    def test_no_readme_in_project_still_creates_one(self, tmp_path: Path):
        """If no README.md exists and proposal is present, it gets created."""
        run_dir = tmp_path / "runs" / "session1"
        run_dir.mkdir(parents=True)
        improvements = tmp_path / "runs" / "improvements.md"
        improvements.write_text("# Improvements\n")

        (run_dir / "README_proposal.md").write_text("# Brand New README\n")

        _forever_restart(tmp_path, run_dir, improvements, self.ui)

        assert (tmp_path / "README.md").read_text() == "# Brand New README\n"

    def test_unicode_readme_proposal(self, tmp_path: Path):
        """README_proposal.md with unicode content is handled correctly."""
        run_dir = tmp_path / "runs" / "session1"
        run_dir.mkdir(parents=True)
        improvements = tmp_path / "runs" / "improvements.md"
        improvements.write_text("# Improvements\n")

        unicode_content = "# 🚀 Evolve\n\n日本語テスト — ñoño — café ☕\n"
        (run_dir / "README_proposal.md").write_text(unicode_content)
        readme = tmp_path / "README.md"
        readme.write_text("# Old\n")

        _forever_restart(tmp_path, run_dir, improvements, self.ui)

        assert readme.read_text() == unicode_content


# ---------------------------------------------------------------------------
# _check_spec_freshness
# ---------------------------------------------------------------------------

class TestCheckSpecFreshness:
    """Tests for _check_spec_freshness — mtime comparison and stale marking."""

    def test_fresh_when_improvements_newer(self, tmp_path):
        """When improvements.md is newer than README, returns True (fresh)."""
        spec = tmp_path / "README.md"
        spec.write_text("# Spec\n")
        imp = tmp_path / "runs" / "improvements.md"
        imp.parent.mkdir(parents=True, exist_ok=True)
        imp.write_text("- [ ] [functional] Do something\n")
        # Ensure improvements is newer
        import os, time
        time.sleep(0.05)
        os.utime(imp, None)

        result = _check_spec_freshness(tmp_path, imp)
        assert result is True

    def test_stale_when_spec_newer(self, tmp_path):
        """When spec is newer than improvements.md, returns False (stale)."""
        spec = tmp_path / "README.md"
        imp = tmp_path / "runs" / "improvements.md"
        imp.parent.mkdir(parents=True, exist_ok=True)
        imp.write_text("- [ ] [functional] Old item\n")
        # Write spec AFTER improvements to make it newer
        import os, time
        time.sleep(0.05)
        spec.write_text("# Updated Spec\n")

        result = _check_spec_freshness(tmp_path, imp)
        assert result is False

    def test_stale_marks_unchecked_items(self, tmp_path):
        """Unchecked items get [stale: spec changed] tag when spec is newer."""
        spec = tmp_path / "README.md"
        imp = tmp_path / "runs" / "improvements.md"
        imp.parent.mkdir(parents=True, exist_ok=True)
        imp.write_text(
            "- [ ] [functional] Item one\n"
            "- [ ] [performance] Item two\n"
        )
        import os, time
        time.sleep(0.05)
        spec.write_text("# New spec\n")

        _check_spec_freshness(tmp_path, imp)

        text = imp.read_text()
        assert "[stale: spec changed]" in text
        lines = text.splitlines()
        assert lines[0] == "- [ ] [stale: spec changed] [functional] Item one"
        assert lines[1] == "- [ ] [stale: spec changed] [performance] Item two"

    def test_checked_items_preserved(self, tmp_path):
        """Already checked [x] items are NOT marked stale."""
        spec = tmp_path / "README.md"
        imp = tmp_path / "runs" / "improvements.md"
        imp.parent.mkdir(parents=True, exist_ok=True)
        imp.write_text(
            "- [x] [functional] Done item\n"
            "- [ ] [functional] Pending item\n"
        )
        import os, time
        time.sleep(0.05)
        spec.write_text("# New spec\n")

        _check_spec_freshness(tmp_path, imp)

        text = imp.read_text()
        lines = text.splitlines()
        # Checked item untouched
        assert lines[0] == "- [x] [functional] Done item"
        # Unchecked item marked stale
        assert lines[1] == "- [ ] [stale: spec changed] [functional] Pending item"

    def test_idempotent_stale_marking(self, tmp_path):
        """Running twice doesn't double-tag already stale items."""
        spec = tmp_path / "README.md"
        imp = tmp_path / "runs" / "improvements.md"
        imp.parent.mkdir(parents=True, exist_ok=True)
        imp.write_text(
            "- [ ] [stale: spec changed] [functional] Already stale\n"
        )
        import os, time
        time.sleep(0.05)
        spec.write_text("# New spec\n")

        _check_spec_freshness(tmp_path, imp)

        text = imp.read_text()
        # Should still have exactly one [stale: spec changed] tag
        assert text.count("[stale: spec changed]") == 1

    def test_missing_spec_file_returns_true(self, tmp_path):
        """When spec file doesn't exist, returns True (no gate)."""
        imp = tmp_path / "runs" / "improvements.md"
        imp.parent.mkdir(parents=True, exist_ok=True)
        imp.write_text("- [ ] [functional] Something\n")

        result = _check_spec_freshness(tmp_path, imp)
        assert result is True

    def test_missing_improvements_returns_true(self, tmp_path):
        """When improvements.md doesn't exist, returns True (agent creates fresh)."""
        spec = tmp_path / "README.md"
        spec.write_text("# Spec\n")
        imp = tmp_path / "runs" / "improvements.md"

        result = _check_spec_freshness(tmp_path, imp)
        assert result is True

    def test_custom_spec_file(self, tmp_path):
        """When spec= is provided, uses that file instead of README.md."""
        custom_spec = tmp_path / "SPEC.md"
        imp = tmp_path / "runs" / "improvements.md"
        imp.parent.mkdir(parents=True, exist_ok=True)
        imp.write_text("- [ ] [functional] Old item\n")
        import os, time
        time.sleep(0.05)
        custom_spec.write_text("# Custom Spec\n")

        result = _check_spec_freshness(tmp_path, imp, spec="SPEC.md")
        assert result is False

    def test_custom_spec_missing_returns_true(self, tmp_path):
        """When custom spec doesn't exist, returns True (no gate)."""
        imp = tmp_path / "runs" / "improvements.md"
        imp.parent.mkdir(parents=True, exist_ok=True)
        imp.write_text("- [ ] [functional] Something\n")
        # SPEC.md doesn't exist, even if README.md does
        (tmp_path / "README.md").write_text("# readme\n")

        result = _check_spec_freshness(tmp_path, imp, spec="SPEC.md")
        assert result is True

    def test_convergence_gate_rejects_when_stale(self, tmp_path):
        """Return value False blocks convergence when spec is newer."""
        spec = tmp_path / "README.md"
        imp = tmp_path / "runs" / "improvements.md"
        imp.parent.mkdir(parents=True, exist_ok=True)
        # All items checked, but spec is newer
        imp.write_text("- [x] [functional] Done\n")
        import os, time
        time.sleep(0.05)
        spec.write_text("# Updated spec\n")

        # Even with all items checked, freshness fails
        result = _check_spec_freshness(tmp_path, imp)
        # improvements mtime < spec mtime → stale → False
        assert result is False

    def test_equal_mtime_returns_true(self, tmp_path):
        """When mtimes are equal, returns True (fresh)."""
        spec = tmp_path / "README.md"
        spec.write_text("# Spec\n")
        imp = tmp_path / "runs" / "improvements.md"
        imp.parent.mkdir(parents=True, exist_ok=True)
        imp.write_text("- [ ] [functional] Item\n")
        # Set both to same mtime
        import os
        mtime = spec.stat().st_mtime
        os.utime(imp, (mtime, mtime))
        os.utime(spec, (mtime, mtime))

        result = _check_spec_freshness(tmp_path, imp)
        assert result is True

    def test_mixed_items_only_unchecked_marked(self, tmp_path):
        """With a mix of checked, unchecked, and blocked items, only unchecked get stale tag."""
        spec = tmp_path / "README.md"
        imp = tmp_path / "runs" / "improvements.md"
        imp.parent.mkdir(parents=True, exist_ok=True)
        imp.write_text(
            "# Improvements\n"
            "\n"
            "- [x] [functional] Already done\n"
            "- [ ] [functional] Pending one\n"
            "- [ ] [functional] [needs-package] Pending two\n"
            "- [x] [performance] Also done\n"
        )
        import os, time
        time.sleep(0.05)
        spec.write_text("# New spec\n")

        _check_spec_freshness(tmp_path, imp)

        lines = imp.read_text().splitlines()
        assert lines[0] == "# Improvements"
        assert lines[1] == ""
        assert lines[2] == "- [x] [functional] Already done"
        assert "[stale: spec changed]" in lines[3]
        assert "[stale: spec changed]" in lines[4]
        assert lines[5] == "- [x] [performance] Also done"
