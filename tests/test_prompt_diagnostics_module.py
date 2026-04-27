"""Tests for the round-3 audit-fix split: ``evolve/prompt_diagnostics.py``.

Locks the four acceptance criteria for the agent.py split step 5
follow-up (Zara HIGH-1 review finding from session 20260427_203955
round 3 attempt 1: ``prompt_builder.py`` was 723 lines, > 500-line
SPEC § "Hard rule" cap).  The diagnostic-section helpers and their
constants now live in a sibling leaf module, with a 3-link re-export
chain ``agent`` → ``prompt_builder`` → ``prompt_diagnostics``
mirroring the ``agent`` → ``oneshot_agents`` → ``sync_readme`` chain
established in US-034.

(a) each of the diagnostic-section symbols is importable from
    ``evolve.prompt_diagnostics``;

(b) ``is``-equality holds across the 3-link chain
    (``evolve.agent.X`` is ``evolve.prompt_builder.X`` is
    ``evolve.prompt_diagnostics.X``) — the re-export chain preserves
    object identity, mirroring US-030/US-031/US-032/US-034 patterns;

(c) ``evolve/prompt_diagnostics.py`` source contains no ``from
    evolve.agent``, ``from evolve.orchestrator``, ``from evolve.cli``,
    ``from evolve.party``, or ``from evolve.prompt_builder``
    top-level imports — leaf-module invariant.  Function-local lazy
    imports inside indented bodies are fine and do NOT trip the
    leaf-invariant regex per memory.md round-7 lesson;

(d) existing tests that ``patch("evolve.agent.<X>")`` for any of the
    re-exported names continue to intercept calls (proves the 3-link
    re-export chain works); covered indirectly by the broader test
    suite — this module asserts the ``is``-identity contract that
    makes such patching reliable.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

import evolve.agent as agent_mod
import evolve.prompt_builder as pb_mod
import evolve.prompt_diagnostics as pd_mod


REPO_ROOT = Path(__file__).resolve().parent.parent


SYMBOLS = (
    "_PREV_ATTEMPT_LOG_FMT",
    "_MEMORY_WIPED_HEADER_FMT",
    "_PRIOR_ROUND_ANOMALY_PATTERNS",
    "_detect_prior_round_anomalies",
    "build_prev_crash_section",
    "build_prior_round_audit_section",
    "build_prev_attempt_section",
)


class TestSymbolsImportableFromPromptDiagnostics:
    """AC (a): every extracted symbol resolves on the new module."""

    @pytest.mark.parametrize("name", SYMBOLS)
    def test_symbol_resolves_on_prompt_diagnostics(self, name: str):
        assert hasattr(pd_mod, name), (
            f"evolve.prompt_diagnostics is missing {name!r} — round-3 "
            f"audit-fix split must hoist the symbol to the new module."
        )


class TestThreeLinkChainIdentity:
    """AC (b): re-export chain preserves object identity across all 3 modules.

    The 3-link chain ``agent`` → ``prompt_builder`` → ``prompt_diagnostics``
    must keep ``is``-equality at every hop, or ``patch("evolve.agent.X",
    ...)`` will fail to intercept call sites that resolve ``X`` through
    ``prompt_builder`` (for ``build_prompt_blocks``) or directly from
    ``prompt_diagnostics`` (for the helpers).  Same lesson as US-030 /
    US-031 / US-032 / US-034 / US-035: re-export at every link must
    bind the SAME object the source module defines.
    """

    @pytest.mark.parametrize("name", SYMBOLS)
    def test_agent_link_matches_diagnostics(self, name: str):
        agent_obj = getattr(agent_mod, name)
        pd_obj = getattr(pd_mod, name)
        assert agent_obj is pd_obj, (
            f"evolve.agent.{name} is NOT the same object as "
            f"evolve.prompt_diagnostics.{name} — 3-link re-export "
            f"chain broken at the agent link."
        )

    @pytest.mark.parametrize("name", SYMBOLS)
    def test_prompt_builder_link_matches_diagnostics(self, name: str):
        pb_obj = getattr(pb_mod, name)
        pd_obj = getattr(pd_mod, name)
        assert pb_obj is pd_obj, (
            f"evolve.prompt_builder.{name} is NOT the same object as "
            f"evolve.prompt_diagnostics.{name} — 3-link re-export "
            f"chain broken at the prompt_builder link."
        )


class TestLeafModuleInvariant:
    """AC (c): prompt_diagnostics.py imports no sibling-package modules at top-level.

    Function-local (indented) lazy imports inside function bodies are
    permitted and do NOT trip this check — memory.md round-7 lesson:
    ``indented imports do NOT trip the leaf-invariant regex
    ``^from evolve\\.``.

    This module is the leaf of the 3-link chain — it must NOT import
    from ``evolve.agent``, ``evolve.orchestrator``, ``evolve.cli``,
    ``evolve.party``, or even ``evolve.prompt_builder`` (which sits
    above it in the chain and would create a cycle).
    """

    def test_no_top_level_sibling_imports(self):
        src = (REPO_ROOT / "evolve" / "prompt_diagnostics.py").read_text()
        # Match top-of-line ``from evolve.X import`` for any X that
        # would create a chain or cycle.  Function-local imports start
        # with whitespace and are explicitly allowed.
        forbidden = re.findall(
            r"^from evolve\.(agent|orchestrator|cli|party|prompt_builder)( |$|\.)",
            src,
            re.MULTILINE,
        )
        assert not forbidden, (
            f"evolve/prompt_diagnostics.py has forbidden top-level imports: "
            f"{forbidden}.  Move them to function-local (indented) "
            f"lazy imports per memory.md round-7 leaf-invariant lesson."
        )


class TestSourceFileSizeUnderCap:
    """SPEC § "Hard rule" 500-line cap — both files in the split must hold.

    The whole point of the round-3 audit fix was to bring
    ``prompt_builder.py`` back under the 500-line cap.  This test
    locks both ``prompt_builder.py`` AND ``prompt_diagnostics.py``
    under the cap so a future refactor cannot silently re-cross the
    line by stuffing helpers back into either file.
    """

    @pytest.mark.parametrize(
        "rel_path",
        ["evolve/prompt_builder.py", "evolve/prompt_diagnostics.py"],
    )
    def test_file_under_500_lines(self, rel_path: str):
        src = (REPO_ROOT / rel_path).read_text()
        line_count = src.count("\n")
        assert line_count < 500, (
            f"{rel_path} has {line_count} lines — exceeds the SPEC § "
            f"'Hard rule: source files MUST NOT exceed 500 lines' cap. "
            f"Split further into a sibling leaf module per the "
            f"3-link re-export chain pattern."
        )


class TestHelperContracts:
    """Sanity: the diagnostic helpers retain their documented behaviour
    after the move — guards against any future refactor that silently
    swaps the dispatch order or drops a branch.
    """

    def test_build_prev_crash_section_empty_input_returns_empty(self):
        assert pd_mod.build_prev_crash_section("") == ""

    def test_build_prev_crash_section_memory_wiped_dispatch(self):
        out = pd_mod.build_prev_crash_section(
            "MEMORY WIPED: 2000\u21925 bytes\n\nrest of diagnostic"
        )
        # Branches into _MEMORY_WIPED_HEADER_FMT.
        assert "silently wiped memory.md" in out
        assert "MEMORY WIPED" in out

    def test_anomaly_patterns_table_is_nonempty(self):
        # The audit scan must have at least one pattern (otherwise
        # _detect_prior_round_anomalies cannot fire).
        assert len(pd_mod._PRIOR_ROUND_ANOMALY_PATTERNS) > 0
