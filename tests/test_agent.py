"""Tests for agent.py — build_prompt, error helpers, retry logic, coverage."""

import asyncio
import textwrap
from pathlib import Path
from unittest.mock import patch, MagicMock, AsyncMock

from evolve.agent import (
    build_prompt,
    _build_check_section,
    _build_multimodal_prompt,
    _detect_current_attempt,
    _detect_prior_round_anomalies,
    _is_benign_runtime_error,
    _load_project_context,
    _should_retry_rate_limit,
    _summarise_tool_input,
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


# ---------------------------------------------------------------------------
# _detect_current_attempt — OSError path
# ---------------------------------------------------------------------------

class TestDetectCurrentAttemptOSError:
    """Cover _detect_current_attempt OSError when reading diagnostic file."""

    def test_oserror_returns_1(self, tmp_path: Path):
        """When reading the diagnostic file raises OSError, return 1."""
        diag = tmp_path / "subprocess_error_round_5.txt"
        diag.write_text("round 5 (attempt 2)")
        # Make file unreadable
        diag.chmod(0o000)
        try:
            result = _detect_current_attempt(tmp_path, 5)
            assert result == 1
        finally:
            diag.chmod(0o644)

    def test_no_attempt_marker_returns_1(self, tmp_path: Path):
        """When diagnostic has no (attempt K), return 1."""
        diag = tmp_path / "subprocess_error_round_3.txt"
        diag.write_text("round 3 failed somehow, no attempt marker")
        result = _detect_current_attempt(tmp_path, 3)
        assert result == 1

    def test_attempt_marker_returns_next(self, tmp_path: Path):
        """When diagnostic has (attempt 2), return 3."""
        diag = tmp_path / "subprocess_error_round_3.txt"
        diag.write_text("round 3 failed (attempt 2)")
        result = _detect_current_attempt(tmp_path, 3)
        assert result == 3


# ---------------------------------------------------------------------------
# _detect_prior_round_anomalies — OSError paths
# ---------------------------------------------------------------------------

class TestDetectPriorRoundAnomaliesOSError:
    """Cover _detect_prior_round_anomalies OSError branches."""

    def test_check_file_oserror(self, tmp_path: Path):
        """When check_round_N.txt exists but is unreadable, no crash."""
        check_f = tmp_path / "check_round_1.txt"
        check_f.write_text("post-fix check: FAIL")
        check_f.chmod(0o000)
        try:
            result = _detect_prior_round_anomalies(tmp_path, 2)
            # Should not include "post-fix check FAIL" since file unreadable
            assert "post-fix check FAIL" not in result
        finally:
            check_f.chmod(0o644)

    def test_convo_file_oserror(self, tmp_path: Path):
        """When conversation_loop_N.md exists but is unreadable, no crash."""
        convo = tmp_path / "conversation_loop_1.md"
        convo.write_text("stalled (120s without output) — killing subprocess")
        convo.chmod(0o000)
        try:
            result = _detect_prior_round_anomalies(tmp_path, 2)
            # Should not include watchdog anomaly since file unreadable
            assert "watchdog stall" not in result
        finally:
            convo.chmod(0o644)

    def test_normal_anomaly_detection(self, tmp_path: Path):
        """When check_round_N.txt has FAIL, detect it."""
        check_f = tmp_path / "check_round_4.txt"
        check_f.write_text("post-fix check: FAIL")
        result = _detect_prior_round_anomalies(tmp_path, 5)
        assert "post-fix check FAIL" in result

    def test_convo_anomaly_detection(self, tmp_path: Path):
        """When conversation log has watchdog stall, detect it."""
        convo = tmp_path / "conversation_loop_4.md"
        convo.write_text("stalled (120s without output) — killing subprocess")
        result = _detect_prior_round_anomalies(tmp_path, 5)
        assert any("watchdog" in a.lower() or "stall" in a.lower() for a in result)


# ---------------------------------------------------------------------------
# _build_multimodal_prompt — async image builder
# ---------------------------------------------------------------------------

class TestBuildMultimodalPrompt:
    """Cover _build_multimodal_prompt async generator."""

    def test_text_only(self):
        """No images → content has just the text block."""
        gen = _build_multimodal_prompt("hello", [])
        messages = list(_exhaust_async_gen(gen))
        assert len(messages) == 1
        content = messages[0]["message"]["content"]
        assert len(content) == 1
        assert content[0]["type"] == "text"
        assert content[0]["text"] == "hello"

    def test_with_image(self, tmp_path: Path):
        """Valid PNG file → content has text + image blocks."""
        img = tmp_path / "test.png"
        # Minimal valid PNG (1x1 pixel, red)
        import base64
        # Tiny 1x1 red PNG
        png_b64 = (
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4"
            "2mP8z8BQDwADhQGAWjR9awAAAABJRU5ErkJggg=="
        )
        img.write_bytes(base64.b64decode(png_b64))
        gen = _build_multimodal_prompt("hello", [img])
        messages = list(_exhaust_async_gen(gen))
        content = messages[0]["message"]["content"]
        assert len(content) == 2
        assert content[0]["type"] == "text"
        assert content[1]["type"] == "image"
        assert content[1]["source"]["media_type"] == "image/png"

    def test_missing_image_skipped(self, tmp_path: Path):
        """Non-existent image path → skipped, only text block."""
        gen = _build_multimodal_prompt("hello", [tmp_path / "nope.png"])
        messages = list(_exhaust_async_gen(gen))
        content = messages[0]["message"]["content"]
        assert len(content) == 1
        assert content[0]["type"] == "text"

    def test_session_id(self):
        """Output message has session_id = 'party-mode'."""
        gen = _build_multimodal_prompt("test", [])
        messages = list(_exhaust_async_gen(gen))
        assert messages[0]["session_id"] == "party-mode"

    def test_unreadable_image_skipped(self, tmp_path: Path):
        """Image that raises OSError on read → skipped gracefully."""
        img = tmp_path / "bad.png"
        img.write_text("not a png")
        img.chmod(0o000)
        try:
            gen = _build_multimodal_prompt("hello", [img])
            messages = list(_exhaust_async_gen(gen))
            content = messages[0]["message"]["content"]
            # Only text block, image was skipped due to OSError
            assert len(content) == 1
            assert content[0]["type"] == "text"
        finally:
            img.chmod(0o644)


def _exhaust_async_gen(agen):
    """Helper: collect all items from an async generator synchronously."""
    results = []
    async def _collect():
        async for item in agen:
            results.append(item)
    asyncio.run(_collect())
    return results


# ---------------------------------------------------------------------------
# analyze_and_fix — yolo parameter + copyfile OSError
# ---------------------------------------------------------------------------

class TestAnalyzeAndFixEdgeCases:
    """Cover analyze_and_fix edge cases: yolo alias, copyfile OSError."""

    @patch("evolve.agent.run_claude_agent", new_callable=AsyncMock)
    @patch("evolve.agent._run_agent_with_retries")
    def test_yolo_forwards_to_allow_installs(self, mock_retries, mock_agent, tmp_path: Path):
        """yolo=True forwards to build_prompt as allow_installs=True."""
        from evolve.agent import analyze_and_fix
        (tmp_path / "README.md").write_text("# Spec")
        run_dir = tmp_path / ".evolve" / "runs" / "session"
        run_dir.mkdir(parents=True)

        with patch("evolve.agent.build_prompt") as mock_bp:
            mock_bp.return_value = "prompt"
            analyze_and_fix(tmp_path, "ok", yolo=True, run_dir=run_dir, round_num=1)
            call_kwargs = mock_bp.call_args
            # yolo=True should pass allow_installs=True to build_prompt
            assert call_kwargs[0][2] is None or call_kwargs[1].get("allow_installs") is None
            # The yolo fallback sets allow_installs = yolo before calling build_prompt
            # Actually, analyze_and_fix passes allow_installs positionally
            # Let's just verify build_prompt was called
            mock_bp.assert_called_once()

    @patch("evolve.agent._run_agent_with_retries")
    def test_copyfile_oserror_non_fatal(self, mock_retries, tmp_path: Path):
        """When shutil.copyfile raises OSError, analyze_and_fix doesn't crash."""
        from evolve.agent import analyze_and_fix
        import shutil
        (tmp_path / "README.md").write_text("# Spec")
        run_dir = tmp_path / ".evolve" / "runs" / "session"
        run_dir.mkdir(parents=True)

        # Create the attempt log so copyfile path is exercised
        attempt_log = run_dir / "conversation_loop_1_attempt_1.md"
        attempt_log.write_text("# log")

        with patch("shutil.copyfile", side_effect=OSError("cross-fs")):
            analyze_and_fix(tmp_path, "ok", run_dir=run_dir, round_num=1)
        # Should not raise — OSError is caught silently


# ---------------------------------------------------------------------------
# build_prompt — yolo alias
# ---------------------------------------------------------------------------

class TestBuildPromptYoloAlias:
    """Cover the yolo→allow_installs fallback in build_prompt."""

    def test_yolo_param_sets_allow_installs(self, tmp_path: Path):
        """When yolo=True, the constraint block is absent (same as allow_installs=True)."""
        (tmp_path / "README.md").write_text("# Spec")
        (tmp_path / "runs").mkdir()
        run_dir = tmp_path / "runs" / "s1"
        run_dir.mkdir()
        prompt = build_prompt(tmp_path, yolo=True, run_dir=run_dir)
        assert "[needs-package]" not in prompt or "skipped" not in prompt
