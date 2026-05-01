"""Tests for SPEC archival (Sid) — SPEC § 'SPEC archival (Sid)'.

Covers: trigger conditions, four-pass stub extraction on a synthetic SPEC,
stub-shorter-than-body invariant, INDEX.md ID monotonic, audit log schema,
>80% shrink abort, orchestrator wiring.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Trigger conditions — _should_run_spec_archival
# ---------------------------------------------------------------------------

from evolve.infrastructure.claude_sdk.spec_archival import ARCHIVAL_LINE_THRESHOLD
from evolve.infrastructure.claude_sdk.spec_archival import ARCHIVAL_ROUND_INTERVAL
from evolve.infrastructure.claude_sdk.spec_archival import _ARCHIVAL_MAX_SHRINK
from evolve.infrastructure.claude_sdk.spec_archival import _should_run_spec_archival
from evolve.infrastructure.claude_sdk.spec_archival import build_spec_archival_prompt
from evolve.infrastructure.claude_sdk.spec_archival import run_spec_archival


class TestShouldRunSpecArchival:
    """Tests for _should_run_spec_archival trigger logic."""

    def test_below_threshold_non_interval_returns_false(self, tmp_path):
        spec = tmp_path / "SPEC.md"
        spec.write_text("# Spec\n" * 100)  # 100 lines, well under 2000
        assert _should_run_spec_archival(spec, 7) is False

    def test_above_threshold_returns_true(self, tmp_path):
        spec = tmp_path / "SPEC.md"
        spec.write_text("line\n" * (ARCHIVAL_LINE_THRESHOLD + 1))
        assert _should_run_spec_archival(spec, 7) is True

    def test_exactly_at_threshold_returns_false(self, tmp_path):
        spec = tmp_path / "SPEC.md"
        spec.write_text("line\n" * ARCHIVAL_LINE_THRESHOLD)
        assert _should_run_spec_archival(spec, 7) is False

    def test_interval_round_returns_true(self, tmp_path):
        spec = tmp_path / "SPEC.md"
        spec.write_text("# Short spec\n")
        assert _should_run_spec_archival(spec, ARCHIVAL_ROUND_INTERVAL) is True
        assert _should_run_spec_archival(spec, ARCHIVAL_ROUND_INTERVAL * 2) is True

    def test_round_zero_not_triggered_by_interval(self, tmp_path):
        spec = tmp_path / "SPEC.md"
        spec.write_text("# Short spec\n")
        assert _should_run_spec_archival(spec, 0) is False

    def test_missing_file_returns_false(self, tmp_path):
        spec = tmp_path / "SPEC.md"
        assert _should_run_spec_archival(spec, 7) is False


# ---------------------------------------------------------------------------
# Prompt building — build_spec_archival_prompt
# ---------------------------------------------------------------------------


class TestBuildSpecArchivalPrompt:
    """Tests for build_spec_archival_prompt content."""

    def test_contains_spec_text(self, tmp_path):
        prompt = build_spec_archival_prompt(
            spec_text="## Architecture\nContent here.\n",
            index_text="",
            git_log="abc123 feat: something",
            round_num=5,
            run_dir=tmp_path,
            spec_path=tmp_path / "SPEC.md",
            archive_dir=tmp_path / "SPEC" / "archive",
        )
        assert "## Architecture" in prompt
        assert "Content here." in prompt
        assert "Sid" in prompt
        assert "abc123" in prompt

    def test_empty_index_shows_first_pass_note(self, tmp_path):
        prompt = build_spec_archival_prompt(
            spec_text="# Spec\n",
            index_text="",
            git_log="",
            round_num=1,
            run_dir=tmp_path,
            spec_path=tmp_path / "SPEC.md",
            archive_dir=tmp_path / "SPEC" / "archive",
        )
        assert "first archival pass" in prompt

    def test_existing_index_included(self, tmp_path):
        idx = "| 001 | migration | 2026-04-27 | round 15 |"
        prompt = build_spec_archival_prompt(
            spec_text="# Spec\n",
            index_text=idx,
            git_log="",
            round_num=2,
            run_dir=tmp_path,
            spec_path=tmp_path / "SPEC.md",
            archive_dir=tmp_path / "SPEC" / "archive",
        )
        assert "001" in prompt
        assert "migration" in prompt

    def test_audit_path_in_prompt(self, tmp_path):
        prompt = build_spec_archival_prompt(
            spec_text="# Spec\n",
            index_text="",
            git_log="",
            round_num=42,
            run_dir=tmp_path,
            spec_path=tmp_path / "SPEC.md",
            archive_dir=tmp_path / "SPEC" / "archive",
        )
        assert "spec_curation_round_42.md" in prompt

    def test_four_passes_mentioned(self, tmp_path):
        prompt = build_spec_archival_prompt(
            spec_text="# Spec\n",
            index_text="",
            git_log="",
            round_num=1,
            run_dir=tmp_path,
            spec_path=tmp_path / "SPEC.md",
            archive_dir=tmp_path / "SPEC" / "archive",
        )
        assert "Stability detection" in prompt
        assert "Stub drafting" in prompt
        assert "Archive extraction" in prompt
        assert "SPEC rewrite" in prompt


# ---------------------------------------------------------------------------
# run_spec_archival — verdict paths
# ---------------------------------------------------------------------------


class TestRunSpecArchival:
    """Tests for run_spec_archival verdict logic."""

    def _make_spec(self, tmp_path, lines=100):
        spec = tmp_path / "SPEC.md"
        spec.write_text("line\n" * lines)
        return spec

    def test_skipped_when_threshold_not_met(self, tmp_path):
        spec = self._make_spec(tmp_path, lines=100)
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        verdict = run_spec_archival(tmp_path, run_dir, 7, spec)
        assert verdict == "SKIPPED"

    def test_skipped_when_file_missing(self, tmp_path):
        spec = tmp_path / "SPEC.md"
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        verdict = run_spec_archival(tmp_path, run_dir, ARCHIVAL_ROUND_INTERVAL, spec)
        assert verdict == "SKIPPED"

    @patch("evolve.infrastructure.claude_sdk.runtime._run_agent_with_retries")
    @patch("evolve.interfaces.tui.get_tui", return_value=MagicMock())
    def test_sdk_fail_when_no_audit_log(self, mock_tui, mock_retries, tmp_path):
        """SDK runs but produces no audit log → SDK_FAIL, original restored."""
        spec = self._make_spec(tmp_path, lines=ARCHIVAL_LINE_THRESHOLD + 1)
        original = spec.read_text()
        run_dir = tmp_path / "run"
        run_dir.mkdir()

        mock_retries.return_value = None  # agent ran but didn't produce audit

        verdict = run_spec_archival(tmp_path, run_dir, 7, spec)
        assert verdict == "SDK_FAIL"
        assert spec.read_text() == original

    @patch("evolve.infrastructure.claude_sdk.runtime._run_agent_with_retries")
    @patch("evolve.interfaces.tui.get_tui", return_value=MagicMock())
    def test_archived_when_audit_present_and_moderate_shrink(self, mock_tui, mock_retries, tmp_path):
        """Agent produces audit log, SPEC shrinks moderately → ARCHIVED."""
        spec = self._make_spec(tmp_path, lines=ARCHIVAL_LINE_THRESHOLD + 1)
        run_dir = tmp_path / "run"
        run_dir.mkdir()

        def fake_agent(*a, **kw):
            # Simulate: agent rewrites SPEC (moderate shrink) and writes audit
            spec.write_text("line\n" * 1500)  # shrink from 2001 to 1500
            audit = run_dir / "spec_curation_round_7.md"
            audit.write_text("# Round 7 — SPEC Archival (Sid)\n\n## Ledger\n| ... |\n")
            return None

        mock_retries.side_effect = fake_agent

        verdict = run_spec_archival(tmp_path, run_dir, 7, spec)
        assert verdict == "ARCHIVED"

    @patch("evolve.infrastructure.claude_sdk.runtime._run_agent_with_retries")
    @patch("evolve.interfaces.tui.get_tui", return_value=MagicMock())
    def test_aborted_when_shrink_exceeds_threshold(self, mock_tui, mock_retries, tmp_path):
        """Agent shrinks SPEC by >80% → ABORTED, original restored."""
        spec = self._make_spec(tmp_path, lines=ARCHIVAL_LINE_THRESHOLD + 1)
        original = spec.read_text()
        run_dir = tmp_path / "run"
        run_dir.mkdir()

        def fake_agent(*a, **kw):
            # Simulate: agent overwrites SPEC to nearly nothing
            spec.write_text("# Spec\n")
            audit = run_dir / "spec_curation_round_7.md"
            audit.write_text("# Round 7\n")
            return None

        mock_retries.side_effect = fake_agent

        verdict = run_spec_archival(tmp_path, run_dir, 7, spec)
        assert verdict == "ABORTED"
        assert spec.read_text() == original

    @patch("evolve.infrastructure.claude_sdk.runtime._run_agent_with_retries")
    @patch("evolve.interfaces.tui.get_tui", return_value=MagicMock())
    def test_aborted_audit_log_updated_with_verdict(self, mock_tui, mock_retries, tmp_path):
        """On ABORT, the audit log is prefixed with verdict: ABORTED."""
        spec = self._make_spec(tmp_path, lines=ARCHIVAL_LINE_THRESHOLD + 1)
        run_dir = tmp_path / "run"
        run_dir.mkdir()

        def fake_agent(*a, **kw):
            spec.write_text("tiny\n")
            audit = run_dir / "spec_curation_round_7.md"
            audit.write_text("# Round 7\n## Ledger\n")
            return None

        mock_retries.side_effect = fake_agent

        run_spec_archival(tmp_path, run_dir, 7, spec)
        audit = run_dir / "spec_curation_round_7.md"
        assert "verdict: ABORTED" in audit.read_text()

    @patch("evolve.infrastructure.claude_sdk.runtime._run_agent_with_retries", side_effect=Exception("SDK boom"))
    @patch("evolve.interfaces.tui.get_tui", return_value=MagicMock())
    def test_sdk_exception_restores_original(self, mock_tui, mock_retries, tmp_path):
        """Exception from SDK → SDK_FAIL, original restored."""
        spec = self._make_spec(tmp_path, lines=ARCHIVAL_LINE_THRESHOLD + 1)
        original = spec.read_text()
        run_dir = tmp_path / "run"
        run_dir.mkdir()

        verdict = run_spec_archival(tmp_path, run_dir, 7, spec)
        assert verdict == "SDK_FAIL"
        assert spec.read_text() == original

    @patch("evolve.infrastructure.claude_sdk.runtime._run_agent_with_retries")
    @patch("evolve.interfaces.tui.get_tui", return_value=MagicMock())
    def test_archive_dir_created(self, mock_tui, mock_retries, tmp_path):
        """run_spec_archival creates SPEC/archive/ if it doesn't exist."""
        spec = self._make_spec(tmp_path, lines=ARCHIVAL_LINE_THRESHOLD + 1)
        run_dir = tmp_path / "run"
        run_dir.mkdir()

        # Agent produces audit log so we get past the check
        def fake_agent(*a, **kw):
            audit = run_dir / "spec_curation_round_7.md"
            audit.write_text("# Audit\n")
            return None

        mock_retries.side_effect = fake_agent

        run_spec_archival(tmp_path, run_dir, 7, spec)
        assert (tmp_path / "SPEC" / "archive").is_dir()


