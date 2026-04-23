"""Branch-coverage tests for loop.py targeting specific uncovered lines.

Each test class docstring lists the loop.py line range it exercises. These
are edge-case branches that other test suites don't reach: OSError handling,
subprocess error paths, deprecated aliases, and corrupted filename inputs.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import loop
from loop import (
    _audit_readme_sync,
    _auto_detect_check,
    _extract_spec_claims,
    _get_current_improvement,
    _git_show_at,
    evolve_loop,
    run_dry_run,
    run_single_round,
    run_validate,
)


def _close_coro(coro):
    """asyncio.run side-effect that closes the coro to silence warnings."""
    coro.close()


# ---------------------------------------------------------------------------
# _auto_detect_check — Makefile read OSError (lines 71-72)
# ---------------------------------------------------------------------------

class TestAutoDetectMakefileOSError:
    def test_makefile_unreadable_returns_none(self, tmp_path: Path):
        """OSError while reading Makefile does NOT crash — returns None."""
        # Makefile present but raises OSError on read
        (tmp_path / "Makefile").write_text("test:\n\techo ok\n")

        with patch("shutil.which", side_effect=lambda name: "/usr/bin/make" if name == "make" else None), \
             patch("loop.Path.read_text", side_effect=OSError("permission denied")):
            # Monkey-patch read_text just for Makefile path — simplest: patch on the method
            result = _auto_detect_check(tmp_path)

        # OSError swallowed — no pytest/npm/cargo/go available in test env, falls through to None
        assert result is None


# ---------------------------------------------------------------------------
# _extract_spec_claims — `$` without space prefix (line 325)
# ---------------------------------------------------------------------------

class TestExtractSpecClaimsDollarNoSpace:
    def test_dollar_without_space_prefix_is_stripped(self):
        """Shell examples with `$cmd` (no space) are normalized and parsed."""
        spec = (
            "# Title\n"
            "\n"
            "```bash\n"
            "$evolve start\n"
            "```\n"
        )
        claims = _extract_spec_claims(spec)
        # `$evolve start` → stripped to `evolve start` via line 325 branch
        shell_claims = [c for c in claims if c[1] == "shell_example"]
        assert any(c[0].startswith("evolve start") for c in shell_claims)


# ---------------------------------------------------------------------------
# _audit_readme_sync — OSError reading spec/readme (lines 409-410)
# ---------------------------------------------------------------------------

class TestAuditReadmeSyncOSError:
    def test_oserror_returns_zero_items(self, tmp_path: Path):
        """OSError during read returns 0 — audit is a no-op, not a crash."""
        spec = tmp_path / "SPEC.md"
        spec.write_text("# Spec\n\n### The --foo flag\n")
        readme = tmp_path / "README.md"
        readme.write_text("# Readme\n")
        improvements = tmp_path / "improvements.md"
        improvements.write_text("# Improvements\n")

        # Make both files raise OSError on read by patching Path.read_text.
        original_read_text = Path.read_text

        def _mock_read(self: Path, *args, **kwargs):
            if self.name in ("SPEC.md", "README.md"):
                raise OSError("permission denied")
            return original_read_text(self, *args, **kwargs)

        with patch.object(Path, "read_text", _mock_read):
            result = _audit_readme_sync(tmp_path, improvements, spec="SPEC.md")

        assert result == 0


# ---------------------------------------------------------------------------
# _git_show_at — subprocess error handling (lines 666-667)
# ---------------------------------------------------------------------------

class TestGitShowAtSubprocessError:
    def test_subprocess_error_returns_none(self, tmp_path: Path):
        """SubprocessError during git show returns None."""
        with patch(
            "loop.subprocess.run",
            side_effect=subprocess.SubprocessError("boom"),
        ):
            assert _git_show_at(tmp_path, "HEAD", "runs/improvements.md") is None

    def test_filenotfound_returns_none(self, tmp_path: Path):
        """FileNotFoundError (git binary missing) returns None."""
        with patch(
            "loop.subprocess.run",
            side_effect=FileNotFoundError("no git"),
        ):
            assert _git_show_at(tmp_path, "HEAD", "runs/improvements.md") is None

    def test_os_error_returns_none(self, tmp_path: Path):
        """Generic OSError returns None."""
        with patch("loop.subprocess.run", side_effect=OSError("io error")):
            assert _git_show_at(tmp_path, "HEAD", "runs/improvements.md") is None


# ---------------------------------------------------------------------------
# _get_current_improvement — yolo deprecated alias (line 834)
# ---------------------------------------------------------------------------

class TestGetCurrentImprovementYoloAlias:
    def test_yolo_true_overrides_allow_installs(self, tmp_path: Path):
        """yolo=True is applied as allow_installs=True (line 833-834 branch)."""
        imp = tmp_path / "improvements.md"
        imp.write_text(
            "- [ ] [functional] [needs-package] install httpx\n"
            "- [ ] [functional] regular item\n"
        )
        # Without yolo alias: skips needs-package → returns regular item
        assert _get_current_improvement(imp, allow_installs=False) == "[functional] regular item"
        # With yolo=True (deprecated): takes needs-package item
        result = _get_current_improvement(imp, allow_installs=False, yolo=True)
        assert "[needs-package]" in result

    def test_yolo_false_explicit_keeps_allow_installs_false(self, tmp_path: Path):
        """yolo=False overrides allow_installs=True → needs-package skipped."""
        imp = tmp_path / "improvements.md"
        imp.write_text(
            "- [ ] [functional] [needs-package] install httpx\n"
            "- [ ] [functional] regular item\n"
        )
        # yolo=False wins over allow_installs=True, per the `if yolo is not None` guard
        result = _get_current_improvement(imp, allow_installs=True, yolo=False)
        assert result == "[functional] regular item"


# ---------------------------------------------------------------------------
# evolve_loop / run_single_round — yolo deprecated alias (lines 1072-1073, 1926-1927)
# ---------------------------------------------------------------------------

class TestYoloAliasEntryPoints:
    def test_evolve_loop_yolo_is_applied(self, tmp_path: Path):
        """evolve_loop's `if yolo is not None: allow_installs = yolo` branch fires."""
        (tmp_path / "README.md").write_text("# Test\n")
        (tmp_path / "runs").mkdir()

        # Patch _run_rounds so the loop returns immediately without running a
        # subprocess. Spy on the allow_installs argument it receives.
        captured = {}

        def _fake_run_rounds(*args, **kwargs):
            captured.update(kwargs)
            # Positional: project_dir, run_dir, improvements_path, ui, start_round,
            # max_rounds, check_cmd, allow_installs
            if len(args) >= 8:
                captured["allow_installs_positional"] = args[7]

        with patch("loop._run_rounds", side_effect=_fake_run_rounds), \
             patch("loop._ensure_git"), \
             patch("loop.load_hooks", return_value={}):
            evolve_loop(
                tmp_path,
                max_rounds=1,
                check_cmd="pytest",
                allow_installs=False,
                yolo=True,  # deprecated alias — should win
            )

        # After the yolo branch, allow_installs is True.
        # Check positional 7th arg (index 7) OR kwarg
        assert captured.get("allow_installs_positional") is True

    def test_run_single_round_yolo_is_applied(self, tmp_path: Path):
        """run_single_round's `if yolo is not None: allow_installs = yolo` branch fires."""
        (tmp_path / "README.md").write_text("# Test\n")
        runs = tmp_path / "runs"
        runs.mkdir()
        run_dir = runs / "session"
        run_dir.mkdir()
        improvements = runs / "improvements.md"
        improvements.write_text(
            "- [ ] [functional] [needs-package] needs install\n"
        )

        # Spy on analyze_and_fix — it receives allow_installs.
        captured = {}

        def _fake_analyze(*args, **kwargs):
            captured["allow_installs"] = kwargs.get("allow_installs", args[2] if len(args) > 2 else None)

        with patch("loop.subprocess.run", return_value=MagicMock(returncode=0, stdout="", stderr="")), \
             patch("agent.analyze_and_fix", side_effect=_fake_analyze), \
             patch.dict("sys.modules", {"claude_agent_sdk": MagicMock()}):
            run_single_round(
                tmp_path,
                round_num=1,
                check_cmd=None,
                allow_installs=False,
                run_dir=run_dir,
                yolo=True,  # deprecated alias
            )

        # yolo=True should have promoted allow_installs to True
        assert captured.get("allow_installs") is True


