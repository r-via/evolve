"""Tests for US-058: LAYERING VIOLATION diagnostic in orchestrator and build_prompt.

Covers:
  1. _detect_layering_violation detects synthetic violating imports
  2. _detect_layering_violation no-op with clean DDD files
  3. build_prev_crash_section renders LAYERING VIOLATION diagnostic header
  4. Integration: round_success._handle_round_success calls the detector
"""

import importlib
import textwrap
from pathlib import Path
from unittest.mock import MagicMock, patch

from evolve.infrastructure.diagnostics.detector import _detect_layering_violation
from evolve.infrastructure.claude_sdk.prompt_diagnostics import build_prev_crash_section


class TestDetectLayeringViolation:
    """Tests for _detect_layering_violation in evolve/diagnostics.py."""

    def test_detects_inward_violating_import(self, tmp_path):
        """Domain file importing from evolve.application.run_loop is a violation."""
        evolve_dir = tmp_path / "evolve"
        domain_dir = evolve_dir / "domain"
        domain_dir.mkdir(parents=True)
        (domain_dir / "__init__.py").write_text("")
        (domain_dir / "bad.py").write_text(
            "from evolve.application.run_loop import something\n"
        )
        violations = _detect_layering_violation(tmp_path)
        assert len(violations) == 1
        f, mod, src, tgt = violations[0]
        assert "bad.py" in f
        assert mod == "evolve.application.run_loop"
        assert src == "domain"
        assert tgt == "application"

    def test_application_importing_infrastructure_allowed_during_migration(self, tmp_path):
        """Application → infrastructure is allowed during DDD migration carve-out."""
        evolve_dir = tmp_path / "evolve"
        app_dir = evolve_dir / "application"
        app_dir.mkdir(parents=True)
        (app_dir / "__init__.py").write_text("")
        (app_dir / "ok.py").write_text(
            "from evolve.infrastructure.git import something\n"
        )
        violations = _detect_layering_violation(tmp_path)
        assert violations == []

    def test_detects_infrastructure_importing_application(self, tmp_path):
        """Infrastructure importing from application is still a violation."""
        evolve_dir = tmp_path / "evolve"
        infra_dir = evolve_dir / "infrastructure"
        infra_dir.mkdir(parents=True)
        (infra_dir / "__init__.py").write_text("")
        (infra_dir / "bad.py").write_text(
            "from evolve.application.run_loop import something\n"
        )
        violations = _detect_layering_violation(tmp_path)
        assert len(violations) == 1
        _, _, src, tgt = violations[0]
        assert src == "infrastructure"
        assert tgt == "application"

    def test_clean_ddd_files_no_violations(self, tmp_path):
        """Clean domain files produce no violations."""
        evolve_dir = tmp_path / "evolve"
        domain_dir = evolve_dir / "domain"
        domain_dir.mkdir(parents=True)
        (domain_dir / "__init__.py").write_text("")
        (domain_dir / "clean.py").write_text(
            "from dataclasses import dataclass\n"
            "from enum import Enum\n"
        )
        app_dir = evolve_dir / "application"
        app_dir.mkdir(parents=True)
        (app_dir / "__init__.py").write_text("")
        (app_dir / "clean.py").write_text(
            "from evolve.domain.round import RoundKind\n"
        )
        violations = _detect_layering_violation(tmp_path)
        assert violations == []

    def test_legacy_files_not_checked(self, tmp_path):
        """Files in evolve/ root (legacy) are not checked."""
        evolve_dir = tmp_path / "evolve"
        evolve_dir.mkdir(parents=True)
        (evolve_dir / "__init__.py").write_text("")
        (evolve_dir / "agent.py").write_text(
            "from evolve.domain.round import RoundKind\n"
            "from evolve.infrastructure.git import something\n"
        )
        violations = _detect_layering_violation(tmp_path)
        assert violations == []

    def test_no_evolve_dir_returns_empty(self, tmp_path):
        """Missing evolve/ dir returns empty list."""
        violations = _detect_layering_violation(tmp_path)
        assert violations == []

    def test_interfaces_allowed_all_layers(self, tmp_path):
        """Interfaces layer can import from application, domain, infrastructure."""
        evolve_dir = tmp_path / "evolve"
        iface_dir = evolve_dir / "interfaces"
        iface_dir.mkdir(parents=True)
        (iface_dir / "__init__.py").write_text("")
        (iface_dir / "ok.py").write_text(
            "from evolve.application.run_round import run_round\n"
            "from evolve.domain.round import RoundKind\n"
            "from evolve.infrastructure.git import something\n"
        )
        violations = _detect_layering_violation(tmp_path)
        assert violations == []