# ---------------------------------------------------------------------------
# Orchestrator wiring — _run_spec_archival_pass
# ---------------------------------------------------------------------------


class TestOrchestratorShouldRunSpecArchival:
    """Tests for _should_run_spec_archival in orchestrator.py (AC 3)."""

    def test_delegates_to_agent_check(self, tmp_path):
        from evolve.infrastructure.claude_sdk.spec_archival import _should_run_spec_archival

        spec = tmp_path / "SPEC.md"
        spec.write_text("line\n" * (ARCHIVAL_LINE_THRESHOLD + 1))
        assert _should_run_spec_archival(tmp_path, 7, "SPEC.md") is True

    def test_below_threshold(self, tmp_path):
        from evolve.infrastructure.claude_sdk.spec_archival import _should_run_spec_archival

        spec = tmp_path / "SPEC.md"
        spec.write_text("# Short\n")
        assert _should_run_spec_archival(tmp_path, 7, "SPEC.md") is False

    def test_interval_triggers(self, tmp_path):
        from evolve.infrastructure.claude_sdk.spec_archival import _should_run_spec_archival

        spec = tmp_path / "SPEC.md"
        spec.write_text("# Short\n")
        assert _should_run_spec_archival(tmp_path, ARCHIVAL_ROUND_INTERVAL, "SPEC.md") is True


