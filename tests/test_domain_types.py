"""Tests for evolve.domain types — US-052.

Verifies all domain types are importable, have correct fields,
enum members match SPEC names, and domain files contain zero
`from evolve.` imports (DDD dependency rule).
"""

from pathlib import Path

from evolve.domain.improvement import USItem, BacklogState, Backlog
from evolve.domain.round import RoundKind, RoundResult
from evolve.domain.convergence import ConvergenceVerdict, ConvergenceGate


# ── Importability & field presence ──────────────────────────────


class TestUSItem:
    def test_fields(self):
        item = USItem(
            id="US-001",
            summary="Test item",
            type_tag="functional",
            priority="P2",
            checked=False,
            acceptance_criteria=["AC1", "AC2"],
        )
        assert item.id == "US-001"
        assert item.summary == "Test item"
        assert item.type_tag == "functional"
        assert item.priority == "P2"
        assert item.checked is False
        assert item.acceptance_criteria == ["AC1", "AC2"]
        assert item.needs_package is False
        assert item.blocked is False
        assert item.blocked_reason is None

    def test_defaults(self):
        item = USItem(
            id="US-002",
            summary="Minimal",
            type_tag="performance",
            priority="P1",
            checked=True,
        )
        assert item.acceptance_criteria == []
        assert item.needs_package is False


class TestBacklogState:
    def test_fields(self):
        state = BacklogState(pending=3, done=10, blocked=1)
        assert state.pending == 3
        assert state.done == 10
        assert state.blocked == 1


class TestBacklog:
    def test_defaults(self):
        backlog = Backlog()
        assert backlog.items == []
        assert backlog.state.pending == 0
        assert backlog.state.done == 0
        assert backlog.state.blocked == 0

    def test_with_items(self):
        item = USItem(
            id="US-001",
            summary="X",
            type_tag="functional",
            priority="P2",
            checked=False,
        )
        state = BacklogState(pending=1, done=0, blocked=0)
        backlog = Backlog(items=[item], state=state)
        assert len(backlog.items) == 1
        assert backlog.state.pending == 1


# ── Enum members match SPEC names ──────────────────────────────


class TestRoundKind:
    def test_members(self):
        assert RoundKind.DRAFT.value == "draft"
        assert RoundKind.IMPLEMENT.value == "implement"
        assert RoundKind.REVIEW.value == "review"

    def test_member_count(self):
        assert len(RoundKind) == 3


class TestRoundResult:
    def test_fields(self):
        result = RoundResult(
            round_num=1,
            kind=RoundKind.IMPLEMENT,
            succeeded=True,
            subtype="success",
            num_turns=40,
        )
        assert result.round_num == 1
        assert result.kind == RoundKind.IMPLEMENT
        assert result.succeeded is True
        assert result.subtype == "success"
        assert result.num_turns == 40

    def test_defaults(self):
        result = RoundResult(
            round_num=2, kind=RoundKind.DRAFT, succeeded=False
        )
        assert result.subtype is None
        assert result.num_turns is None


class TestConvergenceVerdict:
    def test_members(self):
        assert ConvergenceVerdict.CONVERGED.value == "converged"
        assert ConvergenceVerdict.NOT_CONVERGED.value == "not_converged"

    def test_member_count(self):
        assert len(ConvergenceVerdict) == 2


class TestConvergenceGate:
    def test_fields(self):
        gate = ConvergenceGate(
            name="spec_freshness", passed=True, reason="mtime OK"
        )
        assert gate.name == "spec_freshness"
        assert gate.passed is True
        assert gate.reason == "mtime OK"

    def test_defaults(self):
        gate = ConvergenceGate(name="backlog", passed=False)
        assert gate.reason is None


# ── DDD dependency rule: domain imports nothing from evolve ─────


class TestDomainPurity:
    """Domain files must contain zero `from evolve.` imports."""

    DOMAIN_DIR = Path(__file__).resolve().parent.parent / "evolve" / "domain"

    def test_no_evolve_imports(self):
        violations = []
        for py_file in self.DOMAIN_DIR.glob("*.py"):
            source = py_file.read_text()
            for i, line in enumerate(source.splitlines(), 1):
                stripped = line.lstrip()
                if stripped.startswith("from evolve.") or (
                    stripped.startswith("import evolve")
                ):
                    violations.append(f"{py_file.name}:{i}: {stripped}")
        assert not violations, (
            "Domain files must not import from evolve:\n"
            + "\n".join(violations)
        )

    def test_all_domain_files_exist(self):
        expected = {"__init__.py", "improvement.py", "round.py", "convergence.py"}
        actual = {f.name for f in self.DOMAIN_DIR.glob("*.py")}
        assert expected.issubset(actual), (
            f"Missing domain files: {expected - actual}"
        )
