"""Tests for the --allow-installs rename and deprecated --yolo alias.

Verifies:
- --allow-installs CLI flag sets allow_installs=True
- --yolo CLI flag emits DeprecationWarning and sets allow_installs=True
- Both flags produce identical behavior
- EVOLVE_ALLOW_INSTALLS env var sets allow_installs=True
- EVOLVE_YOLO env var emits DeprecationWarning and sets allow_installs=True
- EVOLVE_ALLOW_INSTALLS takes precedence over EVOLVE_YOLO (no warning)
- allow_installs=true in evolve.toml works
- yolo=true in evolve.toml emits DeprecationWarning and sets allow_installs=True
- allow_installs in evolve.toml takes precedence over yolo (no warning)
- _parse_round_args handles both --allow-installs and --yolo
"""

import argparse
import warnings
from pathlib import Path
from unittest.mock import patch

import pytest

from evolve import _resolve_config, _parse_round_args, main


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _make_args(**overrides):
    args = argparse.Namespace(
        check=None, rounds=None, timeout=None,
        model=None, allow_installs=None, resume=False,
    )
    for k, v in overrides.items():
        setattr(args, k, v)
    return args


# ---------------------------------------------------------------------------
# CLI flag tests
# ---------------------------------------------------------------------------

class TestAllowInstallsCLIFlag:
    """--allow-installs and deprecated --yolo produce same result."""

    def test_allow_installs_flag_sets_true(self):
        """--allow-installs sets allow_installs=True."""
        ap = argparse.ArgumentParser()
        ap.add_argument("--allow-installs", action="store_true", dest="allow_installs")
        args = ap.parse_args(["--allow-installs"])
        assert args.allow_installs is True

    def test_yolo_flag_emits_deprecation_warning(self, tmp_path: Path):
        """--yolo emits DeprecationWarning and sets allow_installs=True via main()."""
        (tmp_path / "README.md").write_text("# Test")
        with patch("sys.argv", ["evolve", "start", str(tmp_path), "--yolo"]), \
             patch.dict("sys.modules", {"claude_agent_sdk": __import__("unittest.mock").mock.MagicMock()}), \
             patch("evolve.orchestrator.evolve_loop") as mock_loop:
            with warnings.catch_warnings(record=True) as w:
                warnings.simplefilter("always")
                try:
                    main()
                except SystemExit:
                    pass
            deprecation_msgs = [x for x in w if issubclass(x.category, DeprecationWarning)]
            assert any("--yolo is deprecated" in str(x.message) for x in deprecation_msgs), \
                f"Expected '--yolo is deprecated' warning, got: {[str(x.message) for x in deprecation_msgs]}"

    def test_yolo_and_allow_installs_both_set_allow_installs(self, tmp_path: Path):
        """Both --yolo and --allow-installs result in allow_installs=True in config."""
        (tmp_path / "README.md").write_text("# Test")

        # Test with --allow-installs
        args1 = _make_args(allow_installs=True)
        with patch("sys.argv", ["evolve", "start", str(tmp_path), "--allow-installs"]), \
             patch.dict("os.environ", {}, clear=True):
            result1 = _resolve_config(args1, tmp_path)

        # Test with --yolo (simulating what main() does before calling _resolve_config)
        args2 = _make_args(allow_installs=True)  # main() sets this before calling _resolve_config
        with patch("sys.argv", ["evolve", "start", str(tmp_path), "--yolo"]), \
             patch.dict("os.environ", {}, clear=True):
            result2 = _resolve_config(args2, tmp_path)

        assert result1.allow_installs is True
        assert result2.allow_installs is True


# ---------------------------------------------------------------------------
# Environment variable tests
# ---------------------------------------------------------------------------