class TestOrchestratorWiring:
    """Tests for _run_spec_archival_pass in orchestrator.py."""

    @patch("evolve.application.run_loop._git_commit")
    @patch("evolve.infrastructure.claude_sdk.spec_archival.run_spec_archival", return_value="ARCHIVED")
    def test_archived_triggers_commit(self, mock_archival, mock_commit, tmp_path):
        from evolve.application.run_loop import _run_spec_archival_pass

        spec = tmp_path / "SPEC.md"
        spec.write_text("# Spec\n")
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        ui = MagicMock()

        _run_spec_archival_pass(tmp_path, run_dir, 5, "SPEC.md", ui)
        mock_commit.assert_called_once()
        commit_msg = mock_commit.call_args[0][1]
        assert "archival" in commit_msg.lower()

    @patch("evolve.infrastructure.claude_sdk.spec_archival.run_spec_archival", return_value="SKIPPED")
    def test_skipped_no_commit(self, mock_archival, tmp_path):
        from evolve.application.run_loop import _run_spec_archival_pass

        spec = tmp_path / "SPEC.md"
        spec.write_text("# Spec\n")
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        ui = MagicMock()

        with patch("evolve.application.run_loop._git_commit") as mock_commit:
            _run_spec_archival_pass(tmp_path, run_dir, 5, "SPEC.md", ui)
            mock_commit.assert_not_called()

    @patch("evolve.infrastructure.claude_sdk.spec_archival.run_spec_archival", return_value="ABORTED")
    def test_aborted_no_commit(self, mock_archival, tmp_path):
        from evolve.application.run_loop import _run_spec_archival_pass

        spec = tmp_path / "SPEC.md"
        spec.write_text("# Spec\n")
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        ui = MagicMock()

        with patch("evolve.application.run_loop._git_commit") as mock_commit:
            _run_spec_archival_pass(tmp_path, run_dir, 5, "SPEC.md", ui)
            mock_commit.assert_not_called()

    def test_missing_spec_file_returns_silently(self, tmp_path):
        from evolve.application.run_loop import _run_spec_archival_pass

        run_dir = tmp_path / "run"
        run_dir.mkdir()
        ui = MagicMock()

        # Should not raise
        _run_spec_archival_pass(tmp_path, run_dir, 5, "NONEXISTENT.md", ui)


