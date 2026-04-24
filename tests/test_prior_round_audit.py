"""Tests for the prior-round audit — SPEC § "Prior round audit".

Every round (≥ 2) runs a pre-flight scan of the previous round's
artifacts for anomaly signals.  When any are detected, the agent is
instructed (via an injected ``## Prior round audit`` section) to
investigate and fix before touching the current backlog target.

This test file covers the detector (``_detect_prior_round_anomalies``)
and the prompt renderer (section shape + presence/absence conditions).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agent import _detect_prior_round_anomalies, build_prompt


def _setup_run_dir(tmp_path: Path) -> Path:
    (tmp_path / "README.md").write_text("# Test project\n")
    (tmp_path / "runs").mkdir()
    run_dir = tmp_path / "runs" / "session"
    run_dir.mkdir()
    return run_dir


class TestDetectPriorRoundAnomalies:
    """Pure unit tests for the signal detector."""

    def test_round_1_returns_empty(self, tmp_path: Path):
        """Round 1 has no prior round; detector returns []."""
        run_dir = _setup_run_dir(tmp_path)
        assert _detect_prior_round_anomalies(run_dir, round_num=1) == []

    def test_no_run_dir_returns_empty(self):
        """No run_dir → []."""
        assert _detect_prior_round_anomalies(None, round_num=5) == []

    def test_clean_prior_round_returns_empty(self, tmp_path: Path):
        """Prior round with no anomaly artifacts returns []."""
        run_dir = _setup_run_dir(tmp_path)
        (run_dir / "conversation_loop_4.md").write_text(
            "## Round 4 — clean pass\n- ran pytest, all PASS\n- committed\n"
        )
        (run_dir / "check_round_4.txt").write_text(
            "Round 4 post-fix check: PASS\n"
        )
        assert _detect_prior_round_anomalies(run_dir, round_num=5) == []

    def test_orchestrator_diagnostic_detected(self, tmp_path: Path):
        """``subprocess_error_round_{N-1}.txt`` exists → flagged."""
        run_dir = _setup_run_dir(tmp_path)
        (run_dir / "subprocess_error_round_4.txt").write_text("crash diag")
        a = _detect_prior_round_anomalies(run_dir, round_num=5)
        assert "orchestrator diagnostic present" in a

    def test_post_fix_check_fail_detected(self, tmp_path: Path):
        """``post-fix check: FAIL`` in check file → flagged."""
        run_dir = _setup_run_dir(tmp_path)
        (run_dir / "check_round_4.txt").write_text(
            "Round 4 post-fix check: FAIL\nCommand: pytest\n"
        )
        a = _detect_prior_round_anomalies(run_dir, round_num=5)
        assert "post-fix check FAIL" in a

    def test_post_fix_check_pass_not_flagged(self, tmp_path: Path):
        """PASS check is not flagged."""
        run_dir = _setup_run_dir(tmp_path)
        (run_dir / "check_round_4.txt").write_text(
            "Round 4 post-fix check: PASS\n"
        )
        a = _detect_prior_round_anomalies(run_dir, round_num=5)
        assert "post-fix check FAIL" not in a

    def test_watchdog_stall_detected(self, tmp_path: Path):
        """Watchdog ``stalled (Ns without output) — killing subprocess``
        in the prior conversation log → flagged."""
        run_dir = _setup_run_dir(tmp_path)
        (run_dir / "conversation_loop_4.md").write_text(
            "...\nWARN: Round 4 stalled (120s without output) — killing subprocess\n"
        )
        a = _detect_prior_round_anomalies(run_dir, round_num=5)
        assert "watchdog stall / SIGKILL" in a

    def test_signal_kill_detected(self, tmp_path: Path):
        """``Round N failed (exit -K)`` → flagged."""
        run_dir = _setup_run_dir(tmp_path)
        (run_dir / "conversation_loop_4.md").write_text("Round 4 failed (exit -9)")
        a = _detect_prior_round_anomalies(run_dir, round_num=5)
        assert "subprocess killed by signal" in a

    def test_precheck_timeout_detected(self, tmp_path: Path):
        """``pre-check TIMEOUT after Ns`` → flagged."""
        run_dir = _setup_run_dir(tmp_path)
        (run_dir / "conversation_loop_4.md").write_text(
            "[probe] pre-check TIMEOUT after 300s"
        )
        a = _detect_prior_round_anomalies(run_dir, round_num=5)
        assert "pre-check TIMEOUT" in a

    def test_frame_capture_error_detected(self, tmp_path: Path):
        """``Frame capture failed … not well-formed`` → flagged."""
        run_dir = _setup_run_dir(tmp_path)
        (run_dir / "conversation_loop_4.md").write_text(
            "Frame capture failed for error_round_4: not well-formed (invalid token)"
        )
        a = _detect_prior_round_anomalies(run_dir, round_num=5)
        assert "frame capture error" in a

    def test_circuit_breaker_detected(self, tmp_path: Path):
        """Exit 4 announce line → flagged."""
        run_dir = _setup_run_dir(tmp_path)
        (run_dir / "conversation_loop_4.md").write_text(
            "Same failure signature 3 attempts in a row — deterministic loop detected"
        )
        a = _detect_prior_round_anomalies(run_dir, round_num=5)
        assert "circuit breaker tripped (exit 4)" in a

    def test_multiple_signals_aggregated(self, tmp_path: Path):
        """Two or more signals in the same prior round are all reported."""
        run_dir = _setup_run_dir(tmp_path)
        (run_dir / "subprocess_error_round_4.txt").write_text("diag")
        (run_dir / "check_round_4.txt").write_text(
            "Round 4 post-fix check: FAIL\n"
        )
        (run_dir / "conversation_loop_4.md").write_text(
            "Round 4 stalled (120s without output) — killing subprocess\n"
            "Round 4 failed (exit -9)\n"
            "Frame capture failed for error_round_4: not well-formed\n"
        )
        a = _detect_prior_round_anomalies(run_dir, round_num=5)
        assert len(a) >= 5


class TestBuildPromptPriorRoundAuditSection:
    """Integration: the ``## Prior round audit`` section is emitted when
    anomalies are detected and omitted otherwise.
    """

    def test_section_present_when_anomaly_detected(self, tmp_path: Path):
        run_dir = _setup_run_dir(tmp_path)
        # Seed a stall in the prior round's conversation log.
        (run_dir / "conversation_loop_4.md").write_text(
            "Round 4 stalled (120s without output) — killing subprocess\n"
        )
        prompt = build_prompt(
            tmp_path,
            check_output="",
            check_cmd=None,
            allow_installs=False,
            run_dir=run_dir,
            round_num=5,
        )
        # Match the rendered section header (with "— Round N" suffix),
        # distinct from the literal string "## Prior round audit" that
        # appears in the system.md Step 1.5 documentation text.
        assert "## Prior round audit — Round 4" in prompt
        assert "watchdog stall / SIGKILL" in prompt
        assert "fix(audit):" in prompt

    def test_section_omitted_when_prior_clean(self, tmp_path: Path):
        run_dir = _setup_run_dir(tmp_path)
        (run_dir / "conversation_loop_4.md").write_text(
            "Round 4 — clean pass, all tests green\n"
        )
        prompt = build_prompt(
            tmp_path,
            check_output="",
            check_cmd=None,
            allow_installs=False,
            run_dir=run_dir,
            round_num=5,
        )
        # No rendered section — only the instructional mention in system.md
        # would still contain the literal string, so match the rendered
        # form (header with "— Round N" suffix).
        assert "## Prior round audit — Round" not in prompt

    def test_section_omitted_on_round_1(self, tmp_path: Path):
        run_dir = _setup_run_dir(tmp_path)
        prompt = build_prompt(
            tmp_path,
            check_output="",
            check_cmd=None,
            allow_installs=False,
            run_dir=run_dir,
            round_num=1,
        )
        # No rendered section — only the instructional mention in system.md
        # would still contain the literal string, so match the rendered
        # form (header with "— Round N" suffix).
        assert "## Prior round audit — Round" not in prompt
