"""Tests for evolve.application layer — US-054.

Verifies all stubs are importable, raise NotImplementedError,
and application files contain only `from evolve.domain.` imports
(DDD dependency rule: application depends only on domain).
"""

from pathlib import Path

import pytest

from evolve.application.run_round import run_round
from evolve.application.run_loop import run_loop
from evolve.application.retry_policy import should_retry
from evolve.application.convergence_check import check_convergence
from evolve.application.draft_us import draft_us
from evolve.application.review_round import review_round
from evolve.application.analyze_and_fix import analyze_and_fix
from evolve.application.curate_memory import curate_memory
from evolve.application.archive_spec import archive_spec
from evolve.application.party_session import run_party_session
from evolve.application.dry_run import dry_run
from evolve.application.validate import validate
from evolve.application.sync_readme import sync_readme
from evolve.application.diff import diff
from evolve.application.update import update
from evolve.domain.round import RoundKind, RoundResult
from evolve.domain.convergence import ConvergenceGate
from evolve.domain.improvement import USItem
from evolve.domain.review_verdict import ReviewVerdict


# ── Importability & stub behavior ─────────────────────────────


class TestRunRound:
    def test_importable(self):
        assert callable(run_round)

    def test_raises_not_implemented(self):
        with pytest.raises(NotImplementedError, match="run_round stub"):
            run_round(round_num=1, kind=RoundKind.IMPLEMENT)


class TestRunLoop:
    def test_importable(self):
        assert callable(run_loop)

    def test_raises_not_implemented(self):
        with pytest.raises(NotImplementedError, match="run_loop stub"):
            run_loop()


class TestRetryPolicy:
    def test_importable(self):
        assert callable(should_retry)

    def test_raises_not_implemented(self):
        result = RoundResult(
            round_num=1, kind=RoundKind.IMPLEMENT, succeeded=False
        )
        with pytest.raises(NotImplementedError, match="should_retry stub"):
            should_retry(result, attempt=1)


class TestConvergenceCheck:
    def test_importable(self):
        assert callable(check_convergence)

    def test_raises_not_implemented(self):
        gates = [ConvergenceGate(name="spec_freshness", passed=True)]
        with pytest.raises(
            NotImplementedError, match="check_convergence stub"
        ):
            check_convergence(gates)


# ── Authoring-context stubs (US-060) ─────────────────────────


class TestDraftUs:
    def test_importable(self):
        assert callable(draft_us)

    def test_raises_not_implemented(self):
        with pytest.raises(NotImplementedError, match="draft_us stub"):
            draft_us()


class TestReviewRound:
    def test_importable(self):
        assert callable(review_round)

    def test_raises_not_implemented(self):
        with pytest.raises(NotImplementedError, match="review_round stub"):
            review_round(round_num=1)


class TestAnalyzeAndFix:
    def test_importable(self):
        assert callable(analyze_and_fix)

    def test_raises_not_implemented(self):
        with pytest.raises(NotImplementedError, match="analyze_and_fix stub"):
            analyze_and_fix(round_num=1)


# ── DDD dependency rule: application imports only from domain ──


class TestApplicationPurity:
    """Application files must contain only `from evolve.domain.` imports."""

    APP_DIR = Path(__file__).resolve().parent.parent / "evolve" / "application"

    def test_no_non_domain_evolve_imports(self):
        """Every `from evolve.` import must be `from evolve.domain.`."""
        violations = []
        for py_file in self.APP_DIR.glob("*.py"):
            source = py_file.read_text()
            for i, line in enumerate(source.splitlines(), 1):
                stripped = line.lstrip()
                # Skip comments
                if stripped.startswith("#"):
                    continue
                # Check for `from evolve.` that is NOT `from evolve.domain.`
                if stripped.startswith("from evolve.") and not stripped.startswith(
                    "from evolve.domain."
                ):
                    violations.append(f"{py_file.name}:{i}: {stripped}")
                # Check for bare `import evolve` (not `import evolve.domain`)
                if stripped.startswith("import evolve") and not stripped.startswith(
                    "import evolve.domain"
                ):
                    violations.append(f"{py_file.name}:{i}: {stripped}")
        assert not violations, (
            "Application files must only import from evolve.domain:\n"
            + "\n".join(violations)
        )

    def test_all_application_files_exist(self):
        expected = {
            "__init__.py",
            "run_round.py",
            "run_loop.py",
            "retry_policy.py",
            "convergence_check.py",
            "draft_us.py",
            "review_round.py",
            "analyze_and_fix.py",
            "curate_memory.py",
            "archive_spec.py",
            "party_session.py",
            "dry_run.py",
            "validate.py",
            "sync_readme.py",
            "diff.py",
            "update.py",
        }
        actual = {f.name for f in self.APP_DIR.glob("*.py")}
        assert expected.issubset(actual), (
            f"Missing application files: {expected - actual}"
        )


# ── Memory/SPEC-lifecycle stubs (US-061) ────────────────────


class TestCurateMemory:
    def test_importable(self):
        assert callable(curate_memory)

    def test_raises_not_implemented(self):
        with pytest.raises(NotImplementedError, match="curate_memory stub"):
            curate_memory()


class TestArchiveSpec:
    def test_importable(self):
        assert callable(archive_spec)

    def test_raises_not_implemented(self):
        with pytest.raises(NotImplementedError, match="archive_spec stub"):
            archive_spec()


# ── Party session stub (US-061) ─────────────────────────────


class TestPartySession:
    def test_importable(self):
        assert callable(run_party_session)

    def test_raises_not_implemented(self):
        with pytest.raises(
            NotImplementedError, match="run_party_session stub"
        ):
            run_party_session()


# ── One-shot use-case stubs (US-061) ────────────────────────


class TestDryRun:
    def test_importable(self):
        assert callable(dry_run)

    def test_raises_not_implemented(self):
        with pytest.raises(NotImplementedError, match="dry_run stub"):
            dry_run()


class TestValidate:
    def test_importable(self):
        assert callable(validate)

    def test_raises_not_implemented(self):
        with pytest.raises(NotImplementedError, match="validate stub"):
            validate()


class TestSyncReadme:
    def test_importable(self):
        assert callable(sync_readme)

    def test_raises_not_implemented(self):
        with pytest.raises(NotImplementedError, match="sync_readme stub"):
            sync_readme()


class TestDiff:
    def test_importable(self):
        assert callable(diff)

    def test_raises_not_implemented(self):
        with pytest.raises(NotImplementedError, match="diff stub"):
            diff()


class TestUpdate:
    def test_importable(self):
        assert callable(update)

    def test_raises_not_implemented(self):
        with pytest.raises(NotImplementedError, match="update stub"):
            update()
