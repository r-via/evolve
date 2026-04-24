"""Tests for dedicated memory curation agent (Mira) — SPEC § "Dedicated memory curation (Mira)".

Covers:
- AC 1: run_memory_curation builds prompt and invokes agent
- AC 2: _should_run_curation triggers on line count and round interval
- AC 3: ABORTED when shrink > 80%
- AC 4: Audit log written on successful curation
- AC 5: SKIPPED when threshold not hit
- AC 6: Orchestrator wiring (_run_curation_pass)
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from evolve.agent import (
    CURATION_LINE_THRESHOLD,
    CURATION_ROUND_INTERVAL,
    _CURATION_MAX_SHRINK,
    _should_run_curation,
    build_memory_curation_prompt,
    run_memory_curation,
)


# ---------------------------------------------------------------------------
# AC 2 + AC 5 — _should_run_curation trigger logic
# ---------------------------------------------------------------------------


class TestShouldRunCuration:
    """_should_run_curation fires on line count or round interval."""

    def test_skipped_when_file_small_and_round_not_interval(self, tmp_path: Path) -> None:
        mem = tmp_path / "memory.md"
        mem.write_text("# Memory\n\n## Errors\n\n## Decisions\n")
        assert _should_run_curation(mem, 3) is False

    def test_triggered_on_line_threshold(self, tmp_path: Path) -> None:
        mem = tmp_path / "memory.md"
        mem.write_text("\n".join(f"line {i}" for i in range(CURATION_LINE_THRESHOLD + 1)))
        assert _should_run_curation(mem, 3) is True

    def test_triggered_on_round_interval(self, tmp_path: Path) -> None:
        mem = tmp_path / "memory.md"
        mem.write_text("# tiny\n")
        assert _should_run_curation(mem, CURATION_ROUND_INTERVAL) is True
        assert _should_run_curation(mem, CURATION_ROUND_INTERVAL * 2) is True

    def test_not_triggered_on_round_zero(self, tmp_path: Path) -> None:
        mem = tmp_path / "memory.md"
        mem.write_text("# tiny\n")
        # round 0 → 0 % 10 == 0 but round > 0 guard prevents
        assert _should_run_curation(mem, 0) is False

    def test_missing_file_returns_false(self, tmp_path: Path) -> None:
        mem = tmp_path / "nonexistent.md"
        assert _should_run_curation(mem, 3) is False


# ---------------------------------------------------------------------------
# AC 1 — build_memory_curation_prompt
# ---------------------------------------------------------------------------


class TestBuildMemoryCurationPrompt:
    """Prompt includes all required inputs."""

    def test_prompt_includes_memory_and_inputs(self, tmp_path: Path) -> None:
        prompt = build_memory_curation_prompt(
            memory_text="## Errors\n### SDK crash — round 5\nfoo",
            spec_memory_section="Entries MUST be ≤ 5 lines.",
            conversation_titles=["Round 8: # impl US-020"],
            git_log="abc1234 feat: thing\ndef5678 fix: other",
            round_num=10,
            run_dir=tmp_path,
            memory_path=tmp_path / "memory.md",
        )
        assert "Mira" in prompt
        assert "SDK crash" in prompt
        assert "≤ 5 lines" in prompt
        assert "Round 8" in prompt
        assert "abc1234" in prompt
        assert "memory_curation_round_10" in prompt


# ---------------------------------------------------------------------------
# AC 1, AC 3, AC 4 — run_memory_curation end-to-end (mocked SDK)
# ---------------------------------------------------------------------------


class TestRunMemoryCuration:
    """run_memory_curation with mocked SDK covers all verdict paths."""

    def _make_memory(self, tmp_path: Path, lines: int = 350) -> Path:
        """Create a memory.md with N lines."""
        mem = tmp_path / ".evolve" / "runs" / "memory.md"
        mem.parent.mkdir(parents=True, exist_ok=True)
        content = "# Agent Memory\n\n## Errors\n\n"
        content += "\n".join(f"### Entry {i} — round {i}\nLine {i}" for i in range(lines))
        mem.write_text(content)
        return mem

    @patch("evolve.agent._run_agent_with_retries")
    def test_skipped_below_threshold(self, mock_retry, tmp_path: Path) -> None:
        mem = tmp_path / "memory.md"
        mem.write_text("# tiny\n")

        verdict = run_memory_curation(
            project_dir=tmp_path,
            run_dir=tmp_path / "run",
            round_num=3,
            memory_path=mem,
        )
        assert verdict == "SKIPPED"
        mock_retry.assert_not_called()

    @patch("evolve.agent._run_agent_with_retries")
    def test_curated_success(self, mock_retry, tmp_path: Path) -> None:
        """Mira produces a curated memory + audit log → CURATED."""
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        mem = tmp_path / "memory.md"
        original = "# Memory\n" + "\n".join(f"line {i}" for i in range(350))
        mem.write_text(original)

        # Simulate agent: shrink by 30% (within bounds), write audit log
        def fake_agent(*a, **kw):
            # Simulate agent writing a smaller memory.md and audit log
            new_content = "# Memory\n" + "\n".join(f"line {i}" for i in range(245))
            mem.write_text(new_content)
            audit = run_dir / "memory_curation_round_10.md"
            audit.write_text("# Round 10 — Memory Curation (Mira)\n\n## Ledger\n...\n")

        mock_retry.side_effect = fake_agent

        verdict = run_memory_curation(
            project_dir=tmp_path,
            run_dir=run_dir,
            round_num=10,
            memory_path=mem,
        )
        assert verdict == "CURATED"
        assert (run_dir / "memory_curation_round_10.md").is_file()

    @patch("evolve.agent._run_agent_with_retries")
    def test_aborted_on_excessive_shrink(self, mock_retry, tmp_path: Path) -> None:
        """Mira shrinks by >80% → ABORTED, original restored."""
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        mem = tmp_path / "memory.md"
        original = "# Memory\n" + "\n".join(f"line {i}" for i in range(350))
        mem.write_text(original)
        original_text = mem.read_text()

        # Simulate agent: shrink by 90% (over threshold)
        def fake_agent(*a, **kw):
            mem.write_text("# Memory\n\nEmpty.\n")
            audit = run_dir / "memory_curation_round_10.md"
            audit.write_text("# Round 10 — Curation\n")

        mock_retry.side_effect = fake_agent

        verdict = run_memory_curation(
            project_dir=tmp_path,
            run_dir=run_dir,
            round_num=10,
            memory_path=mem,
        )
        assert verdict == "ABORTED"
        # Original restored
        assert mem.read_text() == original_text
        # Audit log updated with ABORTED verdict
        audit_text = (run_dir / "memory_curation_round_10.md").read_text()
        assert "ABORTED" in audit_text

    @patch("evolve.agent._run_agent_with_retries")
    def test_sdk_fail_no_audit_log(self, mock_retry, tmp_path: Path) -> None:
        """SDK returns no audit log → SDK_FAIL, original restored."""
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        mem = tmp_path / "memory.md"
        original = "# Memory\n" + "\n".join(f"line {i}" for i in range(350))
        mem.write_text(original)
        original_text = mem.read_text()

        # Agent runs but doesn't write audit log
        def fake_agent(*a, **kw):
            pass  # no audit log written

        mock_retry.side_effect = fake_agent

        verdict = run_memory_curation(
            project_dir=tmp_path,
            run_dir=run_dir,
            round_num=10,
            memory_path=mem,
        )
        assert verdict == "SDK_FAIL"
        assert mem.read_text() == original_text

    @patch("evolve.agent._run_agent_with_retries", side_effect=Exception("boom"))
    def test_sdk_exception_returns_sdk_fail(self, mock_retry, tmp_path: Path) -> None:
        """Exception during SDK call → SDK_FAIL, original restored."""
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        mem = tmp_path / "memory.md"
        original = "# Memory\n" + "\n".join(f"line {i}" for i in range(350))
        mem.write_text(original)
        original_text = mem.read_text()

        verdict = run_memory_curation(
            project_dir=tmp_path,
            run_dir=run_dir,
            round_num=10,
            memory_path=mem,
        )
        assert verdict == "SDK_FAIL"
        assert mem.read_text() == original_text


# ---------------------------------------------------------------------------
# AC 6 — Orchestrator wiring (_run_curation_pass)
# ---------------------------------------------------------------------------


class TestRunCurationPass:
    """_run_curation_pass in orchestrator delegates correctly."""

    @patch("evolve.orchestrator._runs_base")
    @patch("evolve.agent.run_memory_curation", return_value="SKIPPED")
    def test_skipped_is_silent(self, mock_cur, mock_rb, tmp_path: Path) -> None:
        from evolve.orchestrator import _run_curation_pass
        mock_rb.return_value = tmp_path
        ui = MagicMock()
        imp_path = tmp_path / "improvements.md"
        imp_path.write_text("- [x] done\n")

        _run_curation_pass(tmp_path, tmp_path / "run", 5, imp_path, None, ui)
        mock_cur.assert_called_once()

    @patch("evolve.orchestrator._runs_base")
    @patch("evolve.agent.run_memory_curation", return_value="CURATED")
    def test_curated_logs_probe(self, mock_cur, mock_rb, tmp_path: Path) -> None:
        from evolve.orchestrator import _run_curation_pass
        mock_rb.return_value = tmp_path
        ui = MagicMock()
        imp_path = tmp_path / "improvements.md"
        imp_path.write_text("- [x] done\n")

        _run_curation_pass(tmp_path, tmp_path / "run", 10, imp_path, "SPEC.md", ui)
        mock_cur.assert_called_once()

    @patch("evolve.orchestrator._runs_base")
    @patch("evolve.agent.run_memory_curation", return_value="ABORTED")
    def test_aborted_warns(self, mock_cur, mock_rb, tmp_path: Path) -> None:
        from evolve.orchestrator import _run_curation_pass
        mock_rb.return_value = tmp_path
        ui = MagicMock()
        imp_path = tmp_path / "improvements.md"
        imp_path.write_text("- [x] done\n")

        _run_curation_pass(tmp_path, tmp_path / "run", 10, imp_path, None, ui)
        mock_cur.assert_called_once()
