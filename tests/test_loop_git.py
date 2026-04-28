"""Tests for _ensure_git and _git_commit (extracted from test_loop_extended.py)."""

from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from evolve.git import _ensure_git, _git_commit


# ---------------------------------------------------------------------------
# _ensure_git
# ---------------------------------------------------------------------------

class TestEnsureGit:
    def test_not_a_git_repo(self, tmp_path: Path):
        """Exits with code 2 if not a git repo."""
        mock_result = MagicMock(returncode=1)
        with patch("evolve.infrastructure.git.adapter.subprocess.run", return_value=mock_result):
            with pytest.raises(SystemExit) as exc:
                _ensure_git(tmp_path)
            assert exc.value.code == 2

    def test_clean_git_repo(self, tmp_path: Path):
        """No commit needed if working tree is clean."""
        git_check = MagicMock(returncode=0)  # is a git repo
        status_clean = MagicMock(returncode=0, stdout="")  # clean

        def side_effect(cmd, **kwargs):
            if cmd[1] == "rev-parse":
                return git_check
            if cmd[1] == "status":
                return status_clean
            return MagicMock(returncode=0)

        with patch("evolve.infrastructure.git.adapter.subprocess.run", side_effect=side_effect):
            _ensure_git(tmp_path)  # should not raise

    def test_uncommitted_changes_triggers_commit(self, tmp_path: Path):
        """Uncommitted changes trigger git add + commit."""
        calls = []

        def side_effect(cmd, **kwargs):
            calls.append(cmd)
            if "rev-parse" in cmd:
                return MagicMock(returncode=0)
            if "status" in cmd:
                return MagicMock(returncode=0, stdout="M file.py\n")
            return MagicMock(returncode=0)

        with patch("evolve.infrastructure.git.adapter.subprocess.run", side_effect=side_effect):
            _ensure_git(tmp_path)

        # Should have called git add -A and git commit
        cmd_strs = [" ".join(str(c) for c in cmd) for cmd in calls]
        assert any("add" in s for s in cmd_strs)
        assert any("commit" in s for s in cmd_strs)

    def test_error_message_includes_project_path(self, tmp_path: Path):
        """Error message should reference the project directory."""
        mock_ui = MagicMock()
        mock_result = MagicMock(returncode=1)
        with patch("evolve.infrastructure.git.adapter.subprocess.run", return_value=mock_result):
            with pytest.raises(SystemExit):
                _ensure_git(tmp_path, ui=mock_ui)
        mock_ui.error.assert_called_once()
        assert str(tmp_path) in mock_ui.error.call_args[0][0]

    def test_uncommitted_commit_message(self, tmp_path: Path):
        """Auto-commit uses the expected snapshot message."""
        calls = []

        def side_effect(cmd, **kwargs):
            calls.append((list(cmd), kwargs))
            if "rev-parse" in cmd:
                return MagicMock(returncode=0)
            if "status" in cmd:
                return MagicMock(returncode=0, stdout="M dirty.py\n")
            return MagicMock(returncode=0)

        with patch("evolve.infrastructure.git.adapter.subprocess.run", side_effect=side_effect):
            _ensure_git(tmp_path)

        commit_calls = [(c, kw) for c, kw in calls if "commit" in c]
        assert len(commit_calls) == 1
        commit_cmd = commit_calls[0][0]
        assert "-m" in commit_cmd
        msg_idx = commit_cmd.index("-m") + 1
        assert "evolve" in commit_cmd[msg_idx].lower()
        assert "snapshot" in commit_cmd[msg_idx].lower()

    def test_mixed_staged_and_unstaged_changes(self, tmp_path: Path):
        """git status with mixed staged/unstaged (M + ??) triggers single commit."""
        calls = []

        def side_effect(cmd, **kwargs):
            calls.append(list(cmd))
            if "rev-parse" in cmd:
                return MagicMock(returncode=0)
            if "status" in cmd:
                return MagicMock(
                    returncode=0,
                    stdout="M  staged.py\n?? untracked.py\n A added.py\n",
                )
            return MagicMock(returncode=0)

        with patch("evolve.infrastructure.git.adapter.subprocess.run", side_effect=side_effect):
            _ensure_git(tmp_path)

        cmd_strs = [" ".join(c) for c in calls]
        # git add -A should capture both staged and untracked
        assert any("add" in s and "-A" in s for s in cmd_strs)
        assert any("commit" in s for s in cmd_strs)

    def test_ui_uncommitted_called_on_dirty_tree(self, tmp_path: Path):
        """ui.uncommitted() is called when working tree is dirty."""
        mock_ui = MagicMock()

        def side_effect(cmd, **kwargs):
            if "rev-parse" in cmd:
                return MagicMock(returncode=0)
            if "status" in cmd:
                return MagicMock(returncode=0, stdout="M file.py\n")
            return MagicMock(returncode=0)

        with patch("evolve.infrastructure.git.adapter.subprocess.run", side_effect=side_effect):
            _ensure_git(tmp_path, ui=mock_ui)

        mock_ui.uncommitted.assert_called_once()

    def test_ui_uncommitted_not_called_on_clean_tree(self, tmp_path: Path):
        """ui.uncommitted() is NOT called when working tree is clean."""
        mock_ui = MagicMock()

        def side_effect(cmd, **kwargs):
            if "rev-parse" in cmd:
                return MagicMock(returncode=0)
            if "status" in cmd:
                return MagicMock(returncode=0, stdout="")
            return MagicMock(returncode=0)

        with patch("evolve.infrastructure.git.adapter.subprocess.run", side_effect=side_effect):
            _ensure_git(tmp_path, ui=mock_ui)

        mock_ui.uncommitted.assert_not_called()

    def test_custom_ui_passed_through(self, tmp_path: Path):
        """When a custom ui is passed, it should be used instead of _DefaultUI."""
        mock_ui = MagicMock()
        mock_result = MagicMock(returncode=1)
        with patch("evolve.infrastructure.git.adapter.subprocess.run", return_value=mock_result):
            with pytest.raises(SystemExit):
                _ensure_git(tmp_path, ui=mock_ui)
        # The custom ui should have received the error
        mock_ui.error.assert_called_once()

    def test_default_ui_used_when_none(self, tmp_path: Path):
        """When ui=None, _DefaultUI fallback is used."""
        from evolve.infrastructure.git.adapter import _DefaultUI
        mock_result = MagicMock(returncode=1)
        with patch("evolve.infrastructure.git.adapter.subprocess.run", return_value=mock_result), \
             patch.object(_DefaultUI, "error") as mock_error:
            with pytest.raises(SystemExit):
                _ensure_git(tmp_path)
        mock_error.assert_called_once()

    def test_only_whitespace_status_is_clean(self, tmp_path: Path):
        """Status output with only whitespace should be treated as clean."""
        calls = []

        def side_effect(cmd, **kwargs):
            calls.append(list(cmd))
            if "rev-parse" in cmd:
                return MagicMock(returncode=0)
            if "status" in cmd:
                return MagicMock(returncode=0, stdout="   \n  \n")
            return MagicMock(returncode=0)

        with patch("evolve.infrastructure.git.adapter.subprocess.run", side_effect=side_effect):
            _ensure_git(tmp_path)

        cmd_strs = [" ".join(c) for c in calls]
        assert not any("commit" in s for s in cmd_strs)

    def test_cwd_passed_to_all_git_commands(self, tmp_path: Path):
        """All subprocess calls should use project_dir as cwd."""
        calls = []

        def side_effect(cmd, **kwargs):
            calls.append((list(cmd), kwargs))
            if "rev-parse" in cmd:
                return MagicMock(returncode=0)
            if "status" in cmd:
                return MagicMock(returncode=0, stdout="M file.py\n")
            return MagicMock(returncode=0)

        with patch("evolve.infrastructure.git.adapter.subprocess.run", side_effect=side_effect):
            _ensure_git(tmp_path)

        for cmd, kwargs in calls:
            assert kwargs.get("cwd") == tmp_path, f"cwd not set for {cmd}"


