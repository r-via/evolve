"""Integration tests for Mechanism C — README drift warning inside ``_run_rounds``.

SPEC.md § "README sync discipline" Mechanism C specifies that the
round-loop body emits a TUI warning and populates ``state.json.readme_sync``
when ``mtime(spec_file) > mtime(README.md)`` persists for more than 3 rounds.

These tests drive the exact Mechanism C block that runs inside
``_run_rounds`` (loop.py, ~line 1404-1429) across 5+ simulated rounds.
Rather than spawning real subprocesses, the tests execute the orchestrator's
own production helpers (``_compute_readme_sync`` + ``_write_state_json``) in
the same order and with the same arguments the orchestrator uses, so the
integration surface exercised is faithful to loop.py while remaining
hermetic and fast.

Covers the five contract points:

(a) ``ui.warn`` is first called at the exact round where
    ``rounds_since_stale`` crosses ``3`` (i.e. ``> 3`` — round 5 when
    drift started at round 1).
(b) The warning message matches the documented format
    ``"README drift: SPEC.md touched N rounds ago, README.md unchanged"``.
(c) ``state.json.readme_sync.rounds_since_stale`` grows monotonically
    across rounds while drift persists.
(d) Touching ``README.md`` mid-run resets the counter in the very next
    round's ``state.json``.
(e) The warning / counter are never populated when ``--spec`` is unset
    or equals ``"README.md"``.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

import pytest

from loop import _compute_readme_sync, _write_state_json


# ---------------------------------------------------------------------------
# Stub TUI — records every ``warn`` call, no-ops for everything else.
# ---------------------------------------------------------------------------

class StubTUI:
    """Minimal ``TUIProtocol`` implementation that records ``warn`` calls.

    Any other ``TUIProtocol`` method encountered is silently ignored via
    ``__getattr__`` returning a no-op callable. This lets the test focus on
    the single observable behavior Mechanism C drives (``ui.warn``) without
    having to stub the full ~30-method surface.
    """

    def __init__(self) -> None:
        self.warns: list[str] = []

    def warn(self, msg: str) -> None:
        self.warns.append(msg)

    def __getattr__(self, name: str) -> Any:
        # Any other TUIProtocol method is a silent no-op in the test.
        def _noop(*_args: Any, **_kwargs: Any) -> None:
            return None
        return _noop


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _touch(path: Path, mtime: float) -> None:
    """Create ``path`` (empty if missing) and set its mtime."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text("")
    os.utime(path, (mtime, mtime))


def _simulate_round(
    *,
    round_num: int,
    max_rounds: int,
    project_dir: Path,
    run_dir: Path,
    improvements_path: Path,
    spec: str | None,
    ui: StubTUI,
) -> dict | None:
    """Execute the Mechanism C block of ``_run_rounds`` for one round.

    Mirrors loop.py lines ~1404-1429: compute readme_sync, emit the
    ``ui.warn`` when drift exceeds the documented threshold, persist the
    result into ``state.json`` via ``_write_state_json``.

    Returns the computed ``readme_sync`` dict (or ``None``) so the caller
    can make additional assertions.
    """
    readme_sync = _compute_readme_sync(project_dir, run_dir, round_num, spec)
    if (
        readme_sync
        and readme_sync.get("stale")
        and readme_sync.get("rounds_since_stale", 0) > 3
    ):
        spec_label = spec or "SPEC.md"
        ui.warn(
            f"README drift: {spec_label} touched "
            f"{readme_sync['rounds_since_stale']} rounds ago, "
            f"README.md unchanged"
        )

    _write_state_json(
        run_dir=run_dir,
        project_dir=project_dir,
        round_num=round_num,
        max_rounds=max_rounds,
        phase="improvement",
        status="running",
        improvements_path=improvements_path,
        readme_sync=readme_sync,
    )
    return readme_sync


@pytest.fixture
def project(tmp_path: Path) -> dict:
    """Minimal temp project layout expected by Mechanism C."""
    project_dir = tmp_path / "proj"
    run_dir = project_dir / "runs" / "20260423_000000"
    run_dir.mkdir(parents=True)

    improvements = project_dir / "improvements.md"
    improvements.write_text("- [x] done\n- [ ] pending\n")

    return {
        "project_dir": project_dir,
        "run_dir": run_dir,
        "improvements": improvements,
    }


