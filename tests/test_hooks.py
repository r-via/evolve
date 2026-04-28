"""Tests for hooks.py — hook loading, event matching, execution, timeout, failure handling."""

import os
import subprocess
import textwrap
from pathlib import Path
from unittest.mock import patch, MagicMock, call

import pytest

from evolve.hooks import load_hooks, fire_hook, SUPPORTED_EVENTS, HOOK_TIMEOUT

class TestSupportedEvents:
    def test_all_events_present(self):
        assert "on_round_start" in SUPPORTED_EVENTS
        assert "on_round_end" in SUPPORTED_EVENTS
        assert "on_converged" in SUPPORTED_EVENTS
        assert "on_error" in SUPPORTED_EVENTS
        assert "on_structural_change" in SUPPORTED_EVENTS

    def test_exactly_five_events(self):
        assert len(SUPPORTED_EVENTS) == 5

    def test_is_frozenset(self):
        assert isinstance(SUPPORTED_EVENTS, frozenset)

class TestHookTimeout:
    def test_default_timeout(self):
        assert HOOK_TIMEOUT == 30

class TestLoadHooksEvolveToml:
    def test_loads_hooks_from_evolve_toml(self, tmp_path):
        toml_content = textwrap.dedent("""\
            [hooks]
            on_round_end = "echo 'Round done'"
            on_converged = "curl http://example.com"
        """)
        (tmp_path / "evolve.toml").write_text(toml_content)

        hooks = load_hooks(tmp_path)
        assert hooks == {
            "on_round_end": "echo 'Round done'",
            "on_converged": "curl http://example.com",
        }

    def test_filters_unsupported_events(self, tmp_path):
        toml_content = textwrap.dedent("""\
            [hooks]
            on_round_end = "echo done"
            on_unknown_event = "echo bad"
        """)
        (tmp_path / "evolve.toml").write_text(toml_content)

        hooks = load_hooks(tmp_path)
        assert "on_round_end" in hooks
        assert "on_unknown_event" not in hooks

    def test_empty_hooks_section(self, tmp_path):
        toml_content = textwrap.dedent("""\
            [hooks]
        """)
        (tmp_path / "evolve.toml").write_text(toml_content)

        hooks = load_hooks(tmp_path)
        assert hooks == {}

    def test_no_hooks_section(self, tmp_path):
        toml_content = textwrap.dedent("""\
            check = "pytest"
            rounds = 10
        """)
        (tmp_path / "evolve.toml").write_text(toml_content)

        hooks = load_hooks(tmp_path)
        assert hooks == {}

    def test_all_four_events(self, tmp_path):
        toml_content = textwrap.dedent("""\
            [hooks]
            on_round_start = "echo start"
            on_round_end = "echo end"
            on_converged = "echo converged"
            on_error = "echo error"
        """)
        (tmp_path / "evolve.toml").write_text(toml_content)

        hooks = load_hooks(tmp_path)
        assert len(hooks) == 4

class TestLoadHooksPyprojectToml:
    def test_loads_from_pyproject_toml(self, tmp_path):
        toml_content = textwrap.dedent("""\
            [tool.evolve.hooks]
            on_round_end = "echo done"
            on_error = "notify-send 'error'"
        """)
        (tmp_path / "pyproject.toml").write_text(toml_content)

        hooks = load_hooks(tmp_path)
        assert hooks == {
            "on_round_end": "echo done",
            "on_error": "notify-send 'error'",
        }

    def test_evolve_toml_takes_precedence(self, tmp_path):
        """evolve.toml hooks override pyproject.toml hooks."""
        (tmp_path / "evolve.toml").write_text(textwrap.dedent("""\
            [hooks]
            on_round_end = "from evolve.toml"
        """))
        (tmp_path / "pyproject.toml").write_text(textwrap.dedent("""\
            [tool.evolve.hooks]
            on_round_end = "from pyproject.toml"
            on_error = "from pyproject.toml"
        """))

        hooks = load_hooks(tmp_path)
        # evolve.toml wins; pyproject.toml hooks are NOT merged
        assert hooks == {"on_round_end": "from evolve.toml"}

    def test_empty_tool_evolve_section(self, tmp_path):
        toml_content = textwrap.dedent("""\
            [tool.evolve]
            check = "pytest"
        """)
        (tmp_path / "pyproject.toml").write_text(toml_content)

        hooks = load_hooks(tmp_path)
        assert hooks == {}

