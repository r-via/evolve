"""Tests for evolve.domain types — US-052 + US-053.

Verifies all domain types are importable, have correct fields,
enum members match SPEC names, and domain files contain zero
`from evolve.` imports (DDD dependency rule).
"""

from pathlib import Path

from evolve.domain.improvement import USItem, BacklogState, Backlog
from evolve.domain.round import RoundKind, RoundResult, RoundAttempt, Round
from evolve.domain.convergence import ConvergenceVerdict, ConvergenceGate
from evolve.domain.agent_invocation import AgentRole, AgentSubtype, AgentResult
from evolve.domain.review_verdict import ReviewVerdict, Finding
from evolve.domain.memory import MemoryEntry, MemoryLog, CompactionDecision
from evolve.domain.spec_compliance import SpecClaim, ClaimVerification


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


# ── US-059: Round and RoundAttempt ────────────────────────────


class TestRoundAttempt:
    def test_fields(self):
        attempt = RoundAttempt(
            attempt_num=1,
            subtype="error_max_turns",
            diagnostic="agent hit max turns",
            succeeded=False,
        )
        assert attempt.attempt_num == 1
        assert attempt.subtype == "error_max_turns"
        assert attempt.diagnostic == "agent hit max turns"
        assert attempt.succeeded is False

    def test_defaults(self):
        attempt = RoundAttempt(attempt_num=2)
        assert attempt.subtype is None
        assert attempt.diagnostic is None
        assert attempt.succeeded is False


class TestRound:
    def test_fields(self):
        attempt = RoundAttempt(attempt_num=1, succeeded=True)
        result = RoundResult(
            round_num=3, kind=RoundKind.IMPLEMENT, succeeded=True
        )
        rnd = Round(
            round_num=3,
            kind=RoundKind.IMPLEMENT,
            attempts=[attempt],
            result=result,
        )
        assert rnd.round_num == 3
        assert rnd.kind == RoundKind.IMPLEMENT
        assert len(rnd.attempts) == 1
        assert rnd.attempts[0] is attempt
        assert rnd.result is result

    def test_defaults(self):
        rnd = Round(round_num=1, kind=RoundKind.DRAFT)
        assert rnd.attempts == []
        assert rnd.result is None

    def test_multiple_attempts(self):
        a1 = RoundAttempt(attempt_num=1, succeeded=False, diagnostic="stall")
        a2 = RoundAttempt(attempt_num=2, succeeded=True)
        rnd = Round(
            round_num=5, kind=RoundKind.IMPLEMENT, attempts=[a1, a2]
        )
        assert len(rnd.attempts) == 2
        assert rnd.attempts[0].succeeded is False
        assert rnd.attempts[1].succeeded is True


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
        expected = {
            "__init__.py",
            "improvement.py",
            "round.py",
            "convergence.py",
            "agent_invocation.py",
            "review_verdict.py",
            "memory.py",
            "spec_compliance.py",
        }
        actual = {f.name for f in self.DOMAIN_DIR.glob("*.py")}
        assert expected.issubset(actual), (
            f"Missing domain files: {expected - actual}"
        )


# ── US-053: agent_invocation types ────────────────────────────


class TestAgentRole:
    def test_members(self):
        assert AgentRole.DRAFT.value == "draft"
        assert AgentRole.IMPLEMENT.value == "implement"
        assert AgentRole.REVIEW.value == "review"
        assert AgentRole.CURATE.value == "curate"
        assert AgentRole.ARCHIVE.value == "archive"

    def test_member_count(self):
        assert len(AgentRole) == 5


class TestAgentSubtype:
    def test_members(self):
        assert AgentSubtype.SUCCESS.value == "success"
        assert AgentSubtype.ERROR_MAX_TURNS.value == "error_max_turns"
        assert AgentSubtype.ERROR_DURING_EXECUTION.value == "error_during_execution"

    def test_member_count(self):
        assert len(AgentSubtype) == 3


class TestAgentResult:
    def test_fields(self):
        result = AgentResult(
            role=AgentRole.IMPLEMENT,
            subtype=AgentSubtype.SUCCESS,
            num_turns=40,
            duration_ms=12345,
        )
        assert result.role == AgentRole.IMPLEMENT
        assert result.subtype == AgentSubtype.SUCCESS
        assert result.num_turns == 40
        assert result.duration_ms == 12345

    def test_defaults(self):
        result = AgentResult(
            role=AgentRole.DRAFT,
            subtype=AgentSubtype.ERROR_MAX_TURNS,
            num_turns=60,
        )
        assert result.duration_ms is None


# ── US-053: review_verdict types ──────────────────────────────


class TestReviewVerdict:
    def test_members(self):
        assert ReviewVerdict.APPROVED.value == "approved"
        assert ReviewVerdict.CHANGES_REQUESTED.value == "changes_requested"
        assert ReviewVerdict.BLOCKED.value == "blocked"

    def test_member_count(self):
        assert len(ReviewVerdict) == 3


class TestFinding:
    def test_fields(self):
        finding = Finding(
            severity="HIGH",
            description="Missing test",
            file_path="evolve/agent.py",
            line=42,
        )
        assert finding.severity == "HIGH"
        assert finding.description == "Missing test"
        assert finding.file_path == "evolve/agent.py"
        assert finding.line == 42

    def test_defaults(self):
        finding = Finding(severity="LOW", description="Minor style")
        assert finding.file_path is None
        assert finding.line is None


# ── US-053: memory types ──────────────────────────────────────


class TestMemoryEntry:
    def test_fields(self):
        entry = MemoryEntry(
            section="Errors",
            title="SDK crash",
            round_ref="round 5",
            body="SDK threw RuntimeError on teardown",
        )
        assert entry.section == "Errors"
        assert entry.title == "SDK crash"
        assert entry.round_ref == "round 5"
        assert entry.body == "SDK threw RuntimeError on teardown"

    def test_defaults(self):
        entry = MemoryEntry(section="Insights", title="Cache hit")
        assert entry.round_ref is None
        assert entry.body == ""


class TestCompactionDecision:
    def test_members(self):
        assert CompactionDecision.KEEP.value == "keep"
        assert CompactionDecision.ARCHIVE.value == "archive"
        assert CompactionDecision.DELETE.value == "delete"

    def test_member_count(self):
        assert len(CompactionDecision) == 3


class TestMemoryLog:
    def test_defaults(self):
        log = MemoryLog()
        assert log.entries == []

    def test_with_entries(self):
        entry = MemoryEntry(section="Errors", title="Crash")
        log = MemoryLog(entries=[entry])
        assert len(log.entries) == 1
        assert log.entries[0].title == "Crash"


# ── US-053: spec_compliance types ─────────────────────────────


class TestSpecClaim:
    def test_fields(self):
        claim = SpecClaim(
            section="CLI flags",
            description="--check flag exists",
            implemented=True,
        )
        assert claim.section == "CLI flags"
        assert claim.description == "--check flag exists"
        assert claim.implemented is True

    def test_defaults(self):
        claim = SpecClaim(section="TUI", description="Rich panels")
        assert claim.implemented is False


class TestClaimVerification:
    def test_fields(self):
        claim = SpecClaim(section="Git", description="Conventional commits")
        verification = ClaimVerification(
            claim=claim,
            evidence="git log shows feat/fix prefixes",
            passed=True,
        )
        assert verification.claim is claim
        assert verification.evidence == "git log shows feat/fix prefixes"
        assert verification.passed is True

    def test_defaults(self):
        claim = SpecClaim(section="Hooks", description="on_error fires")
        verification = ClaimVerification(claim=claim)
        assert verification.evidence is None
        assert verification.passed is False
