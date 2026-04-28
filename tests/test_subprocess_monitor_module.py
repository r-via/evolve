"""Regression tests for the US-043 ``evolve/subprocess_monitor.py`` extract.

Validates the four invariants that make this a clean leaf-module split:

1. ``_run_monitored_subprocess`` is importable from ``evolve.subprocess_monitor``.
2. ``is``-equality holds between ``evolve.orchestrator._run_monitored_subprocess``
   and ``evolve.subprocess_monitor._run_monitored_subprocess`` — the re-export
   in ``orchestrator.py`` preserves the patch surface
   (``patch("evolve.orchestrator._run_monitored_subprocess")`` continues to
   intercept the binding ``_run_rounds`` actually calls).
3. ``WATCHDOG_TIMEOUT`` constant moves with the function and is also re-exported.
4. ``evolve/subprocess_monitor.py`` imports ONLY from stdlib + ``evolve.tui`` at
   module top — ``grep -E "^from evolve\\.(agent|orchestrator|cli)( |$|\\.)"``
   returns zero matches.  Same leaf-module invariant established by
   US-027 / US-030 / US-031 / US-032 / US-033 / US-034 / US-035 / US-036
   / US-037 / US-038 / US-040 / US-041 / US-042.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest


_LEAF_INVARIANT = re.compile(
    r"^from evolve\.(agent|orchestrator|cli)( |$|\.)",
    re.MULTILINE,
)


def test_run_monitored_subprocess_importable_from_subprocess_monitor():
    from evolve.subprocess_monitor import _run_monitored_subprocess

    assert callable(_run_monitored_subprocess)


def test_watchdog_timeout_importable_from_subprocess_monitor():
    from evolve.subprocess_monitor import WATCHDOG_TIMEOUT

    assert isinstance(WATCHDOG_TIMEOUT, int)
    assert WATCHDOG_TIMEOUT == 120


@pytest.mark.parametrize(
    "name",
    ["_run_monitored_subprocess", "WATCHDOG_TIMEOUT"],
)
def test_orchestrator_reexports_same_object(name):
    """Re-export at orchestrator.py top must preserve ``is``-identity.

    Without this, ``patch("evolve.orchestrator._run_monitored_subprocess")``
    would patch the orchestrator-namespace alias while ``_run_rounds`` (and
    every existing test that patches via ``evolve.orchestrator``) would call
    the real subprocess — silently bypassing the mock.
    """
    import evolve.orchestrator as _orch
    import evolve.subprocess_monitor as _sm

    assert getattr(_orch, name) is getattr(_sm, name), (
        f"{name} re-export broken: evolve.orchestrator and "
        "evolve.subprocess_monitor must bind the same object"
    )


def test_subprocess_monitor_is_leaf_module():
    """No ``from evolve.{agent,orchestrator,cli}`` top-level imports."""
    src_path = Path(__file__).resolve().parent.parent / "evolve" / "subprocess_monitor.py"
    src = src_path.read_text(encoding="utf-8")

    matches = _LEAF_INVARIANT.findall(src)
    assert matches == [], (
        f"evolve/subprocess_monitor.py violates leaf-module invariant — "
        f"top-level imports from forbidden siblings: {matches}"
    )


def test_subprocess_monitor_under_500_lines():
    """SPEC § 'Hard rule: source files MUST NOT exceed 500 lines'."""
    src_path = Path(__file__).resolve().parent.parent / "evolve" / "subprocess_monitor.py"
    line_count = len(src_path.read_text(encoding="utf-8").splitlines())
    assert line_count < 500, (
        f"evolve/subprocess_monitor.py at {line_count} lines exceeds 500-line cap"
    )
