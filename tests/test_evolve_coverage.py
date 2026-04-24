"""Coverage tests for evolve.py — _resolve_config env vars, main() dispatch, _check_deps."""

import argparse
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from evolve import _resolve_config, _load_config, _check_deps, _show_status, main


# ---------------------------------------------------------------------------
# _resolve_config — environment variable paths
# ---------------------------------------------------------------------------

class TestResolveConfigEnvVars:
    """Test _resolve_config env var resolution (lines 101-154)."""

    def _make_args(self, **overrides):
        args = argparse.Namespace(
            check=None, rounds=None, timeout=None,
            model=None, allow_installs=None, resume=False,
        )
        for k, v in overrides.items():
            setattr(args, k, v)
        return args

    def test_evolve_check_env(self, tmp_path: Path):
        """EVOLVE_CHECK env var sets check command."""
        args = self._make_args()
        with patch("sys.argv", ["evolve", "start", str(tmp_path)]), \
             patch.dict("os.environ", {"EVOLVE_CHECK": "npm test"}, clear=True):
            result = _resolve_config(args, tmp_path)
        assert result.check == "npm test"

    def test_evolve_rounds_env(self, tmp_path: Path):
        """EVOLVE_ROUNDS env var sets rounds."""
        args = self._make_args()
        with patch("sys.argv", ["evolve", "start", str(tmp_path)]), \
             patch.dict("os.environ", {"EVOLVE_ROUNDS": "25"}, clear=True):
            result = _resolve_config(args, tmp_path)
        assert result.rounds == 25

    def test_evolve_rounds_env_invalid(self, tmp_path: Path):
        """EVOLVE_ROUNDS with non-integer value — env branch is entered but ValueError caught."""
        args = self._make_args()
        with patch("sys.argv", ["evolve", "start", str(tmp_path)]), \
             patch.dict("os.environ", {"EVOLVE_ROUNDS": "not_a_number"}, clear=True):
            result = _resolve_config(args, tmp_path)
        # The env branch was entered (ValueError caught), rounds stays None
        assert result.rounds is None

    def test_evolve_timeout_env(self, tmp_path: Path):
        """EVOLVE_TIMEOUT env var sets timeout."""
        args = self._make_args()
        with patch("sys.argv", ["evolve", "start", str(tmp_path)]), \
             patch.dict("os.environ", {"EVOLVE_TIMEOUT": "600"}, clear=True):
            result = _resolve_config(args, tmp_path)
        assert result.timeout == 600

    def test_evolve_timeout_env_invalid(self, tmp_path: Path):
        """EVOLVE_TIMEOUT with non-integer — env branch entered but ValueError caught."""
        args = self._make_args()
        with patch("sys.argv", ["evolve", "start", str(tmp_path)]), \
             patch.dict("os.environ", {"EVOLVE_TIMEOUT": "abc"}, clear=True):
            result = _resolve_config(args, tmp_path)
        # The env branch was entered (ValueError caught), timeout stays None
        assert result.timeout is None

    def test_evolve_model_env(self, tmp_path: Path):
        """EVOLVE_MODEL env var sets model."""
        args = self._make_args()
        with patch("sys.argv", ["evolve", "start", str(tmp_path)]), \
             patch.dict("os.environ", {"EVOLVE_MODEL": "claude-sonnet-4-20250514"}, clear=True):
            result = _resolve_config(args, tmp_path)
        assert result.model == "claude-sonnet-4-20250514"

    def test_evolve_yolo_env_true(self, tmp_path: Path):
        """EVOLVE_YOLO=1 enables yolo."""
        args = self._make_args()
        with patch("sys.argv", ["evolve", "start", str(tmp_path)]), \
             patch.dict("os.environ", {"EVOLVE_YOLO": "1"}, clear=True):
            result = _resolve_config(args, tmp_path)
        assert result.allow_installs is True

    def test_evolve_yolo_env_yes(self, tmp_path: Path):
        """EVOLVE_YOLO=yes enables yolo."""
        args = self._make_args()
        with patch("sys.argv", ["evolve", "start", str(tmp_path)]), \
             patch.dict("os.environ", {"EVOLVE_YOLO": "yes"}, clear=True):
            result = _resolve_config(args, tmp_path)
        assert result.allow_installs is True

    def test_evolve_yolo_env_TRUE(self, tmp_path: Path):
        """EVOLVE_YOLO=TRUE (uppercase) enables yolo."""
        args = self._make_args()
        with patch("sys.argv", ["evolve", "start", str(tmp_path)]), \
             patch.dict("os.environ", {"EVOLVE_YOLO": "TRUE"}, clear=True):
            result = _resolve_config(args, tmp_path)
        assert result.allow_installs is True

    def test_cli_rounds_flag_wins(self, tmp_path: Path):
        """CLI --rounds flag wins over env and file config."""
        (tmp_path / "evolve.toml").write_text("rounds = 50\n")
        args = self._make_args(rounds=20)
        with patch("sys.argv", ["evolve", "start", str(tmp_path), "--rounds", "20"]), \
             patch.dict("os.environ", {"EVOLVE_ROUNDS": "30"}, clear=True):
            result = _resolve_config(args, tmp_path)
        assert result.rounds == 20

    def test_cli_timeout_flag_wins(self, tmp_path: Path):
        """CLI --timeout flag wins over env and file config."""
        args = self._make_args(timeout=120)
        with patch("sys.argv", ["evolve", "start", str(tmp_path), "--timeout", "120"]), \
             patch.dict("os.environ", {"EVOLVE_TIMEOUT": "600"}, clear=True):
            result = _resolve_config(args, tmp_path)
        assert result.timeout == 120

    def test_file_config_rounds(self, tmp_path: Path):
        """File config sets rounds when CLI and env are not set."""
        (tmp_path / "evolve.toml").write_text("rounds = 42\n")
        args = self._make_args()
        with patch("sys.argv", ["evolve", "start", str(tmp_path)]), \
             patch.dict("os.environ", {}, clear=True):
            result = _resolve_config(args, tmp_path)
        assert result.rounds == 42

    def test_file_config_timeout(self, tmp_path: Path):
        """File config sets timeout when CLI and env are not set."""
        (tmp_path / "evolve.toml").write_text("timeout = 999\n")
        args = self._make_args()
        with patch("sys.argv", ["evolve", "start", str(tmp_path)]), \
             patch.dict("os.environ", {}, clear=True):
            result = _resolve_config(args, tmp_path)
        assert result.timeout == 999

    def test_file_config_yolo(self, tmp_path: Path):
        """File config sets yolo when CLI and env are not set."""
        (tmp_path / "evolve.toml").write_text("yolo = true\n")
        args = self._make_args()
        with patch("sys.argv", ["evolve", "start", str(tmp_path)]), \
             patch.dict("os.environ", {}, clear=True):
            result = _resolve_config(args, tmp_path)
        assert result.allow_installs is True

    def test_all_env_vars_together(self, tmp_path: Path):
        """All env vars set simultaneously."""
        args = self._make_args()
        env = {
            "EVOLVE_CHECK": "cargo test",
            "EVOLVE_ROUNDS": "15",
            "EVOLVE_TIMEOUT": "120",
            "EVOLVE_MODEL": "claude-sonnet-4-20250514",
            "EVOLVE_YOLO": "true",
        }
        with patch("sys.argv", ["evolve", "start", str(tmp_path)]), \
             patch.dict("os.environ", env, clear=True):
            result = _resolve_config(args, tmp_path)
        assert result.check == "cargo test"
        assert result.rounds == 15
        assert result.timeout == 120
        assert result.model == "claude-sonnet-4-20250514"
        assert result.allow_installs is True

    def test_evolve_max_cost_env(self, tmp_path: Path):
        """EVOLVE_MAX_COST env var sets max_cost."""
        args = self._make_args()
        with patch("sys.argv", ["evolve", "start", str(tmp_path)]), \
             patch.dict("os.environ", {"EVOLVE_MAX_COST": "10.50"}, clear=True):
            result = _resolve_config(args, tmp_path)
        assert result.max_cost == 10.50

    def test_evolve_max_cost_env_invalid(self, tmp_path: Path):
        """EVOLVE_MAX_COST with non-float value falls through to default."""
        args = self._make_args()
        with patch("sys.argv", ["evolve", "start", str(tmp_path)]), \
             patch.dict("os.environ", {"EVOLVE_MAX_COST": "not_a_number"}, clear=True):
            result = _resolve_config(args, tmp_path)
        assert result.max_cost is None

    def test_evolve_max_cost_cli(self, tmp_path: Path):
        """CLI --max-cost takes precedence over env and file config."""
        args = self._make_args(max_cost=25.0)
        with patch("sys.argv", ["evolve", "start", str(tmp_path), "--max-cost", "25.0"]), \
             patch.dict("os.environ", {"EVOLVE_MAX_COST": "10.0"}, clear=True):
            result = _resolve_config(args, tmp_path)
        assert result.max_cost == 25.0

    def test_evolve_max_cost_file_config(self, tmp_path: Path):
        """max_cost_usd from evolve.toml is used when no CLI/env."""
        (tmp_path / "evolve.toml").write_text("max_cost_usd = 50.0\n")
        args = self._make_args()
        with patch("sys.argv", ["evolve", "start", str(tmp_path)]), \
             patch.dict("os.environ", {}, clear=True):
            result = _resolve_config(args, tmp_path)
        assert result.max_cost == 50.0

    def test_evolve_max_cost_default_none(self, tmp_path: Path):
        """max_cost defaults to None when not set anywhere."""
        args = self._make_args()
        with patch("sys.argv", ["evolve", "start", str(tmp_path)]), \
             patch.dict("os.environ", {}, clear=True):
            result = _resolve_config(args, tmp_path)
        assert result.max_cost is None


