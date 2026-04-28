"""Tests for evolve.state._write_state_json — real-time state.json writer."""

from pathlib import Path
from unittest.mock import patch

from evolve.state import _write_state_json

class TestWriteStateJson:
    """Tests for the real-time state.json writer."""

    def test_basic_state_json(self, tmp_path: Path):
        """Write state.json and verify all required fields."""
        run_dir = tmp_path / "session"
        run_dir.mkdir()
        improvements = tmp_path / "improvements.md"
        improvements.write_text(
            "# Improvements\n"
            "- [x] [functional] done one\n"
            "- [x] [functional] done two\n"
            "- [ ] [functional] pending one\n"
        )
        project_dir = tmp_path / "myproject"
        project_dir.mkdir()

        _write_state_json(
            run_dir=run_dir,
            project_dir=project_dir,
            round_num=3,
            max_rounds=10,
            phase="improvement",
            status="running",
            improvements_path=improvements,
            check_passed=True,
            check_tests=42,
            check_duration_s=1.234,
            started_at="2026-03-25T15:00:00Z",
        )

        import json
        state = json.loads((run_dir / "state.json").read_text())
        assert state["version"] == 2
        assert state["session"] == "session"
        assert state["project"] == "myproject"
        assert state["round"] == 3
        assert state["max_rounds"] == 10
        assert state["phase"] == "improvement"
        assert state["status"] == "running"
        assert state["improvements"] == {"done": 2, "remaining": 1, "blocked": 0}
        assert state["last_check"]["passed"] is True
        assert state["last_check"]["tests"] == 42
        assert state["last_check"]["duration_s"] == 1.2
        assert state["started_at"] == "2026-03-25T15:00:00Z"
        assert "updated_at" in state

    def test_state_json_no_check(self, tmp_path: Path):
        """State.json with no check results has empty last_check."""
        run_dir = tmp_path / "session"
        run_dir.mkdir()
        improvements = tmp_path / "improvements.md"
        improvements.write_text("# Improvements\n")
        project_dir = tmp_path / "proj"
        project_dir.mkdir()

        _write_state_json(
            run_dir=run_dir,
            project_dir=project_dir,
            round_num=1,
            max_rounds=5,
            phase="error",
            status="running",
            improvements_path=improvements,
            started_at="2026-03-25T15:00:00Z",
        )

        import json
        state = json.loads((run_dir / "state.json").read_text())
        assert state["last_check"] == {}
        assert state["improvements"] == {"done": 0, "remaining": 0, "blocked": 0}

    def test_state_json_preserves_started_at(self, tmp_path: Path):
        """When started_at is None, reads from existing state.json."""
        run_dir = tmp_path / "session"
        run_dir.mkdir()
        improvements = tmp_path / "improvements.md"
        improvements.write_text("# Improvements\n- [ ] [functional] todo\n")
        project_dir = tmp_path / "proj"
        project_dir.mkdir()

        import json
        # Write initial state with a known started_at
        (run_dir / "state.json").write_text(json.dumps({
            "started_at": "2026-01-01T00:00:00Z",
        }))

        _write_state_json(
            run_dir=run_dir,
            project_dir=project_dir,
            round_num=2,
            max_rounds=10,
            phase="improvement",
            status="running",
            improvements_path=improvements,
            started_at=None,  # should read from existing
        )

        state = json.loads((run_dir / "state.json").read_text())
        assert state["started_at"] == "2026-01-01T00:00:00Z"

    def test_state_json_blocked_count(self, tmp_path: Path):
        """Blocked items are counted separately in improvements."""
        run_dir = tmp_path / "session"
        run_dir.mkdir()
        improvements = tmp_path / "improvements.md"
        improvements.write_text(
            "# Improvements\n"
            "- [x] [functional] done\n"
            "- [ ] [functional] [needs-package] blocked item\n"
            "- [ ] [functional] regular pending\n"
        )
        project_dir = tmp_path / "proj"
        project_dir.mkdir()

        _write_state_json(
            run_dir=run_dir,
            project_dir=project_dir,
            round_num=1,
            max_rounds=5,
            phase="improvement",
            status="running",
            improvements_path=improvements,
            started_at="2026-03-25T15:00:00Z",
        )

        import json
        state = json.loads((run_dir / "state.json").read_text())
        assert state["improvements"]["done"] == 1
        assert state["improvements"]["remaining"] == 2
        assert state["improvements"]["blocked"] == 1

    def test_state_json_converged_status(self, tmp_path: Path):
        """State.json reflects converged status correctly."""
        run_dir = tmp_path / "session"
        run_dir.mkdir()
        improvements = tmp_path / "improvements.md"
        improvements.write_text(
            "# Improvements\n- [x] [functional] all done\n"
        )
        project_dir = tmp_path / "proj"
        project_dir.mkdir()

        _write_state_json(
            run_dir=run_dir,
            project_dir=project_dir,
            round_num=5,
            max_rounds=20,
            phase="convergence",
            status="converged",
            improvements_path=improvements,
            check_passed=True,
            check_tests=100,
            check_duration_s=2.5,
            started_at="2026-03-25T15:00:00Z",
        )

        import json
        state = json.loads((run_dir / "state.json").read_text())
        assert state["status"] == "converged"
        assert state["phase"] == "convergence"
        assert state["improvements"]["done"] == 1
        assert state["improvements"]["remaining"] == 0

    def test_state_json_missing_improvements_file(self, tmp_path: Path):
        """State.json works even when improvements.md doesn't exist."""
        run_dir = tmp_path / "session"
        run_dir.mkdir()
        project_dir = tmp_path / "proj"
        project_dir.mkdir()
        missing = tmp_path / "nonexistent_improvements.md"

        _write_state_json(
            run_dir=run_dir,
            project_dir=project_dir,
            round_num=1,
            max_rounds=10,
            phase="error",
            status="running",
            improvements_path=missing,
            started_at="2026-03-25T15:00:00Z",
        )

        import json
        state = json.loads((run_dir / "state.json").read_text())
        assert state["improvements"] == {"done": 0, "remaining": 0, "blocked": 0}

    def test_state_json_no_existing_generates_started_at(self, tmp_path: Path):
        """When no existing state.json and no started_at, generates current time."""
        run_dir = tmp_path / "session"
        run_dir.mkdir()
        improvements = tmp_path / "improvements.md"
        improvements.write_text("# Improvements\n")
        project_dir = tmp_path / "proj"
        project_dir.mkdir()

        _write_state_json(
            run_dir=run_dir,
            project_dir=project_dir,
            round_num=1,
            max_rounds=10,
            phase="improvement",
            status="running",
            improvements_path=improvements,
            started_at=None,
        )

        import json
        state = json.loads((run_dir / "state.json").read_text())
        # Should have a valid ISO timestamp
        assert "T" in state["started_at"]
        assert state["started_at"].endswith("Z")

    def test_state_json_corrupted_existing_generates_started_at(self, tmp_path: Path):
        """When existing state.json is corrupted, generates a fresh started_at."""
        run_dir = tmp_path / "session"
        run_dir.mkdir()
        improvements = tmp_path / "improvements.md"
        improvements.write_text("# Improvements\n")
        project_dir = tmp_path / "proj"
        project_dir.mkdir()

        # Write corrupted JSON
        (run_dir / "state.json").write_text("{not valid json!!")

        _write_state_json(
            run_dir=run_dir,
            project_dir=project_dir,
            round_num=2,
            max_rounds=10,
            phase="error",
            status="running",
            improvements_path=improvements,
            started_at=None,
        )

        import json
        state = json.loads((run_dir / "state.json").read_text())
        # Should have generated a new timestamp despite corrupted file
        assert "T" in state["started_at"]
        assert state["started_at"].endswith("Z")
        assert state["round"] == 2

    def test_state_json_overwrites_previous(self, tmp_path: Path):
        """Writing state.json twice overwrites the first with updated values."""
        run_dir = tmp_path / "session"
        run_dir.mkdir()
        improvements = tmp_path / "improvements.md"
        improvements.write_text("# Improvements\n- [ ] [functional] todo\n")
        project_dir = tmp_path / "proj"
        project_dir.mkdir()

        import json

        _write_state_json(
            run_dir=run_dir,
            project_dir=project_dir,
            round_num=1,
            max_rounds=10,
            phase="error",
            status="running",
            improvements_path=improvements,
            started_at="2026-01-01T00:00:00Z",
        )
        state1 = json.loads((run_dir / "state.json").read_text())
        assert state1["round"] == 1

        # Update improvements and write again
        improvements.write_text(
            "# Improvements\n- [x] [functional] done\n- [ ] [functional] next\n"
        )
        _write_state_json(
            run_dir=run_dir,
            project_dir=project_dir,
            round_num=2,
            max_rounds=10,
            phase="improvement",
            status="running",
            improvements_path=improvements,
            started_at="2026-01-01T00:00:00Z",
        )
        state2 = json.loads((run_dir / "state.json").read_text())
        assert state2["round"] == 2
        assert state2["improvements"]["done"] == 1
        assert state2["improvements"]["remaining"] == 1

    def test_state_json_partial_check_results(self, tmp_path: Path):
        """Only provided check fields appear in last_check."""
        run_dir = tmp_path / "session"
        run_dir.mkdir()
        improvements = tmp_path / "improvements.md"
        improvements.write_text("# Improvements\n")
        project_dir = tmp_path / "proj"
        project_dir.mkdir()

        import json

        # Only check_passed, no tests or duration
        _write_state_json(
            run_dir=run_dir,
            project_dir=project_dir,
            round_num=1,
            max_rounds=5,
            phase="error",
            status="running",
            improvements_path=improvements,
            check_passed=False,
            started_at="2026-03-25T15:00:00Z",
        )
        state = json.loads((run_dir / "state.json").read_text())
        assert state["last_check"] == {"passed": False}
        assert "tests" not in state["last_check"]
        assert "duration_s" not in state["last_check"]

    def test_state_json_duration_rounding(self, tmp_path: Path):
        """check_duration_s is rounded to 1 decimal place."""
        run_dir = tmp_path / "session"
        run_dir.mkdir()
        improvements = tmp_path / "improvements.md"
        improvements.write_text("# Improvements\n")
        project_dir = tmp_path / "proj"
        project_dir.mkdir()

        import json

        _write_state_json(
            run_dir=run_dir,
            project_dir=project_dir,
            round_num=1,
            max_rounds=5,
            phase="improvement",
            status="running",
            improvements_path=improvements,
            check_passed=True,
            check_duration_s=3.6789,
            started_at="2026-03-25T15:00:00Z",
        )
        state = json.loads((run_dir / "state.json").read_text())
        assert state["last_check"]["duration_s"] == 3.7

    def test_state_json_all_status_values(self, tmp_path: Path):
        """All documented status values are written correctly."""
        run_dir = tmp_path / "session"
        run_dir.mkdir()
        improvements = tmp_path / "improvements.md"
        improvements.write_text("# Improvements\n")
        project_dir = tmp_path / "proj"
        project_dir.mkdir()

        import json

        for status in ("running", "converged", "max_rounds", "error", "party_mode"):
            _write_state_json(
                run_dir=run_dir,
                project_dir=project_dir,
                round_num=1,
                max_rounds=5,
                phase="improvement",
                status=status,
                improvements_path=improvements,
                started_at="2026-03-25T15:00:00Z",
            )
            state = json.loads((run_dir / "state.json").read_text())
            assert state["status"] == status

    def test_state_json_updated_at_is_valid_iso(self, tmp_path: Path):
        """updated_at is a valid ISO-format UTC timestamp."""
        run_dir = tmp_path / "session"
        run_dir.mkdir()
        improvements = tmp_path / "improvements.md"
        improvements.write_text("# Improvements\n")
        project_dir = tmp_path / "proj"
        project_dir.mkdir()

        import json
        from datetime import datetime, timezone

        _write_state_json(
            run_dir=run_dir,
            project_dir=project_dir,
            round_num=1,
            max_rounds=5,
            phase="improvement",
            status="running",
            improvements_path=improvements,
            started_at="2026-03-25T15:00:00Z",
        )
        state = json.loads((run_dir / "state.json").read_text())
        # Should parse without error
        dt = datetime.strptime(state["updated_at"], "%Y-%m-%dT%H:%M:%SZ")
        assert dt.year >= 2026

    def test_state_json_existing_without_started_at_key(self, tmp_path: Path):
        """Existing state.json missing started_at generates a new timestamp."""
        run_dir = tmp_path / "session"
        run_dir.mkdir()
        improvements = tmp_path / "improvements.md"
        improvements.write_text("# Improvements\n")
        project_dir = tmp_path / "proj"
        project_dir.mkdir()

        import json
        # Existing state without started_at key
        (run_dir / "state.json").write_text(json.dumps({"version": 1}))

        _write_state_json(
            run_dir=run_dir,
            project_dir=project_dir,
            round_num=3,
            max_rounds=10,
            phase="improvement",
            status="running",
            improvements_path=improvements,
            started_at=None,
        )
        state = json.loads((run_dir / "state.json").read_text())
        # Should have generated a fresh timestamp
        assert "T" in state["started_at"]
        assert state["started_at"].endswith("Z")

    def test_state_json_with_usage(self, tmp_path: Path):
        """state.json includes usage dict when provided."""
        run_dir = tmp_path / "session"
        run_dir.mkdir()
        improvements = tmp_path / "improvements.md"
        improvements.write_text("# Improvements\n- [x] done\n")
        project_dir = tmp_path / "proj"
        project_dir.mkdir()

        usage_data = {
            "total_input_tokens": 45000,
            "total_output_tokens": 12000,
            "total_cache_creation_tokens": 8000,
            "total_cache_read_tokens": 38000,
            "estimated_cost_usd": 1.24,
            "rounds_tracked": 1,
        }
        _write_state_json(
            run_dir=run_dir,
            project_dir=project_dir,
            round_num=1,
            max_rounds=10,
            phase="improvement",
            status="running",
            improvements_path=improvements,
            started_at="2026-04-23T18:00:00Z",
            usage=usage_data,
        )
        import json
        state = json.loads((run_dir / "state.json").read_text())
        assert "usage" in state
        assert state["usage"]["total_input_tokens"] == 45000
        assert state["usage"]["estimated_cost_usd"] == 1.24
        assert state["usage"]["rounds_tracked"] == 1

    def test_state_json_without_usage(self, tmp_path: Path):
        """state.json omits usage key when not provided."""
        run_dir = tmp_path / "session"
        run_dir.mkdir()
        improvements = tmp_path / "improvements.md"
        improvements.write_text("# Improvements\n")
        project_dir = tmp_path / "proj"
        project_dir.mkdir()

        _write_state_json(
            run_dir=run_dir,
            project_dir=project_dir,
            round_num=1,
            max_rounds=10,
            phase="improvement",
            status="running",
            improvements_path=improvements,
            started_at="2026-04-23T18:00:00Z",
        )
        import json
        state = json.loads((run_dir / "state.json").read_text())
        assert "usage" not in state


# ---------------------------------------------------------------------------
# _parse_check_output
# ---------------------------------------------------------------------------

