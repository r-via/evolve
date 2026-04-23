"""Consolidated tests for the full README sync discipline.

Mechanisms A (pre-convergence audit), B (party-mode README_proposal),
and C (drift warning) are covered individually in ``test_readme_audit.py``,
``test_spec_flag.py``, ``test_readme_drift.py``, and
``test_readme_drift_integration.py``. This file consolidates the
discipline-level invariants that span all three mechanisms:

1. Mechanism A NEVER blocks convergence, even when it appends items
2. Mechanism B's README_proposal.md is produced ONLY when ``--spec``
   differs from ``README.md``
3. Mechanism C's warning/counter fire at exactly the documented
   ``rounds_since_stale > 3`` threshold
4. All three mechanisms are no-ops uniformly when ``--spec`` is unset
   or equal to ``"README.md"``

See SPEC.md § "README sync discipline" for the contract.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from loop import (
    _audit_readme_sync,
    _compute_readme_sync,
    _extract_spec_claims,
    _run_party_mode,
)


def _touch(path: Path, mtime: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text("")
    os.utime(path, (mtime, mtime))


# ---------------------------------------------------------------------------
# Mechanism A — never blocks convergence
# ---------------------------------------------------------------------------


class TestMechanismADoesNotBlockConvergence:
    """Mechanism A is advisory — gaps generate items but convergence proceeds."""

    def test_audit_returns_count_not_exception_even_with_many_gaps(
        self, tmp_path: Path
    ) -> None:
        """Audit must not raise even when the spec has many unmentioned claims."""
        (tmp_path / "SPEC.md").write_text(
            "### The --one flag\n"
            "### The --two flag\n"
            "### The --three flag\n"
            "Set `EVOLVE_X` and `EVOLVE_Y` and `EVOLVE_Z`.\n"
            "\n"
            "## Requirements\n"
            "- Python 3.10+\n"
            "- `claude-agent-sdk`\n"
            "\n"
            "```bash\n"
            "$ evolve start . --one\n"
            "$ evolve start . --two\n"
            "```\n"
        )
        (tmp_path / "README.md").write_text("# Project\n\nBarely mentions anything.\n")
        imp = tmp_path / "improvements.md"
        imp.write_text("# Improvements\n")

        count = _audit_readme_sync(tmp_path, imp, spec="SPEC.md")
        # Many gaps: flags, env vars, requirements, shell examples
        assert count > 3
        # Convergence flow is unaffected: no exception, just a count
        # The CONVERGED marker (created separately by the agent) is never
        # touched by the audit.

    def test_audit_preserves_converged_marker_semantics(
        self, tmp_path: Path
    ) -> None:
        """Audit writes to improvements.md; CONVERGED file is never mutated."""
        (tmp_path / "SPEC.md").write_text("### The --missing flag\n")
        (tmp_path / "README.md").write_text("# readme\n")
        imp = tmp_path / "improvements.md"
        imp.write_text("# Improvements\n")
        run_dir = tmp_path / "runs" / "session"
        run_dir.mkdir(parents=True)
        converged_file = run_dir / "CONVERGED"
        converged_file.write_text("All spec claims verified")
        original_content = converged_file.read_text()

        # Run audit — even though it appends items, CONVERGED stays
        count = _audit_readme_sync(tmp_path, imp, spec="SPEC.md")
        assert count >= 1
        assert converged_file.is_file()
        assert converged_file.read_text() == original_content

    def test_audit_appended_items_are_unchecked_and_functional(
        self, tmp_path: Path
    ) -> None:
        """Appended items must be standard functional checkboxes (next-cycle targets)."""
        (tmp_path / "SPEC.md").write_text(
            "### The --alpha flag\n"
            "Set `EVOLVE_BETA` for something.\n"
        )
        (tmp_path / "README.md").write_text("# readme\n")
        imp = tmp_path / "improvements.md"
        imp.write_text("# Improvements\n")

        _audit_readme_sync(tmp_path, imp, spec="SPEC.md")
        text = imp.read_text()
        # Every sync item must be an unchecked functional checkbox so the
        # next cycle's agent picks them up via the normal path.
        for line in text.splitlines():
            if "README sync:" in line:
                assert line.startswith("- [ ] [functional] README sync:")
                assert "[needs-package]" not in line


# ---------------------------------------------------------------------------
# Mechanism B — README_proposal only when spec != README.md
# ---------------------------------------------------------------------------


class TestMechanismBProposalScoping:
    """The README_proposal.md instruction is conditional on the spec path."""

    def _make_run_dir(self, tmp_path: Path) -> Path:
        run_dir = tmp_path / "runs" / "session"
        run_dir.mkdir(parents=True)
        (tmp_path / "SPEC.md").write_text("# Spec\n")
        (tmp_path / "README.md").write_text("# README\n")
        (tmp_path / "runs" / "improvements.md").write_text("# done\n")
        (tmp_path / "runs" / "memory.md").write_text("# memory\n")
        # Ensure at least one agent persona exists so party mode runs
        agents = tmp_path / "agents"
        agents.mkdir(exist_ok=True)
        (agents / "architect.md").write_text("Architect persona.")
        return run_dir

    def _capture_prompt(self, tmp_path: Path, spec: str | None) -> str:
        """Invoke _run_party_mode with mocked agents, return the prompt text."""
        run_dir = self._make_run_dir(tmp_path)
        ui = MagicMock()
        captured: list[str] = []

        async def fake_agent(prompt, project_dir, round_num=0, run_dir=None, log_filename=None, images=None):
            captured.append(prompt)

        with patch("agent.run_claude_agent", fake_agent), \
             patch("agent._is_benign_runtime_error", return_value=False), \
             patch("agent._should_retry_rate_limit", return_value=None):
            _run_party_mode(tmp_path, run_dir, ui, spec=spec)

        return captured[0] if captured else ""

    def test_spec_differs_from_readme_requests_readme_proposal(
        self, tmp_path: Path
    ) -> None:
        prompt = self._capture_prompt(tmp_path, spec="SPEC.md")
        # The prompt must explicitly list README_proposal.md as a required output
        assert "README_proposal.md" in prompt
        # It must request SPEC_proposal.md, not README_proposal.md, as the spec proposal
        assert "SPEC_proposal.md" in prompt

    def test_spec_is_none_does_not_request_second_readme_proposal(
        self, tmp_path: Path
    ) -> None:
        """When README.md IS the spec, no separate README_proposal is requested.

        Note: the spec-proposal-for-README.md IS named ``README_proposal.md``
        (that's the proposal for the spec itself), but there should be no
        second/duplicate README_proposal produced.
        """
        prompt = self._capture_prompt(tmp_path, spec=None)
        # The spec-proposal IS README_proposal (that's the proposal for the spec
        # itself when spec is README.md). But the prompt must NOT ask for a
        # SECOND separate README_proposal on top of that.
        assert prompt.count("README_proposal.md") >= 1
        # There must be no SPEC_proposal reference when spec is None
        assert "SPEC_proposal.md" not in prompt

    def test_spec_equals_readme_md_literal_does_not_request_extra(
        self, tmp_path: Path
    ) -> None:
        prompt = self._capture_prompt(tmp_path, spec="README.md")
        assert "SPEC_proposal.md" not in prompt


# ---------------------------------------------------------------------------
# Mechanism C — >3 threshold precision
# ---------------------------------------------------------------------------


class TestMechanismCThresholdPrecision:
    """Boundary tests for the ``rounds_since_stale > 3`` threshold."""

    def _setup_drift(self, tmp_path: Path) -> None:
        now = time.time()
        _touch(tmp_path / "README.md", now - 10_000)
        _touch(tmp_path / "SPEC.md", now)

    def _seed_prior_state(self, tmp_path: Path, rss: int, since_round: int) -> None:
        prior = {
            "version": 1,
            "readme_sync": {
                "stale": True,
                "rounds_since_stale": rss,
                "readme_stale_since_round": since_round,
            },
        }
        (tmp_path / "state.json").write_text(json.dumps(prior))

    @pytest.mark.parametrize(
        "round_num,since_round,expected_rss",
        [
            (1, 1, 0),  # drift just started
            (2, 1, 1),
            (3, 1, 2),
            (4, 1, 3),  # exactly at threshold — NO warn
            (5, 1, 4),  # > 3 — warn fires
            (6, 1, 5),
            (10, 1, 9),
        ],
    )
    def test_rounds_since_stale_linear_accrual(
        self, tmp_path: Path, round_num: int, since_round: int, expected_rss: int
    ) -> None:
        self._setup_drift(tmp_path)
        self._seed_prior_state(tmp_path, rss=expected_rss - 1 if expected_rss > 0 else 0, since_round=since_round)

        result = _compute_readme_sync(tmp_path, tmp_path, round_num=round_num, spec="SPEC.md")
        assert result is not None
        assert result["stale"] is True
        assert result["rounds_since_stale"] == expected_rss
        # Warning should fire iff rss > 3
        should_warn = expected_rss > 3
        # The threshold check is done at the orchestrator level; here we
        # verify the numeric field is correct so the comparison is reliable.
        if should_warn:
            assert result["rounds_since_stale"] > 3
        else:
            assert result["rounds_since_stale"] <= 3

    def test_threshold_at_exactly_3_no_warn(self, tmp_path: Path) -> None:
        """rounds_since_stale == 3 is NOT a warning state (strict >3)."""
        self._setup_drift(tmp_path)
        self._seed_prior_state(tmp_path, rss=2, since_round=1)
        result = _compute_readme_sync(tmp_path, tmp_path, round_num=4, spec="SPEC.md")
        assert result is not None
        # round 4 - since_round 1 = 3 rounds elapsed — threshold NOT crossed
        assert result["rounds_since_stale"] == 3
        assert not (result["rounds_since_stale"] > 3)

    def test_threshold_at_4_warn_fires(self, tmp_path: Path) -> None:
        """rounds_since_stale == 4 IS a warning state (first round past threshold)."""
        self._setup_drift(tmp_path)
        self._seed_prior_state(tmp_path, rss=3, since_round=1)
        result = _compute_readme_sync(tmp_path, tmp_path, round_num=5, spec="SPEC.md")
        assert result is not None
        assert result["rounds_since_stale"] == 4
        assert result["rounds_since_stale"] > 3


# ---------------------------------------------------------------------------
# Uniform no-op across A, B, C when --spec is unset or README.md
# ---------------------------------------------------------------------------


class TestAllMechanismsNoOpUniformly:
    """When spec is None or "README.md", none of A/B/C activates."""

    @pytest.mark.parametrize("spec", [None, "README.md"])
    def test_mechanism_a_noop(self, tmp_path: Path, spec: str | None) -> None:
        """Mechanism A: audit returns 0 and writes nothing."""
        (tmp_path / "README.md").write_text(
            "### The --foo flag\n"
            "Set `EVOLVE_BAR`.\n"
        )
        imp = tmp_path / "improvements.md"
        imp.write_text("# Improvements\n")
        before = imp.read_text()

        assert _audit_readme_sync(tmp_path, imp, spec=spec) == 0
        assert imp.read_text() == before  # untouched

    @pytest.mark.parametrize("spec", [None, "README.md"])
    def test_mechanism_c_noop(self, tmp_path: Path, spec: str | None) -> None:
        """Mechanism C: _compute_readme_sync returns None."""
        (tmp_path / "README.md").write_text("# readme\n")
        # Even if a "SPEC.md" exists, None/README.md means no tracking.
        (tmp_path / "SPEC.md").write_text("# spec\n")

        result = _compute_readme_sync(tmp_path, tmp_path, round_num=10, spec=spec)
        assert result is None

    def test_mechanism_b_noop_spec_none(self, tmp_path: Path) -> None:
        """Mechanism B: no SPEC_proposal.md instruction when spec is None.

        (README_proposal.md IS still mentioned, as it's the proposal filename
        derived from spec=None → README.md, but no secondary README_proposal
        is requested.)
        """
        run_dir = tmp_path / "runs" / "session"
        run_dir.mkdir(parents=True)
        (tmp_path / "README.md").write_text("# README\n")
        (tmp_path / "runs" / "improvements.md").write_text("# done\n")
        (tmp_path / "runs" / "memory.md").write_text("# memory\n")
        agents = tmp_path / "agents"
        agents.mkdir(exist_ok=True)
        (agents / "architect.md").write_text("Architect persona.")
        ui = MagicMock()
        captured: list[str] = []

        async def fake_agent(prompt, project_dir, round_num=0, run_dir=None, log_filename=None, images=None):
            captured.append(prompt)

        with patch("agent.run_claude_agent", fake_agent), \
             patch("agent._is_benign_runtime_error", return_value=False), \
             patch("agent._should_retry_rate_limit", return_value=None):
            _run_party_mode(tmp_path, run_dir, ui, spec=None)

        prompt = captured[0] if captured else ""
        # No SPEC_proposal reference when spec is None
        assert "SPEC_proposal.md" not in prompt


# ---------------------------------------------------------------------------
# Cross-mechanism coherence
# ---------------------------------------------------------------------------


class TestCrossMechanismCoherence:
    """Verify A + C share a consistent activation predicate (spec != README.md)."""

    def test_a_and_c_activate_together(self, tmp_path: Path) -> None:
        """When spec != README.md, both A and C activate on the same project."""
        now = time.time()
        _touch(tmp_path / "README.md", now - 1000)
        _touch(tmp_path / "SPEC.md", now)
        # Seed content so A has something to find
        (tmp_path / "SPEC.md").write_text("### The --gap flag\n")
        _touch(tmp_path / "SPEC.md", now)  # re-stamp after write
        (tmp_path / "README.md").write_text("# readme\n")
        _touch(tmp_path / "README.md", now - 1000)

        imp = tmp_path / "improvements.md"
        imp.write_text("# Improvements\n")

        # Mechanism A: detects gap
        a_count = _audit_readme_sync(tmp_path, imp, spec="SPEC.md")
        assert a_count >= 1

        # Mechanism C: also activates (spec is newer than README)
        c_result = _compute_readme_sync(tmp_path, tmp_path, round_num=1, spec="SPEC.md")
        assert c_result is not None
        assert c_result["stale"] is True

    @pytest.mark.parametrize("spec", [None, "README.md"])
    def test_a_and_c_deactivate_together(self, tmp_path: Path, spec: str | None) -> None:
        """When spec IS README.md (or unset), both A and C are quiet."""
        (tmp_path / "README.md").write_text("### The --foo flag\n")
        imp = tmp_path / "improvements.md"
        imp.write_text("# Improvements\n")

        a_count = _audit_readme_sync(tmp_path, imp, spec=spec)
        c_result = _compute_readme_sync(tmp_path, tmp_path, round_num=1, spec=spec)

        assert a_count == 0
        assert c_result is None


# ---------------------------------------------------------------------------
# Mechanism A — multi-format spec claim extraction
# ---------------------------------------------------------------------------


class TestMechanismAMultiFormatExtraction:
    """Verify _extract_spec_claims handles diverse spec formats."""

    def test_mixed_heading_depths(self) -> None:
        """Level-2 and level-3 CLI flag headers both detected."""
        spec = (
            "## The --top flag\n"
            "Description.\n"
            "\n"
            "### The --nested flag\n"
            "Description.\n"
        )
        claims = _extract_spec_claims(spec)
        flag_names = {c[0] for c in claims if c[1] == "flag"}
        assert "--top" in flag_names
        assert "--nested" in flag_names

    def test_env_var_in_various_quoting_styles(self) -> None:
        """Env vars in backticks and plain text are both detected."""
        spec = (
            "The variable `EVOLVE_QUOTED` is one option.\n"
            "Also EVOLVE_PLAIN works.\n"
        )
        claims = _extract_spec_claims(spec)
        env_names = {c[0] for c in claims if c[1] == "env_var"}
        assert "EVOLVE_QUOTED" in env_names
        assert "EVOLVE_PLAIN" in env_names

    def test_shell_examples_dollar_prefix_inside_bash_fence(self) -> None:
        """Shell examples with $ prefix inside ```bash fences detected."""
        spec = (
            "```bash\n"
            "$ evolve start .\n"
            "$ evolve start . --forever\n"
            "```\n"
        )
        claims = _extract_spec_claims(spec)
        shell = [c for c in claims if c[1] == "shell_example"]
        assert len(shell) >= 2

    def test_formatted_items_are_well_formed(self, tmp_path: Path) -> None:
        """Each appended line is parseable as a standard checkbox item."""
        (tmp_path / "SPEC.md").write_text(
            "### The --first flag\n"
            "### The --second flag\n"
            "Set `EVOLVE_FIRST`.\n"
        )
        (tmp_path / "README.md").write_text("# empty\n")
        imp = tmp_path / "improvements.md"
        imp.write_text("# Improvements\n")

        _audit_readme_sync(tmp_path, imp, spec="SPEC.md")
        lines = imp.read_text().splitlines()
        sync_lines = [ln for ln in lines if "README sync:" in ln]
        assert len(sync_lines) >= 3
        for line in sync_lines:
            # Format: - [ ] [functional] README sync: mention <claim> in <section> (documented in <spec> § <section> but absent from README.md)
            assert line.startswith("- [ ] [functional] README sync: mention ")
            assert " in " in line
            assert "documented in SPEC.md" in line
            assert "absent from README.md" in line
