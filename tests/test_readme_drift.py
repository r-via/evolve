"""Tests for Mechanism C — README drift warning.

Covers ``_compute_readme_sync`` in loop.py and its integration with
``_write_state_json``:

- No-op when ``spec`` is ``None`` or equals ``"README.md"``
- Returns ``stale=False`` + zero rounds_since_stale when README is newer
- Tracks ``rounds_since_stale`` across rounds via prior ``state.json``
- Populates ``readme_sync`` into ``state.json`` when provided
- Schema fields: ``stale``, ``spec_mtime``, ``readme_mtime``,
  ``rounds_since_stale``

See SPEC.md § "README sync discipline" § Mechanism C for the contract.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from loop import _compute_readme_sync, _write_state_json


def _touch(path: Path, mtime: float) -> None:
    """Create ``path`` (if missing) and set its mtime to ``mtime``."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text("")
    os.utime(path, (mtime, mtime))


class TestComputeReadmeSyncNoOp:
    """Mechanism C is a no-op when README is the spec or spec is unset."""

    def test_none_spec_returns_none(self, tmp_path: Path) -> None:
        (tmp_path / "README.md").write_text("# readme")
        result = _compute_readme_sync(tmp_path, tmp_path, round_num=5, spec=None)
        assert result is None

    def test_readme_spec_returns_none(self, tmp_path: Path) -> None:
        (tmp_path / "README.md").write_text("# readme")
        result = _compute_readme_sync(tmp_path, tmp_path, round_num=5, spec="README.md")
        assert result is None

    def test_missing_spec_file_returns_none(self, tmp_path: Path) -> None:
        (tmp_path / "README.md").write_text("# readme")
        # SPEC.md does not exist
        result = _compute_readme_sync(tmp_path, tmp_path, round_num=5, spec="SPEC.md")
        assert result is None

    def test_missing_readme_returns_none(self, tmp_path: Path) -> None:
        (tmp_path / "SPEC.md").write_text("# spec")
        # README.md does not exist
        result = _compute_readme_sync(tmp_path, tmp_path, round_num=5, spec="SPEC.md")
        assert result is None


class TestComputeReadmeSyncFresh:
    """When README is at-or-newer than the spec, drift is not stale."""

    def test_readme_newer_than_spec_not_stale(self, tmp_path: Path) -> None:
        now = time.time()
        _touch(tmp_path / "SPEC.md", now - 1000)
        _touch(tmp_path / "README.md", now)

        result = _compute_readme_sync(tmp_path, tmp_path, round_num=1, spec="SPEC.md")
        assert result is not None
        assert result["stale"] is False
        assert result["rounds_since_stale"] == 0
        assert "spec_mtime" in result
        assert "readme_mtime" in result

    def test_fresh_readme_clears_prior_stale_counter(self, tmp_path: Path) -> None:
        """If the README is touched (no longer stale), the counter resets."""
        # Seed a prior state.json claiming drift started at round 2
        prior_state = {
            "version": 1,
            "readme_sync": {
                "stale": True,
                "spec_mtime": "2026-04-20T00:00:00Z",
                "readme_mtime": "2026-04-19T00:00:00Z",
                "rounds_since_stale": 3,
                "readme_stale_since_round": 2,
            },
        }
        (tmp_path / "state.json").write_text(json.dumps(prior_state))

        # README is now fresher than SPEC.md
        now = time.time()
        _touch(tmp_path / "SPEC.md", now - 500)
        _touch(tmp_path / "README.md", now)

        result = _compute_readme_sync(tmp_path, tmp_path, round_num=6, spec="SPEC.md")
        assert result is not None
        assert result["stale"] is False
        assert result["rounds_since_stale"] == 0
        # No persisted stale-since counter when not stale
        assert "readme_stale_since_round" not in result


