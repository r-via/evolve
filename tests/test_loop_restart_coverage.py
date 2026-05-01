"""Coverage tests for restart-required handling and backlog state.json schema.

Extracted from test_loop_coverage.py to keep modules under the 500-line cap.
Covers:
- TestBacklogStateJsonSchema — backlog block in state.json
- TestParseRestartRequired — RESTART_REQUIRED marker parsing
- TestRunRoundsRestartRequired — _run_rounds exit-code-3 handling
"""

import subprocess
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from evolve.application.run_loop import _run_rounds
from evolve.infrastructure.filesystem.state_manager import _parse_restart_required


class TestBacklogStateJsonSchema:
    """Tests for the ``backlog`` block exposed in state.json.

    Verifies the field shape documented in SPEC.md § "Growth monitoring":
    ``backlog: { pending, done, blocked, added_this_round,
    growth_rate_last_5_rounds }``.
    """

    @staticmethod
    def _git(project_dir: Path, *args: str) -> None:
        subprocess.run(["git", *args], cwd=project_dir, check=True,
                       capture_output=True)

    def _init_repo(self, project_dir: Path) -> None:
        project_dir.mkdir(parents=True, exist_ok=True)
        self._git(project_dir, "init", "-q", "-b", "main")
        self._git(project_dir, "config", "user.email", "test@example.com")
        self._git(project_dir, "config", "user.name", "Test")
        self._git(project_dir, "config", "commit.gpgsign", "false")

    def test_schema_field_names_and_types(self, tmp_path: Path):
        """state.json.backlog has the exact 5 keys and types documented in SPEC."""
        from evolve.application.run_loop import _write_state_json
        import json as _json

        project_dir = tmp_path / "proj"
        self._init_repo(project_dir)
        run_dir = project_dir / "runs" / "session"
        run_dir.mkdir(parents=True)
        improvements = project_dir / "runs" / "improvements.md"
        improvements.write_text(
            "# Improvements\n"
            "- [x] [functional] done one\n"
            "- [ ] [functional] pending one\n"
            "- [ ] [performance] [needs-package] pending two needing pkg\n"
        )

        _write_state_json(
            run_dir=run_dir,
            project_dir=project_dir,
            round_num=1,
            max_rounds=10,
            phase="improvement",
            status="running",
            improvements_path=improvements,
            started_at="2026-04-23T15:00:00Z",
        )

        state = _json.loads((run_dir / "state.json").read_text())
        backlog = state["backlog"]
        assert set(backlog.keys()) == {
            "pending",
            "done",
            "blocked",
            "added_this_round",
            "growth_rate_last_5_rounds",
        }
        assert isinstance(backlog["pending"], int)
        assert isinstance(backlog["done"], int)
        assert isinstance(backlog["blocked"], int)
        assert isinstance(backlog["added_this_round"], int)
        assert isinstance(backlog["growth_rate_last_5_rounds"], (int, float))
        assert backlog["pending"] == 2
        assert backlog["done"] == 1
        assert backlog["blocked"] == 1

    def test_added_this_round_and_growth_from_git_history(self, tmp_path: Path):
        """added_this_round = new ``- [ ]`` lines vs HEAD; growth = delta vs HEAD~5."""
        from evolve.application.run_loop import _write_state_json
        import json as _json

        project_dir = tmp_path / "proj"
        self._init_repo(project_dir)
        run_dir = project_dir / "runs" / "session"
        run_dir.mkdir(parents=True)
        rel_imp = project_dir / "runs" / "improvements.md"

        rel_imp.write_text("# Improvements\n- [ ] [functional] item 0\n")
        self._git(project_dir, "add", "-A")
        self._git(project_dir, "commit", "-q", "-m", "round 0")
        for i in range(1, 6):
            text = "# Improvements\n" + "".join(
                f"- [ ] [functional] item {j}\n" for j in range(i + 1)
            )
            rel_imp.write_text(text)
            self._git(project_dir, "add", "-A")
            self._git(project_dir, "commit", "-q", "-m", f"round {i}")

        text = rel_imp.read_text() + "- [ ] [functional] freshly added\n"
        rel_imp.write_text(text)

        _write_state_json(
            run_dir=run_dir,
            project_dir=project_dir,
            round_num=6,
            max_rounds=10,
            phase="improvement",
            status="running",
            improvements_path=rel_imp,
            started_at="2026-04-23T15:00:00Z",
        )

        state = _json.loads((run_dir / "state.json").read_text())
        backlog = state["backlog"]
        assert backlog["pending"] == 7
        assert backlog["added_this_round"] == 1
        assert backlog["growth_rate_last_5_rounds"] == 1.2

    def test_growth_zero_without_git_history(self, tmp_path: Path):
        """No git repo → added_this_round=0, growth_rate=0.0 (graceful degrade)."""
        from evolve.application.run_loop import _write_state_json
        import json as _json

        project_dir = tmp_path / "proj"
        project_dir.mkdir()
        run_dir = project_dir / "runs" / "session"
        run_dir.mkdir(parents=True)
        improvements = project_dir / "runs" / "improvements.md"
        improvements.write_text(
            "# Improvements\n- [ ] [functional] only one\n"
        )

        _write_state_json(
            run_dir=run_dir,
            project_dir=project_dir,
            round_num=1,
            max_rounds=10,
            phase="improvement",
            status="running",
            improvements_path=improvements,
            started_at="2026-04-23T15:00:00Z",
        )

        state = _json.loads((run_dir / "state.json").read_text())
        backlog = state["backlog"]
        assert backlog["pending"] == 1
        assert backlog["added_this_round"] == 0
        assert backlog["growth_rate_last_5_rounds"] == 0.0

    def test_added_this_round_uses_line_set_diff_not_count_diff(self, tmp_path: Path):
        """Checking off A and adding B → added_this_round=1, NOT 0 (count is unchanged)."""
        from evolve.application.run_loop import _write_state_json
        import json as _json

        project_dir = tmp_path / "proj"
        self._init_repo(project_dir)
        run_dir = project_dir / "runs" / "session"
        run_dir.mkdir(parents=True)
        improvements = project_dir / "runs" / "improvements.md"
        improvements.write_text(
            "# Improvements\n- [ ] [functional] item A\n"
        )
        self._git(project_dir, "add", "-A")
        self._git(project_dir, "commit", "-q", "-m", "init")

        improvements.write_text(
            "# Improvements\n"
            "- [x] [functional] item A\n"
            "- [ ] [functional] item B\n"
        )

        _write_state_json(
            run_dir=run_dir,
            project_dir=project_dir,
            round_num=1,
            max_rounds=10,
            phase="improvement",
            status="running",
            improvements_path=improvements,
            started_at="2026-04-23T15:00:00Z",
        )

        state = _json.loads((run_dir / "state.json").read_text())
        backlog = state["backlog"]
        assert backlog["pending"] == 1
        assert backlog["done"] == 1
        assert backlog["added_this_round"] == 1


