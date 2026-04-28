"""Tests for forever-mode helpers — _setup_forever_branch, _forever_restart."""

import subprocess
import textwrap
from pathlib import Path
from unittest.mock import MagicMock, patch

from evolve.git import _setup_forever_branch
from evolve.party import _forever_restart

# ---------------------------------------------------------------------------
# _setup_forever_branch
# ---------------------------------------------------------------------------

class TestSetupForeverBranch:
    def _init_git(self, path: Path):
        """Initialize a git repo with an initial commit."""
        subprocess.run(["git", "init"], cwd=str(path), capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=str(path), capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=str(path), capture_output=True)
        (path / "README.md").write_text("# Test\n")
        subprocess.run(["git", "add", "-A"], cwd=str(path), capture_output=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=str(path), capture_output=True)

    def test_creates_branch(self, tmp_path: Path):
        self._init_git(tmp_path)
        _setup_forever_branch(tmp_path)
        result = subprocess.run(
            ["git", "branch", "--show-current"],
            cwd=str(tmp_path), capture_output=True, text=True,
        )
        branch = result.stdout.strip()
        assert branch.startswith("evolve/")

    def test_exits_on_failure(self, tmp_path: Path):
        """Exits with code 2 if git checkout -b fails (not a git repo)."""
        import pytest
        with pytest.raises(SystemExit) as exc_info:
            _setup_forever_branch(tmp_path)
        assert exc_info.value.code == 2


# ---------------------------------------------------------------------------
# _forever_restart
# ---------------------------------------------------------------------------

class TestForeverRestart:
    def setup_method(self):
        """Fresh UI mock per test — avoids per-test MagicMock() boilerplate."""
        self.ui = MagicMock()

    def test_adopts_readme_proposal(self, tmp_path: Path):
        """README_proposal.md replaces README.md when present."""
        run_dir = tmp_path / "runs" / "session1"
        run_dir.mkdir(parents=True)
        improvements = tmp_path / "runs" / "improvements.md"
        improvements.write_text("# Improvements\n- [x] done\n- [ ] pending\n")

        proposal = run_dir / "README_proposal.md"
        proposal.write_text("# New README\nProposed content.\n")
        readme = tmp_path / "README.md"
        readme.write_text("# Old README\n")

        _forever_restart(tmp_path, run_dir, improvements, self.ui)

        assert readme.read_text() == "# New README\nProposed content.\n"
        assert improvements.read_text() == "# Improvements\n"
        self.ui.info.assert_any_call("  Forever mode: adopting README_proposal.md as new README.md")
        self.ui.info.assert_any_call("  Forever mode: resetting improvements.md for next cycle")

    def test_no_proposal_warns(self, tmp_path: Path):
        """Warns and continues when no README_proposal.md exists."""
        run_dir = tmp_path / "runs" / "session1"
        run_dir.mkdir(parents=True)
        improvements = tmp_path / "runs" / "improvements.md"
        improvements.write_text("# Improvements\n- [x] done\n")

        readme = tmp_path / "README.md"
        readme.write_text("# Original README\n")

        _forever_restart(tmp_path, run_dir, improvements, self.ui)

        # README unchanged
        assert readme.read_text() == "# Original README\n"
        # improvements still reset
        assert improvements.read_text() == "# Improvements\n"
        self.ui.warn.assert_called_once_with(
            "No README_proposal.md produced — restarting with current README.md"
        )


# ---------------------------------------------------------------------------
# _setup_forever_branch — edge cases
# ---------------------------------------------------------------------------

class TestSetupForeverBranchEdgeCases:
    """Edge-case tests for _setup_forever_branch."""

    def _init_git(self, path: Path):
        """Initialize a git repo with an initial commit."""
        subprocess.run(["git", "init"], cwd=str(path), capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=str(path), capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=str(path), capture_output=True)
        (path / "README.md").write_text("# Test\n")
        subprocess.run(["git", "add", "-A"], cwd=str(path), capture_output=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=str(path), capture_output=True)

    def test_branch_name_uses_timestamp_format(self, tmp_path: Path):
        """Branch name follows evolve/YYYYMMDD_HHMMSS pattern."""
        self._init_git(tmp_path)
        with patch("evolve.infrastructure.git.adapter.datetime") as mock_dt:
            mock_dt.now.return_value.strftime.return_value = "20260325_120000"
            _setup_forever_branch(tmp_path)

        result = subprocess.run(
            ["git", "branch", "--show-current"],
            cwd=str(tmp_path), capture_output=True, text=True,
        )
        assert result.stdout.strip() == "evolve/20260325_120000"

    def test_exits_code_2_on_git_failure(self, tmp_path: Path):
        """sys.exit(2) when git checkout -b fails (no git repo)."""
        import pytest
        with pytest.raises(SystemExit) as exc_info:
            _setup_forever_branch(tmp_path)
        assert exc_info.value.code == 2

    def test_exits_code_2_when_branch_already_exists(self, tmp_path: Path):
        """sys.exit(2) when branch name collides with existing branch."""
        import pytest
        self._init_git(tmp_path)
        # Create branch first time
        fixed_ts = "20260101_000000"
        with patch("evolve.infrastructure.git.adapter.datetime") as mock_dt:
            mock_dt.now.return_value.strftime.return_value = fixed_ts
            _setup_forever_branch(tmp_path)

        # Switch back to main so the second call tries to create same branch
        subprocess.run(["git", "checkout", "-b", "temp"], cwd=str(tmp_path), capture_output=True)

        with pytest.raises(SystemExit) as exc_info:
            with patch("evolve.infrastructure.git.adapter.datetime") as mock_dt2:
                mock_dt2.now.return_value.strftime.return_value = fixed_ts
                _setup_forever_branch(tmp_path)
        assert exc_info.value.code == 2

    def test_error_message_logged_on_failure(self, tmp_path: Path):
        """Error message is emitted via ui.error on git failure."""
        import pytest
        mock_ui = MagicMock()
        with pytest.raises(SystemExit):
            _setup_forever_branch(tmp_path, ui=mock_ui)
        mock_ui.error.assert_called_once()
        assert "Failed to create branch" in mock_ui.error.call_args[0][0]