class TestComputeReadmeSyncStale:
    """When SPEC.md is newer than README.md, drift accrues across rounds."""

    def test_first_stale_round_is_zero_rounds_since(self, tmp_path: Path) -> None:
        now = time.time()
        _touch(tmp_path / "README.md", now - 1000)
        _touch(tmp_path / "SPEC.md", now)

        result = _compute_readme_sync(tmp_path, tmp_path, round_num=3, spec="SPEC.md")
        assert result is not None
        assert result["stale"] is True
        assert result["rounds_since_stale"] == 0
        assert result["readme_stale_since_round"] == 3

    def test_rounds_since_stale_increments_when_persisted(self, tmp_path: Path) -> None:
        """After several rounds without README update, rounds_since_stale grows."""
        now = time.time()
        _touch(tmp_path / "README.md", now - 1000)
        _touch(tmp_path / "SPEC.md", now)

        # Seed prior state: drift started at round 2
        prior_state = {
            "version": 1,
            "readme_sync": {
                "stale": True,
                "spec_mtime": "2026-04-20T00:00:00Z",
                "readme_mtime": "2026-04-19T00:00:00Z",
                "rounds_since_stale": 1,
                "readme_stale_since_round": 2,
            },
        }
        (tmp_path / "state.json").write_text(json.dumps(prior_state))

        result = _compute_readme_sync(tmp_path, tmp_path, round_num=5, spec="SPEC.md")
        assert result is not None
        assert result["stale"] is True
        assert result["readme_stale_since_round"] == 2
        # 5 - 2 == 3 rounds elapsed
        assert result["rounds_since_stale"] == 3

    def test_threshold_boundary(self, tmp_path: Path) -> None:
        """rounds_since_stale > 3 is the documented warning threshold."""
        now = time.time()
        _touch(tmp_path / "README.md", now - 1000)
        _touch(tmp_path / "SPEC.md", now)

        prior_state = {
            "version": 1,
            "readme_sync": {
                "stale": True,
                "rounds_since_stale": 3,
                "readme_stale_since_round": 1,
            },
        }
        (tmp_path / "state.json").write_text(json.dumps(prior_state))

        # Round 5 means 5 - 1 == 4 rounds elapsed (> 3 → warning fires)
        result = _compute_readme_sync(tmp_path, tmp_path, round_num=5, spec="SPEC.md")
        assert result is not None
        assert result["rounds_since_stale"] == 4

    def test_corrupt_prior_state_json(self, tmp_path: Path) -> None:
        """Invalid JSON in state.json is tolerated — counter restarts."""
        now = time.time()
        _touch(tmp_path / "README.md", now - 1000)
        _touch(tmp_path / "SPEC.md", now)

        (tmp_path / "state.json").write_text("{not valid json")

        result = _compute_readme_sync(tmp_path, tmp_path, round_num=4, spec="SPEC.md")
        assert result is not None
        assert result["stale"] is True
        # Counter starts at current round when prior state is unreadable
        assert result["readme_stale_since_round"] == 4
        assert result["rounds_since_stale"] == 0


class TestWriteStateJsonReadmeSync:
    """``_write_state_json`` embeds readme_sync when provided."""

    def test_state_json_includes_readme_sync(self, tmp_path: Path) -> None:
        improvements = tmp_path / "improvements.md"
        improvements.write_text("- [x] done\n- [ ] pending\n")
        readme_sync = {
            "stale": True,
            "spec_mtime": "2026-04-23T13:40:00Z",
            "readme_mtime": "2026-04-23T12:10:00Z",
            "rounds_since_stale": 5,
        }

        _write_state_json(
            run_dir=tmp_path,
            project_dir=tmp_path,
            round_num=5,
            max_rounds=10,
            phase="improvement",
            status="running",
            improvements_path=improvements,
            readme_sync=readme_sync,
        )

        state = json.loads((tmp_path / "state.json").read_text())
        assert state["readme_sync"] == readme_sync

    def test_state_json_omits_readme_sync_when_none(self, tmp_path: Path) -> None:
        """When Mechanism C is inactive (spec is README.md), no field is written."""
        improvements = tmp_path / "improvements.md"
        improvements.write_text("- [ ] item\n")

        _write_state_json(
            run_dir=tmp_path,
            project_dir=tmp_path,
            round_num=1,
            max_rounds=10,
            phase="improvement",
            status="running",
            improvements_path=improvements,
            readme_sync=None,
        )

        state = json.loads((tmp_path / "state.json").read_text())
        assert "readme_sync" not in state
