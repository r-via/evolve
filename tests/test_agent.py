"""Tests for agent.py — build_prompt, error helpers, retry logic, coverage."""

import asyncio
import textwrap
from pathlib import Path
from unittest.mock import patch, MagicMock, AsyncMock

from evolve.infrastructure.claude_sdk.prompt_builder import (
    build_prompt,
    _load_project_context,
)
from evolve.infrastructure.claude_sdk.oneshot_agents import _build_check_section
from evolve.infrastructure.claude_sdk.runner import _build_multimodal_prompt
from evolve.infrastructure.claude_sdk.agent import _detect_current_attempt
from evolve.infrastructure.claude_sdk.runtime import _summarise_tool_input
from evolve.infrastructure.claude_sdk.runtime import _should_retry_rate_limit
from evolve.infrastructure.claude_sdk.prompt_diagnostics import _detect_prior_round_anomalies
from evolve.infrastructure.claude_sdk.runtime import _is_benign_runtime_error


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


# ---------------------------------------------------------------------------
# Phase 3.5 — Structural change self-detection (prompt-driven)
# ---------------------------------------------------------------------------
# SPEC.md § "Structural change self-detection" — agent-side protocol lives
# entirely in prompts/system.md.  These tests verify the template contains
# all documented trigger conditions, the STRUCTURAL prefix rule, the
# RESTART_REQUIRED schema, and the Phase 4 skip directive.

_ROOT = Path(__file__).resolve().parent.parent
_PROMPT_TEMPLATE = (_ROOT / "prompts" / "system.md").read_text(encoding="utf-8")


class TestStructuralChangeSelfDetection:
    """Verify prompts/system.md contains the Phase 3.5 block per SPEC.md."""

    # -- Section header --

    def test_phase_35_header_present(self):
        """Phase 3.5 section header with correct title exists."""
        assert "Phase 3.5" in _PROMPT_TEMPLATE
        assert "STRUCTURAL CHANGE SELF-DETECTION" in _PROMPT_TEMPLATE

    def test_mandatory_before_commit(self):
        """Phase 3.5 is marked as mandatory before commit."""
        assert "mandatory before commit" in _PROMPT_TEMPLATE

    # -- Trigger conditions (6 per SPEC) --

    def test_trigger_file_rename(self):
        """Trigger: file rename detected via git diff --diff-filter=R."""
        assert "diff-filter=R" in _PROMPT_TEMPLATE

    def test_trigger_file_creation_deletion_imports(self):
        """Trigger: file creation/deletion referenced by import in another file."""
        assert 'grep -l "from <name>"' in _PROMPT_TEMPLATE or \
               "grep -l" in _PROMPT_TEMPLATE
        assert 'import <name>' in _PROMPT_TEMPLATE or \
               "imported by another tracked file" in _PROMPT_TEMPLATE

    def test_trigger_pyproject_toml_scripts(self):
        """Trigger: pyproject.toml [project.scripts] section changes."""
        assert "[project.scripts]" in _PROMPT_TEMPLATE

    def test_trigger_pyproject_toml_setuptools(self):
        """Trigger: pyproject.toml [tool.setuptools] section changes."""
        assert "[tool.setuptools]" in _PROMPT_TEMPLATE

    def test_trigger_init_py(self):
        """Trigger: __init__.py changes that alter module re-exports."""
        assert "__init__.py" in _PROMPT_TEMPLATE

    def test_trigger_main_py(self):
        """Trigger: __main__.py creation or deletion."""
        assert "__main__.py" in _PROMPT_TEMPLATE

    def test_trigger_conftest(self):
        """Trigger: conftest.py or tests/conftest.py changes."""
        assert "conftest.py" in _PROMPT_TEMPLATE
        assert "tests/conftest.py" in _PROMPT_TEMPLATE

    # -- Agent protocol --

    def test_structural_prefix_rule(self):
        """COMMIT_MSG first line must be prefixed with 'STRUCTURAL: '."""
        assert "STRUCTURAL: " in _PROMPT_TEMPLATE

    def test_restart_required_file(self):
        """Template instructs writing a RESTART_REQUIRED marker file."""
        assert "RESTART_REQUIRED" in _PROMPT_TEMPLATE

    def test_restart_required_schema_reason(self):
        """RESTART_REQUIRED schema includes 'reason' field."""
        assert "reason:" in _PROMPT_TEMPLATE

    def test_restart_required_schema_verify(self):
        """RESTART_REQUIRED schema includes 'verify' field."""
        assert "verify:" in _PROMPT_TEMPLATE

    def test_restart_required_schema_resume(self):
        """RESTART_REQUIRED schema includes 'resume' field."""
        assert "resume:" in _PROMPT_TEMPLATE

    def test_restart_required_schema_round(self):
        """RESTART_REQUIRED schema includes 'round' field."""
        assert "round:" in _PROMPT_TEMPLATE

    def test_restart_required_schema_timestamp(self):
        """RESTART_REQUIRED schema includes 'timestamp' field."""
        assert "timestamp:" in _PROMPT_TEMPLATE

    def test_phase_4_skip_directive(self):
        """Template directs agent to skip Phase 4 on structural changes."""
        assert "Skip Phase 4" in _PROMPT_TEMPLATE

    def test_no_converged_on_structural(self):
        """Template says do NOT write CONVERGED on structural changes."""
        # Extract the Phase 3.5 block: from "Phase 3.5" to "Phase 4 —"
        # (the actual Phase 4 header, not the inline "Skip Phase 4" ref)
        phase35_start = _PROMPT_TEMPLATE.find("Phase 3.5")
        phase4_header = _PROMPT_TEMPLATE.find("Phase 4 —", phase35_start)
        phase35_block = _PROMPT_TEMPLATE[phase35_start:phase4_header]
        assert "Do NOT write" in phase35_block
        assert "CONVERGED" in phase35_block

    def test_exit_code_3_mentioned(self):
        """Template mentions orchestrator exit code 3."""
        phase35_start = _PROMPT_TEMPLATE.find("Phase 3.5")
        phase4_header = _PROMPT_TEMPLATE.find("Phase 4 —", phase35_start)
        phase35_block = _PROMPT_TEMPLATE[phase35_start:phase4_header]
        assert "exit with code 3" in phase35_block

    # -- Runtime verification via build_prompt --

    def test_build_prompt_renders_structural_block(self, tmp_path: Path):
        """build_prompt() output includes the structural change block."""
        (tmp_path / "README.md").write_text("# Proj")
        (tmp_path / "runs").mkdir()
        run_dir = tmp_path / "runs" / "session1"
        run_dir.mkdir()
        prompt = build_prompt(tmp_path, run_dir=run_dir)
        assert "STRUCTURAL CHANGE SELF-DETECTION" in prompt
        assert "RESTART_REQUIRED" in prompt
        assert "STRUCTURAL: " in prompt

    def test_build_prompt_substitutes_run_dir_in_restart_required(self, tmp_path: Path):
        """build_prompt() substitutes {run_dir} in the RESTART_REQUIRED path."""
        (tmp_path / "README.md").write_text("# Proj")
        (tmp_path / "runs").mkdir()
        run_dir = tmp_path / "runs" / "session1"
        run_dir.mkdir()
        prompt = build_prompt(tmp_path, run_dir=run_dir)
        # After substitution, {run_dir} should be replaced with actual path
        assert "{run_dir}" not in prompt
        expected = f"{run_dir}/RESTART_REQUIRED"
        assert expected in prompt