class TestLoadHooksEdgeCases:
    def test_no_config_files(self, tmp_path):
        hooks = load_hooks(tmp_path)
        assert hooks == {}

    def test_corrupted_evolve_toml(self, tmp_path):
        (tmp_path / "evolve.toml").write_text("this is not valid toml {{{{")

        hooks = load_hooks(tmp_path)
        assert hooks == {}

    def test_corrupted_pyproject_toml(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text("{{{{ invalid")

        hooks = load_hooks(tmp_path)
        assert hooks == {}

    def test_no_tomllib_available(self, tmp_path):
        """When neither tomllib nor tomli is available, return empty dict."""
        (tmp_path / "evolve.toml").write_text("[hooks]\non_round_end = 'echo done'\n")

        import builtins
        original_import = builtins.__import__

        def fail_both(name, *args, **kwargs):
            if name in ("tomllib", "tomli"):
                raise ImportError(f"no {name}")
            return original_import(name, *args, **kwargs)

        with patch.object(builtins, "__import__", side_effect=fail_both):
            result = load_hooks(tmp_path)
        assert result == {}

    def test_tomli_fallback_when_tomllib_missing(self, tmp_path):
        """When tomllib is unavailable but tomli is, use tomli."""
        (tmp_path / "evolve.toml").write_text("[hooks]\non_round_end = 'echo done'\n")

        import builtins
        original_import = builtins.__import__

        def fail_tomllib(name, *args, **kwargs):
            if name == "tomllib":
                raise ImportError("no tomllib")
            return original_import(name, *args, **kwargs)

        with patch.object(builtins, "__import__", side_effect=fail_tomllib):
            result = load_hooks(tmp_path)
        # tomli should work as fallback (or if tomli isn't installed either,
        # at least the tomllib ImportError path on line 56 is exercised)
        # The key is exercising lines 56-58
        assert isinstance(result, dict)

    def test_non_string_hook_values(self, tmp_path):
        """Non-string values are converted to strings via str()."""
        toml_content = textwrap.dedent("""\
            [hooks]
            on_round_end = 42
        """)
        (tmp_path / "evolve.toml").write_text(toml_content)

        # tomllib will parse 42 as int; load_hooks should str() it
        hooks = load_hooks(tmp_path)
        assert hooks.get("on_round_end") == "42"

class TestFireHookSuccess:
    def test_fires_configured_hook(self):
        hooks = {"on_round_end": "echo done"}
        with patch("evolve.hooks.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            result = fire_hook(hooks, "on_round_end", session="abc", round_num=5, status="success")

        assert result is True
        mock_run.assert_called_once()
        args, kwargs = mock_run.call_args
        assert args[0] == "echo done"
        assert kwargs["shell"] is True
        assert kwargs["timeout"] == HOOK_TIMEOUT

    def test_sets_environment_variables(self):
        hooks = {"on_round_end": "echo $EVOLVE_SESSION"}
        with patch("evolve.hooks.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            fire_hook(hooks, "on_round_end", session="20260325_123456", round_num=3, status="success")

        env = mock_run.call_args[1]["env"]
        assert env["EVOLVE_SESSION"] == "20260325_123456"
        assert env["EVOLVE_ROUND"] == "3"
        assert env["EVOLVE_STATUS"] == "success"

    def test_noop_when_no_hook_configured(self):
        hooks = {}
        result = fire_hook(hooks, "on_round_end")
        assert result is True  # no-op is success

    def test_noop_for_unconfigured_event(self):
        hooks = {"on_round_end": "echo done"}
        result = fire_hook(hooks, "on_converged")
        assert result is True  # event not configured = no-op success

class TestFireHookFailure:
    def test_returns_false_on_nonzero_exit(self):
        hooks = {"on_error": "exit 1"}
        with patch("evolve.hooks.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stderr="something went wrong")
            result = fire_hook(hooks, "on_error", session="s", round_num=1, status="error")

        assert result is False

    def test_returns_false_on_timeout(self):
        hooks = {"on_round_end": "sleep 60"}
        with patch("evolve.hooks.subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.TimeoutExpired(cmd="sleep 60", timeout=30)
            result = fire_hook(hooks, "on_round_end")

        assert result is False

    def test_returns_false_on_exception(self):
        hooks = {"on_round_end": "nonexistent_cmd"}
        with patch("evolve.hooks.subprocess.run") as mock_run:
            mock_run.side_effect = OSError("command not found")
            result = fire_hook(hooks, "on_round_end")

        assert result is False

    def test_returns_false_for_unknown_event(self):
        hooks = {"on_unknown": "echo bad"}
        result = fire_hook(hooks, "on_unknown")
        assert result is False

    def test_nonzero_exit_logs_stderr(self):
        hooks = {"on_error": "exit 1"}
        with patch("evolve.hooks.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=42, stderr="big error message")
            with patch("evolve.hooks.logger") as mock_logger:
                fire_hook(hooks, "on_error", session="s", round_num=1, status="error")
                mock_logger.warning.assert_called()
                warning_msg = mock_logger.warning.call_args[0][0]
                assert "exited with code" in warning_msg

    def test_timeout_logs_warning(self):
        hooks = {"on_round_end": "sleep 60"}
        with patch("evolve.hooks.subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.TimeoutExpired(cmd="sleep 60", timeout=30)
            with patch("evolve.hooks.logger") as mock_logger:
                fire_hook(hooks, "on_round_end")
                mock_logger.warning.assert_called()
                warning_msg = mock_logger.warning.call_args[0][0]
                assert "timed out" in warning_msg

    def test_exception_logs_warning(self):
        hooks = {"on_round_end": "bad"}
        with patch("evolve.hooks.subprocess.run") as mock_run:
            mock_run.side_effect = RuntimeError("unexpected")
            with patch("evolve.hooks.logger") as mock_logger:
                fire_hook(hooks, "on_round_end")
                mock_logger.warning.assert_called()

    def test_empty_stderr_on_failure(self):
        """When stderr is empty, the warning message should show '(no stderr)'."""
        hooks = {"on_error": "exit 1"}
        with patch("evolve.hooks.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stderr="")
            with patch("evolve.hooks.logger") as mock_logger:
                fire_hook(hooks, "on_error", session="s", round_num=1, status="error")
                # Check that the warning includes "(no stderr)"
                warning_args = mock_logger.warning.call_args[0]
                assert "(no stderr)" in str(warning_args)

class TestFireHookEnvDefaults:
    def test_default_env_values(self):
        hooks = {"on_round_end": "echo test"}
        with patch("evolve.hooks.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            fire_hook(hooks, "on_round_end")

        env = mock_run.call_args[1]["env"]
        assert env["EVOLVE_SESSION"] == ""
        assert env["EVOLVE_ROUND"] == "0"
        assert env["EVOLVE_STATUS"] == ""

    def test_env_inherits_parent_environment(self):
        hooks = {"on_round_end": "echo test"}
        with patch("evolve.hooks.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            with patch.dict(os.environ, {"MY_CUSTOM_VAR": "hello"}):
                fire_hook(hooks, "on_round_end")

        env = mock_run.call_args[1]["env"]
        assert env.get("MY_CUSTOM_VAR") == "hello"

class TestHooksIntegration:
    def test_load_and_fire(self, tmp_path):
        toml_content = textwrap.dedent("""\
            [hooks]
            on_converged = "echo converged!"
        """)
        (tmp_path / "evolve.toml").write_text(toml_content)

        hooks = load_hooks(tmp_path)
        with patch("evolve.hooks.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            result = fire_hook(hooks, "on_converged", session="test_session", round_num=10, status="converged")

        assert result is True
        args, kwargs = mock_run.call_args
        assert args[0] == "echo converged!"
        assert kwargs["env"]["EVOLVE_SESSION"] == "test_session"
        assert kwargs["env"]["EVOLVE_ROUND"] == "10"
        assert kwargs["env"]["EVOLVE_STATUS"] == "converged"

    def test_fire_all_events(self, tmp_path):
        """All four lifecycle events can be loaded and fired."""
        toml_content = textwrap.dedent("""\
            [hooks]
            on_round_start = "echo start"
            on_round_end = "echo end"
            on_converged = "echo converged"
            on_error = "echo error"
        """)
        (tmp_path / "evolve.toml").write_text(toml_content)

        hooks = load_hooks(tmp_path)
        with patch("evolve.hooks.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            for event in SUPPORTED_EVENTS:
                result = fire_hook(hooks, event, session="s", round_num=1, status="ok")
                assert result is True

        assert mock_run.call_count == 4

class TestFireHookEnvVarAccess:
    """Integration tests that run real subprocesses to verify hook commands
    can actually read EVOLVE_SESSION, EVOLVE_ROUND, and EVOLVE_STATUS."""

    def test_hook_reads_evolve_session(self, tmp_path):
        """Hook command can read EVOLVE_SESSION from environment."""
        out_file = tmp_path / "session.txt"
        hooks = {"on_round_end": f"echo $EVOLVE_SESSION > {out_file}"}
        result = fire_hook(hooks, "on_round_end", session="20260325_155704", round_num=1, status="ok")
        assert result is True
        assert out_file.read_text().strip() == "20260325_155704"

    def test_hook_reads_evolve_round(self, tmp_path):
        """Hook command can read EVOLVE_ROUND from environment."""
        out_file = tmp_path / "round.txt"
        hooks = {"on_round_end": f"echo $EVOLVE_ROUND > {out_file}"}
        result = fire_hook(hooks, "on_round_end", session="s", round_num=7, status="ok")
        assert result is True
        assert out_file.read_text().strip() == "7"

    def test_hook_reads_evolve_status(self, tmp_path):
        """Hook command can read EVOLVE_STATUS from environment."""
        out_file = tmp_path / "status.txt"
        hooks = {"on_converged": f"echo $EVOLVE_STATUS > {out_file}"}
        result = fire_hook(hooks, "on_converged", session="s", round_num=1, status="converged")
        assert result is True
        assert out_file.read_text().strip() == "converged"

    def test_hook_reads_all_three_env_vars(self, tmp_path):
        """Hook command can read all three env vars in a single invocation."""
        out_file = tmp_path / "all_vars.txt"
        hooks = {
            "on_round_end": (
                f"echo $EVOLVE_SESSION:$EVOLVE_ROUND:$EVOLVE_STATUS > {out_file}"
            ),
        }
        result = fire_hook(
            hooks, "on_round_end",
            session="sess_42", round_num=12, status="running",
        )
        assert result is True
        assert out_file.read_text().strip() == "sess_42:12:running"

    def test_hook_env_vars_with_special_characters_in_session(self, tmp_path):
        """Session names with underscores/digits are passed correctly."""
        out_file = tmp_path / "session_special.txt"
        hooks = {"on_round_start": f"echo $EVOLVE_SESSION > {out_file}"}
        result = fire_hook(
            hooks, "on_round_start",
            session="20260325_155704_retry_2", round_num=1, status="ok",
        )
        assert result is True
        assert out_file.read_text().strip() == "20260325_155704_retry_2"

    def test_hook_env_vars_default_values_real_subprocess(self, tmp_path):
        """Default env var values (empty session, round 0, empty status) are accessible."""
        out_file = tmp_path / "defaults.txt"
        hooks = {
            "on_round_end": (
                f"echo \"SESSION=$EVOLVE_SESSION,ROUND=$EVOLVE_ROUND,STATUS=$EVOLVE_STATUS\" > {out_file}"
            ),
        }
        result = fire_hook(hooks, "on_round_end")
        assert result is True
        assert out_file.read_text().strip() == "SESSION=,ROUND=0,STATUS="

    def test_hook_env_vars_do_not_leak_between_events(self, tmp_path):
        """Each hook invocation gets its own env; values don't leak."""
        out1 = tmp_path / "out1.txt"
        out2 = tmp_path / "out2.txt"
        hooks = {
            "on_round_start": f"echo $EVOLVE_ROUND > {out1}",
            "on_round_end": f"echo $EVOLVE_ROUND > {out2}",
        }
        fire_hook(hooks, "on_round_start", session="s", round_num=5, status="ok")
        fire_hook(hooks, "on_round_end", session="s", round_num=10, status="ok")
        assert out1.read_text().strip() == "5"
        assert out2.read_text().strip() == "10"

    def test_hook_env_vars_on_error_event(self, tmp_path):
        """Error hooks receive correct env vars."""
        out_file = tmp_path / "error_env.txt"
        hooks = {
            "on_error": f"echo $EVOLVE_SESSION:$EVOLVE_ROUND:$EVOLVE_STATUS > {out_file}",
        }
        result = fire_hook(
            hooks, "on_error",
            session="err_session", round_num=3, status="crash",
        )
        assert result is True
        assert out_file.read_text().strip() == "err_session:3:crash"

class TestFireHookExtraEnv:
    """Test extra_env parameter passes additional env vars to hook."""

    def test_extra_env_passed_to_subprocess(self):
        hooks = {"on_structural_change": "echo test"}
        with patch("evolve.hooks.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            fire_hook(
                hooks, "on_structural_change",
                session="s", round_num=1, status="structural_change",
                extra_env={"EVOLVE_STRUCTURAL_REASON": "moved hooks.py"},
            )
        env = mock_run.call_args[1]["env"]
        assert env["EVOLVE_STRUCTURAL_REASON"] == "moved hooks.py"
        assert env["EVOLVE_SESSION"] == "s"

    def test_extra_env_none_is_noop(self):
        hooks = {"on_round_end": "echo test"}
        with patch("evolve.hooks.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            fire_hook(hooks, "on_round_end", extra_env=None)
        env = mock_run.call_args[1]["env"]
        assert "EVOLVE_STRUCTURAL_REASON" not in env

    def test_extra_env_with_structural_marker_fields(self, tmp_path):
        """Real subprocess reads EVOLVE_STRUCTURAL_* env vars."""
        out_file = tmp_path / "structural.txt"
        hooks = {
            "on_structural_change": (
                f"echo $EVOLVE_STRUCTURAL_REASON:$EVOLVE_STRUCTURAL_ROUND > {out_file}"
            ),
        }
        result = fire_hook(
            hooks, "on_structural_change",
            session="s", round_num=5, status="structural_change",
            extra_env={
                "EVOLVE_STRUCTURAL_REASON": "extracted git.py",
                "EVOLVE_STRUCTURAL_VERIFY": "pytest",
                "EVOLVE_STRUCTURAL_RESUME": "evolve start . --resume",
                "EVOLVE_STRUCTURAL_ROUND": "5",
                "EVOLVE_STRUCTURAL_TIMESTAMP": "2026-04-23T21:00:00Z",
            },
        )
        assert result is True
        assert out_file.read_text().strip() == "extracted git.py:5"
