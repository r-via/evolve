"""Tests for evolve.infrastructure.filesystem.state_manager / evolve.diagnostics check helpers — _auto_detect_check, _parse_check_output, _check_spec_freshness."""

from pathlib import Path
from unittest.mock import patch

from evolve.infrastructure.diagnostics.detector import _auto_detect_check
from evolve.infrastructure.filesystem.state_manager import _check_spec_freshness
from evolve.infrastructure.filesystem.improvement_parser import _parse_check_output

# ---------------------------------------------------------------------------
# _auto_detect_check
# ---------------------------------------------------------------------------

class TestAutoDetectCheck:
    """Test auto-detection of test framework from project files."""

    def test_detects_pytest_from_pyproject(self, tmp_path: Path):
        """Detects pytest when pyproject.toml exists and pytest is on PATH."""
        (tmp_path / "pyproject.toml").write_text("[project]\nname = 'foo'\n")
        with patch("evolve.infrastructure.diagnostics.detector.shutil.which", side_effect=lambda x: "/usr/bin/pytest" if x == "pytest" else None):
            assert _auto_detect_check(tmp_path) == "pytest"

    def test_detects_pytest_from_setup_py(self, tmp_path: Path):
        (tmp_path / "setup.py").write_text("from setuptools import setup\n")
        with patch("evolve.infrastructure.diagnostics.detector.shutil.which", side_effect=lambda x: "/usr/bin/pytest" if x == "pytest" else None):
            assert _auto_detect_check(tmp_path) == "pytest"

    def test_detects_pytest_from_test_files(self, tmp_path: Path):
        """Detects pytest when tests/ dir has test_*.py files."""
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        (tests_dir / "test_foo.py").write_text("def test_x(): pass\n")
        with patch("evolve.infrastructure.diagnostics.detector.shutil.which", side_effect=lambda x: "/usr/bin/pytest" if x == "pytest" else None):
            assert _auto_detect_check(tmp_path) == "pytest"

    def test_detects_npm_test(self, tmp_path: Path):
        (tmp_path / "package.json").write_text('{"name": "foo"}\n')
        with patch("evolve.infrastructure.diagnostics.detector.shutil.which", side_effect=lambda x: "/usr/bin/npm" if x == "npm" else None):
            assert _auto_detect_check(tmp_path) == "npm test"

    def test_detects_cargo_test(self, tmp_path: Path):
        (tmp_path / "Cargo.toml").write_text("[package]\nname = 'foo'\n")
        with patch("evolve.infrastructure.diagnostics.detector.shutil.which", side_effect=lambda x: "/usr/bin/cargo" if x == "cargo" else None):
            assert _auto_detect_check(tmp_path) == "cargo test"

    def test_detects_go_test(self, tmp_path: Path):
        (tmp_path / "go.mod").write_text("module example.com/foo\n")
        with patch("evolve.infrastructure.diagnostics.detector.shutil.which", side_effect=lambda x: "/usr/bin/go" if x == "go" else None):
            assert _auto_detect_check(tmp_path) == "go test ./..."

    def test_detects_make_test(self, tmp_path: Path):
        (tmp_path / "Makefile").write_text("test:\n\t@echo running tests\n")
        with patch("evolve.infrastructure.diagnostics.detector.shutil.which", side_effect=lambda x: "/usr/bin/make" if x == "make" else None):
            assert _auto_detect_check(tmp_path) == "make test"

    def test_make_without_test_target(self, tmp_path: Path):
        """Makefile without a 'test' target should not match."""
        (tmp_path / "Makefile").write_text("build:\n\t@echo building\n")
        with patch("evolve.infrastructure.diagnostics.detector.shutil.which", side_effect=lambda x: "/usr/bin/make" if x == "make" else None):
            assert _auto_detect_check(tmp_path) is None

    def test_returns_none_empty_dir(self, tmp_path: Path):
        """Empty directory returns None."""
        with patch("evolve.infrastructure.diagnostics.detector.shutil.which", return_value=None):
            assert _auto_detect_check(tmp_path) is None

    def test_pytest_not_on_path(self, tmp_path: Path):
        """Python project but pytest not installed — skip to next."""
        (tmp_path / "pyproject.toml").write_text("[project]\n")
        with patch("evolve.infrastructure.diagnostics.detector.shutil.which", return_value=None):
            assert _auto_detect_check(tmp_path) is None

    def test_priority_order_python_over_node(self, tmp_path: Path):
        """Python project takes priority over Node project."""
        (tmp_path / "pyproject.toml").write_text("[project]\n")
        (tmp_path / "package.json").write_text("{}\n")
        def which_side(x):
            return f"/usr/bin/{x}" if x in ("pytest", "npm") else None
        with patch("evolve.infrastructure.diagnostics.detector.shutil.which", side_effect=which_side):
            assert _auto_detect_check(tmp_path) == "pytest"

    def test_falls_through_to_npm_when_no_pytest(self, tmp_path: Path):
        """If pytest not available, falls through to npm test."""
        (tmp_path / "pyproject.toml").write_text("[project]\n")
        (tmp_path / "package.json").write_text("{}\n")
        def which_side(x):
            return f"/usr/bin/{x}" if x == "npm" else None
        with patch("evolve.infrastructure.diagnostics.detector.shutil.which", side_effect=which_side):
            assert _auto_detect_check(tmp_path) == "npm test"



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