# ---------------------------------------------------------------------------
# _load_config — tomllib fallback paths
# ---------------------------------------------------------------------------

class TestLoadConfigFallback:
    def test_no_tomllib(self, tmp_path: Path):
        """Returns empty when tomllib/tomli not available."""
        (tmp_path / "evolve.toml").write_text("check = 'pytest'\n")

        real_import = __import__

        def mock_import(name, *args, **kwargs):
            if name in ("tomllib", "tomli"):
                raise ImportError(f"No module {name}")
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=mock_import):
            result = _load_config(tmp_path)
        assert result == {}


# ---------------------------------------------------------------------------
# _check_deps — various scenarios
# ---------------------------------------------------------------------------

class TestCheckDepsExtended:
    def test_sdk_importable(self):
        """When SDK is importable, returns without error."""
        mock_sdk = MagicMock()
        with patch.dict("sys.modules", {"claude_agent_sdk": mock_sdk}):
            _check_deps()  # Should not raise


# ---------------------------------------------------------------------------
# main() — subcommand dispatch
# ---------------------------------------------------------------------------

class TestMainDispatch:
    def test_init_command(self, tmp_path: Path):
        """main() dispatches init correctly."""
        target = tmp_path / "new_project"
        with patch("sys.argv", ["evolve", "init", str(target)]), \
             patch.dict("sys.modules", {"claude_agent_sdk": MagicMock()}):
            main()
        assert (target / "evolve.toml").is_file()

    def test_status_command(self, tmp_path: Path):
        """main() dispatches status correctly."""
        (tmp_path / "README.md").write_text("# Test")
        with patch("sys.argv", ["evolve", "status", str(tmp_path)]), \
             patch.dict("sys.modules", {"claude_agent_sdk": MagicMock()}):
            main()

    def test_clean_command(self, tmp_path: Path):
        """main() dispatches clean correctly."""
        runs = tmp_path / "runs"
        runs.mkdir()
        with patch("sys.argv", ["evolve", "clean", str(tmp_path), "--keep", "1"]), \
             patch.dict("sys.modules", {"claude_agent_sdk": MagicMock()}):
            main()

    def test_start_command_calls_evolve_loop(self, tmp_path: Path):
        """main() dispatches start to evolve_loop."""
        (tmp_path / "README.md").write_text("# Test")
        with patch("sys.argv", ["evolve", "start", str(tmp_path), "--rounds", "5"]), \
             patch.dict("sys.modules", {"claude_agent_sdk": MagicMock()}), \
             patch("evolve.orchestrator.evolve_loop") as mock_loop, \
             patch("evolve.orchestrator._ensure_git"), \
             patch("evolve.orchestrator._run_rounds"):
            try:
                main()
            except SystemExit:
                pass

    def test_round_command(self, tmp_path: Path):
        """_round internal command is dispatched."""
        (tmp_path / "README.md").write_text("# Test")
        runs = tmp_path / "runs"
        runs.mkdir()
        run_dir = runs / "session"
        run_dir.mkdir()

        with patch("sys.argv", [
            "evolve", "_round", str(tmp_path),
            "--round-num", "1", "--timeout", "60",
            "--run-dir", str(run_dir),
        ]), \
             patch.dict("sys.modules", {"claude_agent_sdk": MagicMock()}), \
             patch("evolve.orchestrator.subprocess.run", return_value=MagicMock(returncode=0, stdout="", stderr="")), \
             patch("evolve.agent.asyncio.run", side_effect=lambda coro: coro.close()):
            main()

    def test_history_command(self, tmp_path: Path):
        """main() dispatches history correctly (line 293)."""
        runs = tmp_path / "runs"
        session = runs / "20260101_000000"
        session.mkdir(parents=True)
        (session / "CONVERGED").write_text("Done")
        with patch("sys.argv", ["evolve", "history", str(tmp_path)]), \
             patch.dict("sys.modules", {"claude_agent_sdk": MagicMock()}):
            main()

    def test_start_validate_dispatch(self, tmp_path: Path):
        """main() dispatches --validate to run_validate (lines 261-262)."""
        (tmp_path / "README.md").write_text("# Test")
        mock_validate = MagicMock(return_value=0)
        with patch("sys.argv", ["evolve", "start", str(tmp_path), "--validate"]), \
             patch.dict("sys.modules", {"claude_agent_sdk": MagicMock()}), \
             patch("evolve.orchestrator.run_validate", mock_validate):
            with pytest.raises(SystemExit) as exc:
                main()
            assert exc.value.code == 0
        mock_validate.assert_called_once()

    def test_start_dry_run_dispatch(self, tmp_path: Path):
        """main() dispatches --dry-run to run_dry_run (lines 269-270)."""
        (tmp_path / "README.md").write_text("# Test")
        mock_dry_run = MagicMock()
        with patch("sys.argv", ["evolve", "start", str(tmp_path), "--dry-run"]), \
             patch.dict("sys.modules", {"claude_agent_sdk": MagicMock()}), \
             patch("evolve.orchestrator.run_dry_run", mock_dry_run):
            main()
        mock_dry_run.assert_called_once()

    def test_start_json_flag_sets_tui(self, tmp_path: Path):
        """main() sets evolve.tui._use_json when --json is passed."""
        (tmp_path / "README.md").write_text("# Test")
        mock_loop = MagicMock()
        import evolve.tui as _tui_mod
        original = getattr(_tui_mod, '_use_json', False)
        try:
            with patch("sys.argv", ["evolve", "start", str(tmp_path), "--json"]), \
                 patch.dict("sys.modules", {"claude_agent_sdk": MagicMock()}), \
                 patch("evolve.orchestrator.evolve_loop", mock_loop):
                main()
            assert _tui_mod._use_json is True
        finally:
            _tui_mod._use_json = original