# ---------------------------------------------------------------------------
# evolve_loop resume — corrupted conversation log filenames
# (lines 1114-1116, 1123-1125)
# ---------------------------------------------------------------------------

class TestResumeCorruptedFilenames:
    def test_resume_with_non_numeric_convo_filename_survives(self, tmp_path: Path):
        """Resume sort-key gracefully handles a non-numeric conversation_loop_*.md."""
        (tmp_path / "README.md").write_text("# Test\n")
        runs = tmp_path / "runs"
        session = runs / "20260101_000000"
        session.mkdir(parents=True)
        # Mix: valid numeric + corrupted name. Sort key returns -1 for the bad one.
        (session / "conversation_loop_1.md").write_text("ok")
        (session / "conversation_loop_abc.md").write_text("corrupted")

        called = {}

        def _fake_run_rounds(*args, **kwargs):
            # Expect start_round == 2 (last good numeric round + 1)
            called["start_round"] = args[4] if len(args) > 4 else kwargs.get("start_round")

        with patch("loop._run_rounds", side_effect=_fake_run_rounds), \
             patch("loop._ensure_git"), \
             patch("loop.load_hooks", return_value={}):
            evolve_loop(
                tmp_path,
                max_rounds=10,
                check_cmd="pytest",
                resume=True,
            )

        # last numeric round was 1 → start_round = 2
        assert called.get("start_round") == 2

    def test_resume_when_only_corrupted_convo_log(self, tmp_path: Path):
        """Resume with ONLY a corrupted convo log: start_round falls back to 1."""
        (tmp_path / "README.md").write_text("# Test\n")
        runs = tmp_path / "runs"
        session = runs / "20260101_000000"
        session.mkdir(parents=True)
        # Only a corrupted filename — the `int(last.rsplit("_", 1)[1])` raises.
        (session / "conversation_loop_abc.md").write_text("corrupted")

        called = {}

        def _fake_run_rounds(*args, **kwargs):
            called["start_round"] = args[4] if len(args) > 4 else kwargs.get("start_round")

        with patch("loop._run_rounds", side_effect=_fake_run_rounds), \
             patch("loop._ensure_git"), \
             patch("loop.load_hooks", return_value={}):
            evolve_loop(
                tmp_path,
                max_rounds=10,
                check_cmd="pytest",
                resume=True,
            )

        # ValueError caught → start_round defaults to 1
        assert called.get("start_round") == 1