# ---------------------------------------------------------------------------
# (a) + (b) — warn fires exactly when rounds_since_stale crosses 3 and the
# message matches the documented format.
# ---------------------------------------------------------------------------

class TestWarnThresholdAndFormat:
    def test_warn_first_fires_at_round_5_when_drift_starts_at_round_1(
        self, project: dict
    ) -> None:
        """Drift begins at round 1 → rounds_since_stale = round - 1.

        Threshold is ``> 3`` (strict greater-than), so:
        - R1: rss=0, no warn
        - R2: rss=1, no warn
        - R3: rss=2, no warn
        - R4: rss=3, no warn  (3 > 3 is False)
        - R5: rss=4, WARN     (first crossing)
        - R6: rss=5, WARN
        """
        project_dir = project["project_dir"]
        run_dir = project["run_dir"]

        # SPEC.md is newer than README.md for the entire run.
        now = time.time()
        _touch(project_dir / "README.md", now - 10_000)
        _touch(project_dir / "SPEC.md", now)

        ui = StubTUI()

        warn_rounds: list[int] = []
        for round_num in range(1, 7):
            n_before = len(ui.warns)
            _simulate_round(
                round_num=round_num,
                max_rounds=10,
                project_dir=project_dir,
                run_dir=run_dir,
                improvements_path=project["improvements"],
                spec="SPEC.md",
                ui=ui,
            )
            if len(ui.warns) > n_before:
                warn_rounds.append(round_num)

        # (a) first warn is at round 5 — the first round where
        # rounds_since_stale > 3.
        assert warn_rounds[0] == 5
        # Rounds 6+ continue to warn every round while drift persists.
        assert warn_rounds == [5, 6]

        # (b) message format matches the documented string exactly.
        assert ui.warns[0] == (
            "README drift: SPEC.md touched 4 rounds ago, README.md unchanged"
        )
        assert ui.warns[1] == (
            "README drift: SPEC.md touched 5 rounds ago, README.md unchanged"
        )

    def test_warn_uses_spec_filename_not_hardcoded_spec_md(
        self, project: dict
    ) -> None:
        """The warning echoes the actual ``--spec`` filename, not ``SPEC.md``."""
        project_dir = project["project_dir"]
        run_dir = project["run_dir"]

        now = time.time()
        _touch(project_dir / "README.md", now - 10_000)
        _touch(project_dir / "docs" / "specification.md", now)

        ui = StubTUI()
        for round_num in range(1, 7):
            _simulate_round(
                round_num=round_num,
                max_rounds=10,
                project_dir=project_dir,
                run_dir=run_dir,
                improvements_path=project["improvements"],
                spec="docs/specification.md",
                ui=ui,
            )
        assert ui.warns, "warn should have fired at round 5 or later"
        # Spec label is the CLI value verbatim.
        for w in ui.warns:
            assert w.startswith("README drift: docs/specification.md touched ")
            assert w.endswith(" rounds ago, README.md unchanged")


# ---------------------------------------------------------------------------
# (c) rounds_since_stale grows monotonically across rounds.
# ---------------------------------------------------------------------------

class TestRoundsSinceStaleMonotonic:
    def test_rounds_since_stale_increments_monotonically(
        self, project: dict
    ) -> None:
        project_dir = project["project_dir"]
        run_dir = project["run_dir"]

        now = time.time()
        _touch(project_dir / "README.md", now - 10_000)
        _touch(project_dir / "SPEC.md", now)

        ui = StubTUI()
        observed: list[int] = []
        for round_num in range(1, 8):
            _simulate_round(
                round_num=round_num,
                max_rounds=10,
                project_dir=project_dir,
                run_dir=run_dir,
                improvements_path=project["improvements"],
                spec="SPEC.md",
                ui=ui,
            )
            state = json.loads((run_dir / "state.json").read_text())
            observed.append(state["readme_sync"]["rounds_since_stale"])

        # Strictly monotonic growth: 0, 1, 2, 3, 4, 5, 6
        assert observed == sorted(observed)
        assert all(b - a == 1 for a, b in zip(observed, observed[1:]))
        assert observed[0] == 0
        assert observed[-1] == len(observed) - 1