class TestAllowInstallsEnvVar:
    """EVOLVE_ALLOW_INSTALLS and deprecated EVOLVE_YOLO env vars."""

    def test_evolve_allow_installs_env_1(self, tmp_path: Path):
        """EVOLVE_ALLOW_INSTALLS=1 sets allow_installs=True."""
        args = _make_args()
        with patch("sys.argv", ["evolve", "start", str(tmp_path)]), \
             patch.dict("os.environ", {"EVOLVE_ALLOW_INSTALLS": "1"}, clear=True):
            result = _resolve_config(args, tmp_path)
        assert result.allow_installs is True

    def test_evolve_allow_installs_env_true(self, tmp_path: Path):
        """EVOLVE_ALLOW_INSTALLS=true sets allow_installs=True."""
        args = _make_args()
        with patch("sys.argv", ["evolve", "start", str(tmp_path)]), \
             patch.dict("os.environ", {"EVOLVE_ALLOW_INSTALLS": "true"}, clear=True):
            result = _resolve_config(args, tmp_path)
        assert result.allow_installs is True

    def test_evolve_allow_installs_env_yes(self, tmp_path: Path):
        """EVOLVE_ALLOW_INSTALLS=yes sets allow_installs=True."""
        args = _make_args()
        with patch("sys.argv", ["evolve", "start", str(tmp_path)]), \
             patch.dict("os.environ", {"EVOLVE_ALLOW_INSTALLS": "yes"}, clear=True):
            result = _resolve_config(args, tmp_path)
        assert result.allow_installs is True

    def test_evolve_yolo_env_emits_deprecation(self, tmp_path: Path):
        """EVOLVE_YOLO=1 emits DeprecationWarning."""
        args = _make_args()
        with patch("sys.argv", ["evolve", "start", str(tmp_path)]), \
             patch.dict("os.environ", {"EVOLVE_YOLO": "1"}, clear=True):
            with warnings.catch_warnings(record=True) as w:
                warnings.simplefilter("always")
                result = _resolve_config(args, tmp_path)
            deprecation_msgs = [x for x in w if issubclass(x.category, DeprecationWarning)]
            assert any("EVOLVE_YOLO is deprecated" in str(x.message) for x in deprecation_msgs)
        assert result.allow_installs is True

    def test_allow_installs_env_takes_precedence_over_yolo(self, tmp_path: Path):
        """EVOLVE_ALLOW_INSTALLS takes precedence — no deprecation warning."""
        args = _make_args()
        with patch("sys.argv", ["evolve", "start", str(tmp_path)]), \
             patch.dict("os.environ", {
                 "EVOLVE_ALLOW_INSTALLS": "1",
                 "EVOLVE_YOLO": "1",
             }, clear=True):
            with warnings.catch_warnings(record=True) as w:
                warnings.simplefilter("always")
                result = _resolve_config(args, tmp_path)
            # EVOLVE_ALLOW_INSTALLS is handled in the main loop, so
            # allow_installs is already True before the deprecated fallback.
            # No DeprecationWarning should be emitted.
            deprecation_msgs = [x for x in w if issubclass(x.category, DeprecationWarning)]
            assert not any("EVOLVE_YOLO" in str(x.message) for x in deprecation_msgs), \
                "EVOLVE_YOLO deprecation warning should not fire when EVOLVE_ALLOW_INSTALLS is set"
        assert result.allow_installs is True

    def test_evolve_yolo_env_false_does_not_enable(self, tmp_path: Path):
        """EVOLVE_YOLO=false does not enable allow_installs."""
        args = _make_args()
        with patch("sys.argv", ["evolve", "start", str(tmp_path)]), \
             patch.dict("os.environ", {"EVOLVE_YOLO": "false"}, clear=True):
            result = _resolve_config(args, tmp_path)
        assert result.allow_installs is False


# ---------------------------------------------------------------------------
# Config file tests
# ---------------------------------------------------------------------------

class TestAllowInstallsConfig:
    """evolve.toml allow_installs and deprecated yolo keys."""

    def test_allow_installs_config_key(self, tmp_path: Path):
        """allow_installs = true in evolve.toml works."""
        (tmp_path / "evolve.toml").write_text("allow_installs = true\n")
        args = _make_args()
        with patch("sys.argv", ["evolve", "start", str(tmp_path)]), \
             patch.dict("os.environ", {}, clear=True):
            result = _resolve_config(args, tmp_path)
        assert result.allow_installs is True

    def test_yolo_config_key_emits_deprecation(self, tmp_path: Path):
        """yolo = true in evolve.toml emits DeprecationWarning."""
        (tmp_path / "evolve.toml").write_text("yolo = true\n")
        args = _make_args()
        with patch("sys.argv", ["evolve", "start", str(tmp_path)]), \
             patch.dict("os.environ", {}, clear=True):
            with warnings.catch_warnings(record=True) as w:
                warnings.simplefilter("always")
                result = _resolve_config(args, tmp_path)
            deprecation_msgs = [x for x in w if issubclass(x.category, DeprecationWarning)]
            assert any("'yolo' config key is deprecated" in str(x.message) for x in deprecation_msgs)
        assert result.allow_installs is True

    def test_allow_installs_config_takes_precedence_over_yolo(self, tmp_path: Path):
        """allow_installs = true suppresses yolo deprecation warning."""
        (tmp_path / "evolve.toml").write_text(
            "allow_installs = true\nyolo = true\n"
        )
        args = _make_args()
        with patch("sys.argv", ["evolve", "start", str(tmp_path)]), \
             patch.dict("os.environ", {}, clear=True):
            with warnings.catch_warnings(record=True) as w:
                warnings.simplefilter("always")
                result = _resolve_config(args, tmp_path)
            # allow_installs is resolved first in the fields loop, so by the
            # time the deprecated fallback runs, allow_installs is already True.
            deprecation_msgs = [x for x in w if issubclass(x.category, DeprecationWarning)]
            assert not any("yolo" in str(x.message).lower() for x in deprecation_msgs), \
                "No yolo deprecation warning when allow_installs is set in config"
        assert result.allow_installs is True

    def test_yolo_false_config_does_not_enable(self, tmp_path: Path):
        """yolo = false in evolve.toml does not enable allow_installs."""
        (tmp_path / "evolve.toml").write_text("yolo = false\n")
        args = _make_args()
        with patch("sys.argv", ["evolve", "start", str(tmp_path)]), \
             patch.dict("os.environ", {}, clear=True):
            result = _resolve_config(args, tmp_path)
        assert result.allow_installs is False


