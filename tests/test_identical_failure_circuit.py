"""Identical-failure circuit breaker — SPEC.md § "Circuit breakers".

``--forever`` mode is vulnerable to deterministic failures (e.g. a
pre-check command that hangs on every round, an irrecoverable bug that
produces the same stack trace every time).  Without a circuit breaker
the loop would spin forever, burning tokens without recovery.

The circuit breaker detects ``MAX_IDENTICAL_FAILURES`` consecutive
rounds with the same failure signature and exits with code 4, letting
an outer supervisor (systemd, ``while true; do ...``, tmux loop)
distinguish "one round failed" (exit 2) from "stuck deterministically"
(exit 4) and react accordingly.
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from loop import (
    MAX_IDENTICAL_FAILURES,
    _failure_signature,
    _is_circuit_breaker_tripped,
    _run_rounds,
)


class TestFailureSignature:
    """Fingerprint semantics — identical inputs hash identically, different
    inputs (kind/returncode/tail) hash differently, and the 500-byte tail
    window lets varying prefixes still match."""

    def test_identical_attempts_produce_identical_signatures(self):
        s1 = _failure_signature("stalled", -9, "pytest collecting...")
        s2 = _failure_signature("stalled", -9, "pytest collecting...")
        assert s1 == s2

    def test_different_kinds_produce_different_signatures(self):
        assert _failure_signature("stalled", -9, "x") != _failure_signature(
            "crashed", -9, "x"
        )

    def test_different_returncodes_produce_different_signatures(self):
        assert _failure_signature("crashed", 1, "x") != _failure_signature(
            "crashed", 2, "x"
        )

    def test_different_output_tails_produce_different_signatures(self):
        assert _failure_signature("stalled", -9, "A" * 1000) != _failure_signature(
            "stalled", -9, "B" * 1000
        )

    def test_prefix_changes_ignored_when_tail_identical(self):
        # Only the trailing 500 bytes are fingerprinted.  Two outputs longer
        # than 500 bytes with the same terminal 500 bytes must match.
        common_tail = "x" * 500
        s1 = _failure_signature("stalled", -9, "prefix A" * 100 + common_tail)
        s2 = _failure_signature("stalled", -9, "totally different" * 100 + common_tail)
        assert s1 == s2

    def test_signature_is_short_hex(self):
        sig = _failure_signature("crashed", 1, "boom")
        assert len(sig) == 16
        int(sig, 16)  # raises ValueError if not hex


class TestCircuitBreakerPredicate:
    """``_is_circuit_breaker_tripped`` — the threshold test.

    Pure helper, trivially unit-testable, covers all the interesting
    boundary cases independently of ``_run_rounds`` wiring.
    """

    def test_empty_list_not_tripped(self):
        assert _is_circuit_breaker_tripped([]) is False

    def test_below_threshold_not_tripped(self):
        assert _is_circuit_breaker_tripped(["a"] * (MAX_IDENTICAL_FAILURES - 1)) is False

    def test_at_threshold_identical_trips(self):
        assert _is_circuit_breaker_tripped(["a"] * MAX_IDENTICAL_FAILURES) is True

    def test_at_threshold_mixed_not_tripped(self):
        sigs = ["a", "b"] + ["a"] * (MAX_IDENTICAL_FAILURES - 2)
        assert _is_circuit_breaker_tripped(sigs) is False

    def test_trailing_identical_trips_even_with_older_noise(self):
        # Old "a" entries, then MAX_IDENTICAL_FAILURES of "b" at the tail.
        sigs = ["a", "x", "y"] + ["b"] * MAX_IDENTICAL_FAILURES
        assert _is_circuit_breaker_tripped(sigs) is True

    def test_older_identical_but_different_tail_not_tripped(self):
        # Same signature far back, interrupted by a different recent one.
        sigs = ["a"] * MAX_IDENTICAL_FAILURES + ["b"]
        assert _is_circuit_breaker_tripped(sigs) is False


class TestCircuitBreakerIntegration:
    """End-to-end: ``_run_rounds`` exits with code 4 when the threshold trips."""

    def setup_method(self):
        self.ui = MagicMock()

    def _setup_project(self, tmp_path: Path):
        project_dir = tmp_path / "proj"
        project_dir.mkdir()
        run_dir = project_dir / "runs" / "session"
        run_dir.mkdir(parents=True)
        imp_path = project_dir / "runs" / "improvements.md"
        imp_path.write_text("- [ ] [functional] do something\n")
        return project_dir, run_dir, imp_path

    def test_forever_identical_stalls_exit_4(self, tmp_path: Path):
        """Forever mode: N rounds stalling identically → exit 4, not infinite loop."""
        project_dir, run_dir, imp_path = self._setup_project(tmp_path)

        def always_stall(cmd, cwd, ui_, round_num, watchdog_timeout=120):
            return 0, "deterministic pytest hang\npytest collecting...", True

        with patch("loop._run_monitored_subprocess", side_effect=always_stall), \
             patch("loop._save_subprocess_diagnostic"), \
             patch("loop._generate_evolution_report"), \
             patch("loop._run_party_mode"), \
             patch("loop._forever_restart"), \
             pytest.raises(SystemExit) as exc:
            _run_rounds(
                project_dir, run_dir, imp_path, self.ui,
                start_round=1, max_rounds=20, check_cmd="pytest",
                allow_installs=False, timeout=300, model="claude-opus-4-6",
                forever=True,
            )
        assert exc.value.code == 4

    def test_non_forever_preserves_exit_2(self, tmp_path: Path):
        """Non-forever exits 2 on first round's retries exhaustion — the
        circuit breaker never reaches its 3-rounds threshold because the
        loop terminates after one failed round.
        """
        project_dir, run_dir, imp_path = self._setup_project(tmp_path)

        def always_stall(cmd, cwd, ui_, round_num, watchdog_timeout=120):
            return 0, "stall", True

        with patch("loop._run_monitored_subprocess", side_effect=always_stall), \
             patch("loop._save_subprocess_diagnostic"), \
             patch("loop._generate_evolution_report"), \
             pytest.raises(SystemExit) as exc:
            _run_rounds(
                project_dir, run_dir, imp_path, self.ui,
                start_round=1, max_rounds=1, check_cmd="pytest",
                allow_installs=False, timeout=300, model="claude-opus-4-6",
                forever=False,
            )
        assert exc.value.code == 2