class TestBuildPrevCrashSection:
    """Tests for LAYERING VIOLATION prefix in build_prev_crash_section."""

    def test_renders_layering_violation_header(self):
        diag = "LAYERING VIOLATION: 1 inward-violating edge(s):\n  - domain/bad.py imports evolve.application.run_loop"
        result = build_prev_crash_section(diag)
        assert "## CRITICAL — DDD layering violation" in result
        assert "LAYERING VIOLATION" in result
        assert "domain/bad.py" in result

    def test_does_not_match_other_prefixes(self):
        result = build_prev_crash_section("TDD VIOLATION: something")
        assert "DDD layering" not in result
        assert "TDD violation" in result


class TestRoundSuccessIntegration:
    """Test that _handle_round_success calls _detect_layering_violation."""

    def test_layering_violation_writes_diagnostic(self, tmp_path):
        """When _detect_layering_violation returns violations,
        _save_subprocess_diagnostic is called with LAYERING VIOLATION prefix."""
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        imp_path = tmp_path / "improvements.md"
        imp_path.write_text("# Improvements\n")

        from evolve.application.run_loop_lifecycle import _handle_round_success

        violations = [("domain/bad.py", "evolve.infrastructure.claude_sdk.runtime.", "domain", "legacy")]
        mock_imports = {
            "_detect_file_too_large": MagicMock(return_value=[]),
            "_detect_layering_violation": MagicMock(return_value=violations),
            "_detect_tdd_violation": MagicMock(return_value=None),
            "_enforce_convergence_backstop": MagicMock(),
            "_FILE_TOO_LARGE_LIMIT": 500,
            "_forever_restart": MagicMock(),
            "_generate_evolution_report": MagicMock(),
            "_git_commit": MagicMock(),
            "_is_self_evolving": MagicMock(return_value=False),
            "_parse_check_output": MagicMock(return_value=(True, 10, 1.0)),
            "_parse_report_summary": MagicMock(return_value={
                "improvements": 0, "bugs_fixed": 0, "tests_passing": 10,
            }),
            "_parse_restart_required": MagicMock(return_value=None),
            "_probe": MagicMock(),
            "_probe_ok": MagicMock(),
            "_probe_warn": MagicMock(),
            "_run_curation_pass": MagicMock(),
            "_run_party_mode": MagicMock(),
            "_run_spec_archival_pass": MagicMock(),
            "_runs_base": MagicMock(return_value=tmp_path),
            "_save_subprocess_diagnostic": MagicMock(),
            "_write_state_json": MagicMock(),
            "aggregate_usage": MagicMock(return_value=(None, None, [])),
            "build_usage_state": MagicMock(return_value={}),
            "fire_hook": MagicMock(),
            "format_cost": MagicMock(return_value="$0.00"),
            "get_tui": MagicMock(),
        }

        with patch.dict(
            "evolve.application.run_loop.__dict__", mock_imports
        ), patch(
            "evolve.application.run_loop_lifecycle._handle_round_success.__module__",
            create=True,
        ):
            # Re-import to pick up patched orchestrator
            import importlib
            import evolve.application.run_loop_lifecycle as rs_mod
            importlib.reload(rs_mod)

            ui = MagicMock()
            try:
                rs_mod._handle_round_success(
                    project_dir=tmp_path,
                    run_dir=run_dir,
                    improvements_path=imp_path,
                    ui=ui,
                    hooks={},
                    session_name="test",
                    round_num=1,
                    max_rounds=10,
                    started_at="2026-01-01T00:00:00Z",
                    rounds_start_time=0.0,
                    cmd=["pytest"],
                    output="all passed",
                    attempt=1,
                    spec=None,
                    capture_frames=False,
                    max_cost=None,
                    forever=False,
                    failure_signatures=[],
                )
            except SystemExit:
                pass  # convergence / budget may trigger exit

            # Verify _save_subprocess_diagnostic was called with LAYERING VIOLATION
            save_calls = mock_imports["_save_subprocess_diagnostic"].call_args_list
            layering_calls = [
                c for c in save_calls
                if "LAYERING VIOLATION" in str(c)
            ]
            assert len(layering_calls) >= 1, (
                f"Expected LAYERING VIOLATION diagnostic, got: {save_calls}"
            )

        # Restore module state: the reload() above created a new function
        # object in evolve.round_success, breaking the re-export identity
        # chain (orchestrator → round_lifecycle → round_success).  Reload
        # the full chain to restore identity invariants.
        import evolve.application.run_loop_lifecycle
        import evolve.application.run_loop_lifecycle
        import evolve.application.run_loop
        importlib.reload(evolve.round_success)
        importlib.reload(evolve.round_lifecycle)
        importlib.reload(evolve.application.run_loop)
