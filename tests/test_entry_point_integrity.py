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


def _probe_interpreter_has_sdk(python: str) -> bool:
    """Return True iff ``python -c 'import claude_agent_sdk'`` exits 0.

    Must be a subprocess probe (not an in-process ``import``) — tests
    run under a conftest that installs a ``claude_agent_sdk`` stub in
    ``sys.modules`` when the real SDK is absent, so the in-process
    import would always succeed and wrongly hand back ``sys.executable``
    for the subprocess that actually needs the real package.
    """
    try:
        result = subprocess.run(
            [python, "-c", "import claude_agent_sdk"],
            capture_output=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def _python_with_sdk() -> str:
    """Return a Python interpreter path that has ``claude_agent_sdk``.

    Prefer ``sys.executable``; otherwise fall back to the project's
    ``.venv/bin/python`` if it exists.  Skip the test when neither has
    the SDK — the entry-point check is meaningful only when the real
    dependencies are importable.
    """
    if _probe_interpreter_has_sdk(sys.executable):
        return sys.executable

    # Project root is two levels up from this test file.
    project_root = Path(__file__).resolve().parent.parent
    venv_python = project_root / ".venv" / "bin" / "python"
    if venv_python.is_file() and _probe_interpreter_has_sdk(str(venv_python)):
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