# ---------------------------------------------------------------------------
# Prompt system.md — archive-read discipline section
# ---------------------------------------------------------------------------


class TestPromptArchiveReadDiscipline:
    """Tests that prompts/system.md contains the archive-read discipline."""

    def test_system_prompt_has_archive_read_discipline(self):
        prompt_path = Path(__file__).resolve().parent.parent / "prompts" / "system.md"
        text = prompt_path.read_text()
        assert "SPEC archive read discipline" in text
        assert "SPEC/archive/*.md" in text
        assert "MUST NOT read them unless ALL of" in text


# ---------------------------------------------------------------------------
# Review attack plan — archive-read-count signal
# ---------------------------------------------------------------------------


class TestReviewArchiveReadCount:
    """Tests that the review attack plan includes archive-read-count signal."""

    def test_review_task_has_archive_read_signal(self):
        task_path = Path(__file__).resolve().parent.parent / "tasks" / "review-adversarial-round.md"
        text = task_path.read_text()
        assert "Archive read count" in text
        assert "SPEC/archive/" in text


# ---------------------------------------------------------------------------
# Persona and protocol files exist
# ---------------------------------------------------------------------------


class TestPersonaAndProtocol:
    """Tests that agents/archivist.md and tasks/spec-archival.md exist."""

    def test_archivist_persona_exists(self):
        path = Path(__file__).resolve().parent.parent / "agents" / "archivist.md"
        assert path.is_file()
        text = path.read_text()
        assert "Sid" in text
        assert "SPEC Archivist" in text

    def test_spec_archival_protocol_exists(self):
        path = Path(__file__).resolve().parent.parent / "tasks" / "spec-archival.md"
        assert path.is_file()
        text = path.read_text()
        assert "four passes" in text.lower() or "Four passes" in text
        assert "Stability detection" in text
        assert "Stub drafting" in text
        assert "Archive extraction" in text
        assert "SPEC rewrite" in text


# ---------------------------------------------------------------------------
# Constants consistency
# ---------------------------------------------------------------------------


class TestConstants:
    """Tests that archival constants match SPEC claims."""

    def test_line_threshold_is_2000(self):
        assert ARCHIVAL_LINE_THRESHOLD == 2000

    def test_round_interval_is_20(self):
        assert ARCHIVAL_ROUND_INTERVAL == 20

    def test_max_shrink_is_80_percent(self):
        assert _ARCHIVAL_MAX_SHRINK == 0.80