# ---------------------------------------------------------------------------
# _parse_restart_required
# ---------------------------------------------------------------------------

class TestParseRestartRequired:
    """Test _parse_restart_required marker file parsing."""

    def test_returns_none_when_no_marker(self, tmp_path: Path):
        assert _parse_restart_required(tmp_path) is None

    def test_parses_valid_marker(self, tmp_path: Path):
        (tmp_path / "RESTART_REQUIRED").write_text(
            "# RESTART_REQUIRED\n"
            "reason: extracted git.py from loop.py\n"
            "verify: python -m evolve --help\n"
            "resume: python -m evolve start . --resume\n"
            "round: 5\n"
            "timestamp: 2026-04-23T21:00:00Z\n"
        )
        marker = _parse_restart_required(tmp_path)
        assert marker is not None
        assert marker["reason"] == "extracted git.py from loop.py"
        assert marker["verify"] == "python -m evolve --help"
        assert marker["resume"] == "python -m evolve start . --resume"
        assert marker["round"] == "5"
        assert marker["timestamp"] == "2026-04-23T21:00:00Z"

    def test_returns_none_when_reason_missing(self, tmp_path: Path):
        (tmp_path / "RESTART_REQUIRED").write_text(
            "# RESTART_REQUIRED\n"
            "verify: python -m evolve --help\n"
        )
        assert _parse_restart_required(tmp_path) is None

    def test_ignores_comments_and_blanks(self, tmp_path: Path):
        (tmp_path / "RESTART_REQUIRED").write_text(
            "# RESTART_REQUIRED\n"
            "# This is a comment\n"
            "\n"
            "reason: test reason\n"
        )
        marker = _parse_restart_required(tmp_path)
        assert marker is not None
        assert marker["reason"] == "test reason"

    def test_handles_colons_in_value(self, tmp_path: Path):
        (tmp_path / "RESTART_REQUIRED").write_text(
            "reason: value with: colons: in it\n"
        )
        marker = _parse_restart_required(tmp_path)
        assert marker is not None
        assert marker["reason"] == "value with: colons: in it"