# ---------------------------------------------------------------------------
# _forever_restart — edge cases
# ---------------------------------------------------------------------------



# ---------------------------------------------------------------------------
# _forever_restart — edge cases
# ---------------------------------------------------------------------------

class TestForeverRestartEdgeCases:
    """Edge-case tests for _forever_restart."""

    def setup_method(self):
        self.ui = MagicMock()

    def test_malformed_readme_proposal_adopted_as_is(self, tmp_path: Path):
        """Malformed README_proposal.md content is still copied verbatim."""
        run_dir = tmp_path / "runs" / "session1"
        run_dir.mkdir(parents=True)
        improvements = tmp_path / "runs" / "improvements.md"
        improvements.write_text("# Improvements\n- [x] done\n")

        # Malformed content — not valid markdown, binary-like garbage
        malformed = "<<<\x00\x01 broken {{{ unclosed\n\n\n###"
        (run_dir / "README_proposal.md").write_text(malformed)
        readme = tmp_path / "README.md"
        readme.write_text("# Old\n")

        _forever_restart(tmp_path, run_dir, improvements, self.ui)

        assert readme.read_text() == malformed
        self.ui.info.assert_any_call(
            "  Forever mode: adopting README_proposal.md as new README.md"
        )

    def test_empty_readme_proposal(self, tmp_path: Path):
        """Empty README_proposal.md results in empty README.md."""
        run_dir = tmp_path / "runs" / "session1"
        run_dir.mkdir(parents=True)
        improvements = tmp_path / "runs" / "improvements.md"
        improvements.write_text("# Improvements\n- [ ] pending\n")

        (run_dir / "README_proposal.md").write_text("")
        readme = tmp_path / "README.md"
        readme.write_text("# Old\n")

        _forever_restart(tmp_path, run_dir, improvements, self.ui)

        assert readme.read_text() == ""

    def test_improvements_reset_with_complex_content(self, tmp_path: Path):
        """improvements.md is reset regardless of how complex it was."""
        run_dir = tmp_path / "runs" / "session1"
        run_dir.mkdir(parents=True)
        improvements = tmp_path / "runs" / "improvements.md"
        complex_content = textwrap.dedent("""\
            # Improvements

            - [x] [functional] First improvement
            - [x] [performance] Second improvement
            - [x] [functional] [needs-package] Third improvement
            - [ ] [functional] Fourth improvement
            - [ ] [performance] Fifth improvement
        """)
        improvements.write_text(complex_content)
        readme = tmp_path / "README.md"
        readme.write_text("# README\n")

        _forever_restart(tmp_path, run_dir, improvements, self.ui)

        assert improvements.read_text() == "# Improvements\n"

    def test_converged_file_not_deleted(self, tmp_path: Path):
        """CONVERGED file in run_dir is preserved after restart."""
        run_dir = tmp_path / "runs" / "session1"
        run_dir.mkdir(parents=True)
        improvements = tmp_path / "runs" / "improvements.md"
        improvements.write_text("# Improvements\n")

        converged = run_dir / "CONVERGED"
        converged.write_text("Converged: all claims verified")
        readme = tmp_path / "README.md"
        readme.write_text("# Old\n")

        _forever_restart(tmp_path, run_dir, improvements, self.ui)

        assert converged.is_file()
        assert converged.read_text() == "Converged: all claims verified"

    def test_no_readme_in_project_still_creates_one(self, tmp_path: Path):
        """If no README.md exists and proposal is present, it gets created."""
        run_dir = tmp_path / "runs" / "session1"
        run_dir.mkdir(parents=True)
        improvements = tmp_path / "runs" / "improvements.md"
        improvements.write_text("# Improvements\n")

        (run_dir / "README_proposal.md").write_text("# Brand New README\n")

        _forever_restart(tmp_path, run_dir, improvements, self.ui)

        assert (tmp_path / "README.md").read_text() == "# Brand New README\n"

    def test_unicode_readme_proposal(self, tmp_path: Path):
        """README_proposal.md with unicode content is handled correctly."""
        run_dir = tmp_path / "runs" / "session1"
        run_dir.mkdir(parents=True)
        improvements = tmp_path / "runs" / "improvements.md"
        improvements.write_text("# Improvements\n")

        unicode_content = "# 🚀 Evolve\n\n日本語テスト — ñoño — café ☕\n"
        (run_dir / "README_proposal.md").write_text(unicode_content)
        readme = tmp_path / "README.md"
        readme.write_text("# Old\n")

        _forever_restart(tmp_path, run_dir, improvements, self.ui)

        assert readme.read_text() == unicode_content


# ---------------------------------------------------------------------------
# _check_spec_freshness
# ---------------------------------------------------------------------------

