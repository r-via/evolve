"""Tests for agent.py — build_prompt, error helpers, retry logic."""

import textwrap
from pathlib import Path
from unittest.mock import patch

from agent import (
    build_prompt,
    _build_check_section,
    _is_benign_runtime_error,
    _load_project_context,
    _should_retry_rate_limit,
)


# ---------------------------------------------------------------------------
# _build_check_section
# ---------------------------------------------------------------------------

class TestBuildCheckSection:
    """Unit tests for _build_check_section covering all three branches."""

    def test_cmd_and_output(self):
        """When both check_cmd and check_output are provided, render full section."""
        result = _build_check_section("pytest", "5 passed")
        assert "## Check command: `pytest`" in result
        assert "### Latest check output:" in result
        assert "5 passed" in result

    def test_cmd_only_no_output(self):
        """When check_cmd is provided but check_output is empty, render 'not yet run'."""
        result = _build_check_section("pytest", "")
        assert "## Check command: `pytest` (not yet run)" in result
        assert "Latest check output" not in result

    def test_no_cmd_no_output(self):
        """When check_cmd is None and output is empty, return empty string."""
        result = _build_check_section(None, "")
        assert result == ""

    def test_no_cmd_with_output(self):
        """When check_cmd is None but output exists, return empty string (no cmd = no section)."""
        result = _build_check_section(None, "some output")
        assert result == ""

    def test_cmd_and_output_contains_markdown_fence(self):
        """Output is wrapped in a Markdown code fence."""
        result = _build_check_section("npm test", "PASS all tests")
        assert "```\nPASS all tests\n```" in result


# ---------------------------------------------------------------------------
# _load_project_context
# ---------------------------------------------------------------------------

class TestLoadProjectContext:
    def test_loads_readme_and_improvements(self, tmp_path: Path):
        (tmp_path / "README.md").write_text("# Hello")
        runs = tmp_path / "runs"
        runs.mkdir()
        (runs / "improvements.md").write_text("- [ ] [functional] Do X\n")
        ctx = _load_project_context(tmp_path)
        assert ctx["readme"] == "# Hello"
        assert "Do X" in ctx["improvements"]

    def test_no_readme(self, tmp_path: Path):
        (tmp_path / "runs").mkdir()
        ctx = _load_project_context(tmp_path)
        assert ctx["readme"] == ""

    def test_no_improvements(self, tmp_path: Path):
        (tmp_path / "README.md").write_text("# P")
        (tmp_path / "runs").mkdir()
        ctx = _load_project_context(tmp_path)
        assert ctx["improvements"] is None

    def test_readme_rst_fallback(self, tmp_path: Path):
        (tmp_path / "README.rst").write_text("RST readme")
        (tmp_path / "runs").mkdir()
        ctx = _load_project_context(tmp_path)
        assert ctx["readme"] == "RST readme"


# ---------------------------------------------------------------------------
# build_prompt
# ---------------------------------------------------------------------------

class TestBuildPrompt:
    def test_includes_readme(self, tmp_path: Path):
        (tmp_path / "README.md").write_text("# My Project\nHello world")
        runs = tmp_path / "runs"
        runs.mkdir()
        prompt = build_prompt(tmp_path)
        assert "Hello world" in prompt

    def test_includes_improvements(self, tmp_path: Path):
        (tmp_path / "README.md").write_text("# Proj")
        runs = tmp_path / "runs"
        runs.mkdir()
        imp = runs / "improvements.md"
        imp.write_text("- [ ] [functional] Add feature X\n")
        prompt = build_prompt(tmp_path)
        assert "Add feature X" in prompt

    def test_constraint_when_not_allow_installs(self, tmp_path: Path):
        (tmp_path / "README.md").write_text("# P")
        (tmp_path / "runs").mkdir()
        prompt = build_prompt(tmp_path, allow_installs=False)
        assert "Do NOT add new binaries" in prompt

    def test_no_constraint_when_allow_installs(self, tmp_path: Path):
        (tmp_path / "README.md").write_text("# P")
        (tmp_path / "runs").mkdir()
        prompt = build_prompt(tmp_path, allow_installs=True)
        assert "Do NOT add new binaries" not in prompt

    def test_check_output_included(self, tmp_path: Path):
        (tmp_path / "README.md").write_text("# P")
        (tmp_path / "runs").mkdir()
        prompt = build_prompt(tmp_path, check_output="42 passed", check_cmd="pytest")
        assert "42 passed" in prompt
        assert "pytest" in prompt

    def test_no_readme_fallback(self, tmp_path: Path):
        (tmp_path / "runs").mkdir()
        prompt = build_prompt(tmp_path)
        assert "(no README found)" in prompt

    def test_curly_braces_in_readme_safe(self, tmp_path: Path):
        """Ensure literal curly braces in README don't crash build_prompt."""
        (tmp_path / "README.md").write_text("let x = {foo: 1};")
        (tmp_path / "runs").mkdir()
        prompt = build_prompt(tmp_path)
        assert "{foo: 1}" in prompt

    def test_project_specific_prompt(self, tmp_path: Path):
        (tmp_path / "README.md").write_text("# P")
        (tmp_path / "runs").mkdir()
        prompts = tmp_path / "prompts"
        prompts.mkdir()
        (prompts / "evolve-system.md").write_text("Custom prompt for {project_dir}")
        prompt = build_prompt(tmp_path)
        assert str(tmp_path) in prompt

    def test_skips_needs_package_target(self, tmp_path: Path):
        (tmp_path / "README.md").write_text("# P")
        runs = tmp_path / "runs"
        runs.mkdir()
        imp = runs / "improvements.md"
        imp.write_text(textwrap.dedent("""\
            - [ ] [functional] [needs-package] blocked
            - [ ] [functional] real target
        """))
        prompt = build_prompt(tmp_path, allow_installs=False)
        assert "Current target improvement: [functional] real target" in prompt


# ---------------------------------------------------------------------------
# _is_benign_runtime_error
# ---------------------------------------------------------------------------

class TestIsBenignRuntimeError:
    def test_cancel_scope(self):
        assert _is_benign_runtime_error(RuntimeError("cancel scope blah")) is True

    def test_event_loop_closed(self):
        assert _is_benign_runtime_error(RuntimeError("Event loop is closed")) is True

    def test_real_error(self):
        assert _is_benign_runtime_error(RuntimeError("something else")) is False


# ---------------------------------------------------------------------------
# _should_retry_rate_limit
# ---------------------------------------------------------------------------

class TestShouldRetryRateLimit:
    def test_rate_limit_retryable(self):
        e = Exception("rate_limit_exceeded")
        assert _should_retry_rate_limit(e, 1, 5) == 60

    def test_rate_limit_second_attempt(self):
        e = Exception("rate_limit_exceeded")
        assert _should_retry_rate_limit(e, 2, 5) == 120

    def test_rate_limit_last_attempt(self):
        """On last attempt, should not retry."""
        e = Exception("rate_limit_exceeded")
        assert _should_retry_rate_limit(e, 5, 5) is None

    def test_non_rate_limit(self):
        e = Exception("something else")
        assert _should_retry_rate_limit(e, 1, 5) is None