# ---------------------------------------------------------------------------
# _summarise_tool_input — edge cases
# ---------------------------------------------------------------------------

class TestSummariseToolInput:
    """Cover all branches in _summarise_tool_input."""

    def test_none_returns_empty(self):
        assert _summarise_tool_input(None) == ""

    def test_empty_dict_returns_empty(self):
        assert _summarise_tool_input({}) == ""

    def test_non_dict_returns_str(self):
        assert _summarise_tool_input(42) == "42"
        assert _summarise_tool_input("hello") == "hello"

    def test_non_dict_truncated(self):
        long = "x" * 200
        assert len(_summarise_tool_input(long)) == 100

    def test_summary_key_string(self):
        result = _summarise_tool_input({"command": "ls -la"})
        assert result == "ls -la"

    def test_summary_key_non_string(self):
        """Non-string value for a summary key → str(val)[:100]."""
        result = _summarise_tool_input({"command": 12345})
        assert result == "12345"

    def test_summary_key_list(self):
        """List value for a summary key → str(val)[:100]."""
        result = _summarise_tool_input({"pattern": ["a", "b"]})
        assert result == "['a', 'b']"

    def test_old_string_edit_no_file_path(self):
        """old_string without file_path (file_path is in summary keys, so only
        tests the old_string branch when file_path is absent)."""
        result = _summarise_tool_input({"old_string": "foo"})
        assert result == "? (edit)"

    def test_old_string_with_file_path(self):
        """When file_path is present, it matches via summary keys first."""
        result = _summarise_tool_input({"old_string": "foo", "file_path": "/a.py"})
        assert result == "/a.py"

    def test_content_length(self):
        result = _summarise_tool_input({"content": "hello world"})
        assert result == "(11 chars)"

    def test_content_non_len(self):
        """content with a value that raises TypeError on len() → fallback."""
        result = _summarise_tool_input({"content": 999})
        # Falls through to todos or last-resort
        assert result  # some non-empty string

    def test_todos_length(self):
        result = _summarise_tool_input({"todos": [1, 2, 3]})
        assert result == "(3 todos)"

    def test_todos_non_len(self):
        """todos with a value that raises TypeError on len() → last-resort."""
        result = _summarise_tool_input({"todos": True})
        assert result  # last-resort fallback, non-empty

    def test_last_resort_fallback(self):
        """Unknown keys → truncated repr of the dict."""
        result = _summarise_tool_input({"xyz": "val"})
        assert "xyz" in result
        assert len(result) <= 80