# ---------------------------------------------------------------------------
# _run_rounds — RESTART_REQUIRED handling (exit code 3)
# ---------------------------------------------------------------------------

class TestRunRoundsRestartRequired:
    """Test _run_rounds exits 3 when RESTART_REQUIRED is written."""

    def setup_method(self):
        self.ui = MagicMock()

    def _setup_project(self, tmp_path):
        project_dir = tmp_path / "proj"
        project_dir.mkdir()
        run_dir = project_dir / "runs" / "session"
        run_dir.mkdir(parents=True)
        imp_path = project_dir / "runs" / "improvements.md"
        imp_path.write_text("- [ ] [functional] do something\n")
        return project_dir, run_dir, imp_path

    def test_restart_required_exits_3(self, tmp_path: Path):
        """When RESTART_REQUIRED is written by the agent, exit code is 3."""
        project_dir, run_dir, imp_path = self._setup_project(tmp_path)
        ui = self.ui

        def mock_monitored(cmd, cwd, ui_, round_num, watchdog_timeout=120):
            convo = run_dir / f"conversation_loop_{round_num}.md"
            convo.write_text("# Round conversation")
            (run_dir / "COMMIT_MSG").write_text("STRUCTURAL: feat(git): extract git.py")
            (run_dir / "RESTART_REQUIRED").write_text(
                "# RESTART_REQUIRED\n"
                "reason: extracted git.py from loop.py\n"
                "verify: python -m evolve --help\n"
                "resume: python -m evolve start . --resume\n"
                "round: 1\n"
                "timestamp: 2026-04-23T21:00:00Z\n"
            )
            imp_path.write_text("- [x] [functional] do something\n")
            return 0, "output", False

        with patch("evolve.application.run_loop._run_monitored_subprocess", side_effect=mock_monitored), \
             patch("evolve.application.run_loop._is_self_evolving", return_value=True), \
             patch("evolve.infrastructure.reporting.generator._generate_evolution_report"), \
             pytest.raises(SystemExit) as exc:
            _run_rounds(
                project_dir, run_dir, imp_path, ui,
                start_round=1, max_rounds=10, check_cmd="pytest",
                allow_installs=False, timeout=300, model="claude-opus-4-6",
            )
        assert exc.value.code == 3

    def test_restart_required_renders_panel(self, tmp_path: Path):
        """structural_change_required is called on the UI."""
        project_dir, run_dir, imp_path = self._setup_project(tmp_path)
        ui = self.ui

        def mock_monitored(cmd, cwd, ui_, round_num, watchdog_timeout=120):
            convo = run_dir / f"conversation_loop_{round_num}.md"
            convo.write_text("# Round conversation")
            (run_dir / "COMMIT_MSG").write_text("STRUCTURAL: test")
            (run_dir / "RESTART_REQUIRED").write_text(
                "reason: test reason\n"
                "verify: pytest\n"
                "resume: evolve start . --resume\n"
                "round: 1\n"
                "timestamp: 2026-04-23T21:00:00Z\n"
            )
            imp_path.write_text("- [x] [functional] do something\n")
            return 0, "output", False

        with patch("evolve.application.run_loop._run_monitored_subprocess", side_effect=mock_monitored), \
             patch("evolve.application.run_loop._is_self_evolving", return_value=True), \
             patch("evolve.infrastructure.reporting.generator._generate_evolution_report"), \
             pytest.raises(SystemExit):
            _run_rounds(
                project_dir, run_dir, imp_path, ui,
                start_round=1, max_rounds=10, check_cmd="pytest",
                allow_installs=False, timeout=300, model="claude-opus-4-6",
            )
        ui.structural_change_required.assert_called_once()
        marker_arg = ui.structural_change_required.call_args[0][0]
        assert marker_arg["reason"] == "test reason"

    def test_restart_required_fires_hook(self, tmp_path: Path):
        """on_structural_change hook is fired with marker env vars."""
        project_dir, run_dir, imp_path = self._setup_project(tmp_path)
        ui = self.ui

        def mock_monitored(cmd, cwd, ui_, round_num, watchdog_timeout=120):
            convo = run_dir / f"conversation_loop_{round_num}.md"
            convo.write_text("# Round conversation")
            (run_dir / "COMMIT_MSG").write_text("STRUCTURAL: test")
            (run_dir / "RESTART_REQUIRED").write_text(
                "reason: moved hooks.py\n"
                "verify: python -m evolve --help\n"
                "resume: evolve start . --resume\n"
                "round: 2\n"
                "timestamp: 2026-04-23T22:00:00Z\n"
            )
            imp_path.write_text("- [x] [functional] do something\n")
            return 0, "output", False

        with patch("evolve.application.run_loop._run_monitored_subprocess", side_effect=mock_monitored), \
             patch("evolve.application.run_loop._is_self_evolving", return_value=True), \
             patch("evolve.infrastructure.reporting.generator._generate_evolution_report"), \
             patch("evolve.application.run_loop.fire_hook") as mock_fire_hook, \
             pytest.raises(SystemExit) as exc:
            _run_rounds(
                project_dir, run_dir, imp_path, ui,
                start_round=1, max_rounds=10, check_cmd="pytest",
                allow_installs=False, timeout=300, model="claude-opus-4-6",
            )
        assert exc.value.code == 3
        structural_calls = [
            c for c in mock_fire_hook.call_args_list
            if len(c.args) >= 2 and c.args[1] == "on_structural_change"
        ]
        assert len(structural_calls) == 1
        call_kwargs = structural_calls[0].kwargs
        assert call_kwargs["status"] == "structural_change"
        extra = call_kwargs["extra_env"]
        assert extra["EVOLVE_STRUCTURAL_REASON"] == "moved hooks.py"
        assert extra["EVOLVE_STRUCTURAL_VERIFY"] == "python -m evolve --help"
        assert extra["EVOLVE_STRUCTURAL_ROUND"] == "2"

    def test_forever_mode_does_not_bypass(self, tmp_path: Path):
        """--forever does NOT bypass structural change — still exits 3."""
        project_dir, run_dir, imp_path = self._setup_project(tmp_path)
        ui = self.ui

        def mock_monitored(cmd, cwd, ui_, round_num, watchdog_timeout=120):
            convo = run_dir / f"conversation_loop_{round_num}.md"
            convo.write_text("# Round conversation")
            (run_dir / "COMMIT_MSG").write_text("STRUCTURAL: test")
            (run_dir / "RESTART_REQUIRED").write_text(
                "reason: structural change\n"
                "verify: pytest\n"
                "resume: evolve start . --resume\n"
                "round: 1\n"
                "timestamp: 2026-04-23T21:00:00Z\n"
            )
            imp_path.write_text("- [x] [functional] do something\n")
            return 0, "output", False

        with patch("evolve.application.run_loop._run_monitored_subprocess", side_effect=mock_monitored), \
             patch("evolve.application.run_loop._is_self_evolving", return_value=True), \
             patch("evolve.infrastructure.reporting.generator._generate_evolution_report"), \
             pytest.raises(SystemExit) as exc:
            _run_rounds(
                project_dir, run_dir, imp_path, ui,
                start_round=1, max_rounds=10, check_cmd="pytest",
                allow_installs=False, timeout=300, model="claude-opus-4-6",
            )
        assert exc.value.code == 3

    def test_no_restart_required_continues_normally(self, tmp_path: Path):
        """Without RESTART_REQUIRED, convergence works normally (exit 0)."""
        project_dir, run_dir, imp_path = self._setup_project(tmp_path)
        ui = self.ui

        def mock_monitored(cmd, cwd, ui_, round_num, watchdog_timeout=120):
            convo = run_dir / f"conversation_loop_{round_num}.md"
            convo.write_text("# Round conversation")
            (run_dir / "CONVERGED").write_text("All done")
            imp_path.write_text("- [x] [functional] do something\n")
            return 0, "output", False

        with patch("evolve.application.run_loop._run_monitored_subprocess", side_effect=mock_monitored), \
             patch("evolve.application.run_loop._is_self_evolving", return_value=True), \
             patch("evolve.infrastructure.reporting.generator._generate_evolution_report"), \
             patch("evolve.application.run_loop._run_party_mode"), \
             pytest.raises(SystemExit) as exc:
            _run_rounds(
                project_dir, run_dir, imp_path, ui,
                start_round=1, max_rounds=10, check_cmd="pytest",
                allow_installs=False, timeout=300, model="claude-opus-4-6",
            )
        assert exc.value.code == 0
        ui.structural_change_required.assert_not_called()