# ---------------------------------------------------------------------------
# run_dry_run / run_validate — check output with stderr (lines 2078, 2164)
# ---------------------------------------------------------------------------

class TestCheckOutputStderr:
    def test_dry_run_captures_stderr(self, tmp_path: Path):
        """run_dry_run appends stderr to check_output (line 2078)."""
        (tmp_path / "README.md").write_text("# Test\n")

        check_result = MagicMock(
            returncode=1,
            stdout="some stdout",
            stderr="ERROR: something broke",
        )

        captured_check = {}

        def _fake_dry_agent(*args, **kwargs):
            captured_check["check_output"] = kwargs.get("check_output", "")

        with patch("loop.subprocess.run", return_value=check_result), \
             patch("agent.run_dry_run_agent", side_effect=_fake_dry_agent), \
             patch.dict("sys.modules", {"claude_agent_sdk": MagicMock()}):
            run_dry_run(tmp_path, check_cmd="pytest", timeout=10)

        assert "stderr:" in captured_check["check_output"]
        assert "something broke" in captured_check["check_output"]

    def test_validate_captures_stderr(self, tmp_path: Path):
        """run_validate appends stderr to check_output (line 2164)."""
        (tmp_path / "README.md").write_text("# Test\n")

        check_result = MagicMock(
            returncode=1,
            stdout="some stdout",
            stderr="ERROR: validation broke",
        )

        captured = {}

        def _fake_val_agent(*args, **kwargs):
            captured["check_output"] = kwargs.get("check_output", "")

        # run_validate_agent must set exit code — patch it to write pass report
        def _write_report(*args, **kwargs):
            rd = kwargs.get("run_dir")
            if rd is not None:
                (rd / "validate_report.md").write_text("All claims: PASS\n")
            _fake_val_agent(*args, **kwargs)

        with patch("loop.subprocess.run", return_value=check_result), \
             patch("agent.run_validate_agent", side_effect=_write_report), \
             patch.dict("sys.modules", {"claude_agent_sdk": MagicMock()}):
            run_validate(tmp_path, check_cmd="pytest", timeout=10)

        assert "stderr:" in captured["check_output"]
        assert "validation broke" in captured["check_output"]