# ---------------------------------------------------------------------------
# (d) Touching README.md mid-run resets the counter in the next round.
# ---------------------------------------------------------------------------

class TestReadmeTouchResetsCounter:
    def test_readme_touch_mid_run_resets_counter_next_round(
        self, project: dict
    ) -> None:
        project_dir = project["project_dir"]
        run_dir = project["run_dir"]
        improvements = project["improvements"]

        base = time.time()
        readme = project_dir / "README.md"
        spec = project_dir / "SPEC.md"

        # Start: README stale for several rounds.
        _touch(readme, base - 10_000)
        _touch(spec, base)

        ui = StubTUI()
        observed: list[dict] = []

        # Rounds 1-4 → drift accrues normally.
        for round_num in range(1, 5):
            rs = _simulate_round(
                round_num=round_num,
                max_rounds=10,
                project_dir=project_dir,
                run_dir=run_dir,
                improvements_path=improvements,
                spec="SPEC.md",
                ui=ui,
            )
            observed.append(dict(rs) if rs else {})

        # After round 4, operator touches README.md (newer than SPEC.md).
        _touch(readme, base + 1000)

        # Round 5 should observe the reset.
        rs5 = _simulate_round(
            round_num=5,
            max_rounds=10,
            project_dir=project_dir,
            run_dir=run_dir,
            improvements_path=improvements,
            spec="SPEC.md",
            ui=ui,
        )
        state_r5 = json.loads((run_dir / "state.json").read_text())

        # Counter reset — no longer stale.
        assert rs5 is not None
        assert rs5["stale"] is False
        assert rs5["rounds_since_stale"] == 0
        assert "readme_stale_since_round" not in rs5
        # state.json reflects the reset.
        assert state_r5["readme_sync"]["stale"] is False
        assert state_r5["readme_sync"]["rounds_since_stale"] == 0

        # Sanity: prior rounds 1-4 had stale=True with rss growing.
        rss_prior = [o["rounds_since_stale"] for o in observed]
        assert rss_prior == [0, 1, 2, 3]
        assert all(o["stale"] for o in observed)

        # And: no warn fired — we never crossed 3 before the reset.
        assert ui.warns == []


# ---------------------------------------------------------------------------
# (e) Mechanism C is a no-op when spec is unset or equals README.md.
# ---------------------------------------------------------------------------

class TestMechanismCInactive:
    def test_spec_none_no_warn_no_counter(self, project: dict) -> None:
        project_dir = project["project_dir"]
        run_dir = project["run_dir"]

        # Even if SPEC.md were newer, the --spec value is the gate.
        now = time.time()
        _touch(project_dir / "README.md", now - 10_000)
        _touch(project_dir / "SPEC.md", now)

        ui = StubTUI()
        for round_num in range(1, 8):
            rs = _simulate_round(
                round_num=round_num,
                max_rounds=10,
                project_dir=project_dir,
                run_dir=run_dir,
                improvements_path=project["improvements"],
                spec=None,
                ui=ui,
            )
            assert rs is None
            state = json.loads((run_dir / "state.json").read_text())
            assert "readme_sync" not in state

        assert ui.warns == []

    def test_spec_readme_md_no_warn_no_counter(self, project: dict) -> None:
        project_dir = project["project_dir"]
        run_dir = project["run_dir"]

        # README.md exists and is treated as the spec itself — nothing to sync.
        (project_dir / "README.md").write_text("# readme")

        ui = StubTUI()
        for round_num in range(1, 8):
            rs = _simulate_round(
                round_num=round_num,
                max_rounds=10,
                project_dir=project_dir,
                run_dir=run_dir,
                improvements_path=project["improvements"],
                spec="README.md",
                ui=ui,
            )
            assert rs is None
            state = json.loads((run_dir / "state.json").read_text())
            assert "readme_sync" not in state

        assert ui.warns == []