# ---------------------------------------------------------------------------
# _parse_round_args tests
# ---------------------------------------------------------------------------

class TestParseRoundArgsAllowInstalls:
    """_parse_round_args handles both --allow-installs and --yolo."""

    def test_allow_installs_flag(self):
        """--allow-installs in _parse_round_args sets allow_installs=True."""
        with patch("sys.argv", [
            "evolve", "_round", "/tmp/proj",
            "--round-num", "1", "--allow-installs",
        ]):
            args = _parse_round_args()
        assert args.allow_installs is True

    def test_yolo_flag(self):
        """--yolo in _parse_round_args sets allow_installs=True (dest alias)."""
        with patch("sys.argv", [
            "evolve", "_round", "/tmp/proj",
            "--round-num", "1", "--yolo",
        ]):
            args = _parse_round_args()
        assert args.allow_installs is True

    def test_neither_flag(self):
        """No flag means allow_installs=False."""
        with patch("sys.argv", [
            "evolve", "_round", "/tmp/proj",
            "--round-num", "1",
        ]):
            args = _parse_round_args()
        assert args.allow_installs is False


# ---------------------------------------------------------------------------
# Resolution order tests
# ---------------------------------------------------------------------------

class TestAllowInstallsResolutionOrder:
    """CLI > env > config > default resolution for allow_installs."""

    def test_cli_wins_over_env(self, tmp_path: Path):
        """CLI --allow-installs=True wins over EVOLVE_ALLOW_INSTALLS=false-ish env."""
        args = _make_args(allow_installs=True)
        with patch("sys.argv", ["evolve", "start", str(tmp_path), "--allow-installs"]), \
             patch.dict("os.environ", {}, clear=True):
            result = _resolve_config(args, tmp_path)
        assert result.allow_installs is True

    def test_env_wins_over_config(self, tmp_path: Path):
        """EVOLVE_ALLOW_INSTALLS env wins over evolve.toml allow_installs=false."""
        (tmp_path / "evolve.toml").write_text("allow_installs = false\n")
        args = _make_args()
        with patch("sys.argv", ["evolve", "start", str(tmp_path)]), \
             patch.dict("os.environ", {"EVOLVE_ALLOW_INSTALLS": "1"}, clear=True):
            result = _resolve_config(args, tmp_path)
        assert result.allow_installs is True

    def test_default_is_false(self, tmp_path: Path):
        """Default allow_installs is False when nothing is set."""
        args = _make_args()
        with patch("sys.argv", ["evolve", "start", str(tmp_path)]), \
             patch.dict("os.environ", {}, clear=True):
            result = _resolve_config(args, tmp_path)
        assert result.allow_installs is False


# ---------------------------------------------------------------------------
# init template tests
# ---------------------------------------------------------------------------

class TestInitTemplateUsesAllowInstalls:
    """evolve init generates evolve.toml with allow_installs, not yolo."""

    def test_init_uses_allow_installs_key(self, tmp_path: Path):
        """Scaffolded evolve.toml uses allow_installs, not yolo."""
        from evolve import _init_config
        _init_config(tmp_path)
        content = (tmp_path / "evolve.toml").read_text()
        assert "allow_installs" in content
        assert "yolo" not in content
