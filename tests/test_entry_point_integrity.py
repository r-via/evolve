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

import pytest


class TestEntryPointIntegrity:
    """Real subprocess tests for CLI entry points."""

    def test_evolve_help(self):
        """``python -m evolve --help`` exits 0 and prints usage."""
        result = subprocess.run(
            [sys.executable, "-m", "evolve", "--help"],
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
        result = subprocess.run(
            [sys.executable, "-m", "evolve", "_round", "--help"],
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