# ---------------------------------------------------------------------------
# _git_commit
# ---------------------------------------------------------------------------

class TestGitCommit:
    def test_nothing_to_commit(self, tmp_path: Path):
        """If no staged changes, skip commit."""
        calls = []

        def side_effect(cmd, **kwargs):
            calls.append(cmd)
            if "diff" in cmd:
                return MagicMock(returncode=0)  # no diff = nothing to commit
            return MagicMock(returncode=0)

        with patch("evolve.infrastructure.git.adapter.subprocess.run", side_effect=side_effect):
            _git_commit(tmp_path, "test msg")

        # Should NOT have called git commit
        cmd_strs = [" ".join(str(c) for c in cmd) for cmd in calls]
        assert not any("commit" in s and "diff" not in s for s in cmd_strs)

    def test_commit_and_push_success(self, tmp_path: Path):
        calls = []

        def side_effect(cmd, **kwargs):
            calls.append(cmd)
            if "diff" in cmd:
                return MagicMock(returncode=1)  # has changes
            if "push" in cmd:
                return MagicMock(returncode=0, stderr="")
            return MagicMock(returncode=0)

        with patch("evolve.infrastructure.git.adapter.subprocess.run", side_effect=side_effect):
            _git_commit(tmp_path, "feat: test")

        cmd_strs = [" ".join(str(c) for c in cmd) for cmd in calls]
        assert any("commit" in s for s in cmd_strs)
        assert any("push" in s for s in cmd_strs)

    def test_commit_push_failure(self, tmp_path: Path):
        """Push failure should not crash, just report."""
        def side_effect(cmd, **kwargs):
            if "diff" in cmd:
                return MagicMock(returncode=1)
            if "push" in cmd:
                return MagicMock(returncode=1, stderr="remote rejected")
            return MagicMock(returncode=0)

        with patch("evolve.infrastructure.git.adapter.subprocess.run", side_effect=side_effect):
            _git_commit(tmp_path, "feat: test")  # should not raise

    def test_commit_push_no_upstream_retries_with_set_upstream(self, tmp_path: Path):
        """Push with 'no upstream branch' error retries with -u origin <branch>."""
        calls = []

        def side_effect(cmd, **kwargs):
            calls.append(cmd)
            if "diff" in cmd:
                return MagicMock(returncode=1)
            if "branch" in cmd and "--show-current" in cmd:
                return MagicMock(returncode=0, stdout="evolve/123\n")
            if "push" in cmd and "-u" in cmd:
                return MagicMock(returncode=0, stderr="")
            if "push" in cmd:
                return MagicMock(
                    returncode=1,
                    stderr="fatal: The current branch has no upstream branch.",
                )
            return MagicMock(returncode=0)

        with patch("evolve.infrastructure.git.adapter.subprocess.run", side_effect=side_effect):
            _git_commit(tmp_path, "feat: test")

        cmd_strs = [" ".join(str(c) for c in cmd) for cmd in calls]
        assert any("-u" in s and "origin" in s for s in cmd_strs)
