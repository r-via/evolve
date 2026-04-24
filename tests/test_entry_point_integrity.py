"""Entry-point integrity tests — the reactive layer.

These tests spawn REAL (not mocked) subprocesses to verify that the
``evolve`` CLI entry points are importable and respond to ``--help``.
They catch structural changes (file renames, broken imports, moved
entry points) that the mocked-subprocess unit tests cannot detect.

See SPEC.md § "Detection confidence" — these are the backup for the
agent-side structural-change self-detection heuristic.
"""

import subprocess
import sys
from pathlib import Path

import pytest


def _python_with_sdk() -> str:
    """Return a Python interpreter path that has ``claude_agent_sdk``.

    Prefer the current interpreter (``sys.executable``); otherwise fall
    back to the project's ``.venv/bin/python`` if it exists. Skip the
    test when neither has the SDK — the entry-point check is meaningful
    only when the real dependencies are importable.
    """
    try:
        import claude_agent_sdk  # noqa: F401
        return sys.executable
    except ImportError:
        pass

    # Project root is two levels up from this test file.
    project_root = Path(__file__).resolve().parent.parent
    venv_python = project_root / ".venv" / "bin" / "python"
    if venv_python.is_file():
        probe = subprocess.run(
            [str(venv_python), "-c", "import claude_agent_sdk"],
            capture_output=True,
            timeout=10,
        )
        if probe.returncode == 0:
            return str(venv_python)

    pytest.skip("claude_agent_sdk not importable in any available interpreter")


class TestEntryPointIntegrity:
    """Real subprocess tests for CLI entry points."""

    def test_evolve_help(self):
        """``python -m evolve --help`` exits 0 and prints usage."""
        python = _python_with_sdk()
        result = subprocess.run(
            [python, "-m", "evolve", "--help"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 0, (
            f"evolve --help exited with {result.returncode}\n"
            f"stdout: {result.stdout[:500]}\n"
            f"stderr: {result.stderr[:500]}"
        )
        assert "usage" in result.stdout.lower(), (
            f"'usage' not found in evolve --help output:\n{result.stdout[:500]}"
        )

    def test_evolve_round_help(self):
        """``python -m evolve _round --help`` exits 0 and prints usage."""
        python = _python_with_sdk()
        result = subprocess.run(
            [python, "-m", "evolve", "_round", "--help"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 0, (
            f"evolve _round --help exited with {result.returncode}\n"
            f"stdout: {result.stdout[:500]}\n"
            f"stderr: {result.stderr[:500]}"
        )
        assert "usage" in result.stdout.lower(), (
            f"'usage' not found in evolve _round --help output:\n{result.stdout[:500]}"
        )