# ---------------------------------------------------------------------------
# _load_config — pyproject.toml exception path (lines 62-63)
# ---------------------------------------------------------------------------

class TestLoadConfigPyprojectException:
    def test_malformed_pyproject_toml(self, tmp_path: Path):
        """Malformed pyproject.toml returns empty dict (lines 62-63)."""
        (tmp_path / "pyproject.toml").write_text("this is {{not valid toml!!!")
        result = _load_config(tmp_path)
        assert result == {}


# ---------------------------------------------------------------------------
# _check_deps — venv-related message paths (lines 177-180, 185-190)
# ---------------------------------------------------------------------------

class TestCheckDepsVenvPaths:
    def test_venv_exists_not_in_venv(self, tmp_path: Path, capsys):
        """_check_deps prints venv activation instructions when venv exists but not active (lines 177-180)."""
        # Create a fake venv directory where _check_deps expects it
        evolve_file = Path(__file__).parent.parent / "evolve.py"
        evolve_dir = evolve_file.parent
        venv_dir = evolve_dir / ".venv"
        venv_existed = venv_dir.is_dir()

        if not venv_existed:
            venv_dir.mkdir(exist_ok=True)

        try:
            # Make import of claude_agent_sdk fail
            real_import = __import__
            def mock_import(name, *args, **kwargs):
                if name == "claude_agent_sdk":
                    raise ImportError("no module")
                return real_import(name, *args, **kwargs)

            # Ensure sys.prefix does NOT contain the venv path
            with patch("builtins.__import__", side_effect=mock_import), \
                 patch.object(sys, "prefix", "/usr"):
                with pytest.raises(SystemExit) as exc:
                    _check_deps()
                assert exc.value.code == 2

            captured = capsys.readouterr()
            assert "virtual environment exists" in captured.out
            assert "source" in captured.out
        finally:
            if not venv_existed:
                venv_dir.rmdir()

    def test_no_venv_exists(self, tmp_path: Path, capsys):
        """_check_deps prints setup instructions when no venv exists (lines 185-190)."""
        evolve_file = Path(__file__).parent.parent / "evolve.py"
        evolve_dir = evolve_file.parent
        venv_dir = evolve_dir / ".venv"

        # Temporarily rename venv if it exists
        renamed = False
        backup_path = evolve_dir / ".venv_backup_test"
        if venv_dir.is_dir():
            venv_dir.rename(backup_path)
            renamed = True

        try:
            real_import = __import__
            def mock_import(name, *args, **kwargs):
                if name == "claude_agent_sdk":
                    raise ImportError("no module")
                return real_import(name, *args, **kwargs)

            with patch("builtins.__import__", side_effect=mock_import), \
                 patch.object(sys, "prefix", "/usr"):
                with pytest.raises(SystemExit) as exc:
                    _check_deps()
                assert exc.value.code == 2

            captured = capsys.readouterr()
            assert "Set up a virtual environment" in captured.out
            assert "python3 -m venv .venv" in captured.out
        finally:
            if renamed:
                backup_path.rename(venv_dir)
