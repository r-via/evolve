"""Tests for US-035: prompt-building extraction into evolve/prompt_builder.py.

Locks the four acceptance criteria for the agent.py split step 5:

(a) each of the four prompt-building symbols is importable from
    ``evolve.prompt_builder``;
(b) ``is``-equality holds between ``evolve.agent.X`` and
    ``evolve.prompt_builder.X`` (the re-export chain preserves
    object identity, mirroring US-030/US-031/US-032/US-034 patterns);
(c) ``evolve/prompt_builder.py`` source contains no ``from
    evolve.agent``, ``from evolve.orchestrator``, or ``from
    evolve.cli`` top-level imports — leaf-module invariant
    (function-local lazy imports inside indented bodies are fine and
    do NOT trip the leaf-invariant regex per memory.md round-7
    lesson);
(d) existing tests that ``patch("evolve.agent.build_prompt")``,
    ``patch("evolve.agent.build_prompt_blocks")``, or
    ``patch("evolve.agent._load_project_context")`` continue to
    intercept calls (proves the re-export chain works); covered
    indirectly by the broader test suite — this module asserts the
    `is`-identity contract that makes such patching reliable.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

import evolve.agent as agent_mod
import evolve.prompt_builder as pb_mod


REPO_ROOT = Path(__file__).resolve().parent.parent


SYMBOLS = (
    "_load_project_context",
    "_detect_prior_round_anomalies",
    "build_prompt_blocks",
    "build_prompt",
    "PromptBlocks",
    "_PREV_ATTEMPT_LOG_FMT",
    "_MEMORY_WIPED_HEADER_FMT",
    "_PRIOR_ROUND_ANOMALY_PATTERNS",
)


class TestSymbolsImportableFromPromptBuilder:
    """AC (a): every extracted symbol resolves on the new module."""

    @pytest.mark.parametrize("name", SYMBOLS)
    def test_symbol_resolves_on_prompt_builder(self, name: str):
        assert hasattr(pb_mod, name), (
            f"evolve.prompt_builder is missing {name!r} — extraction "
            f"per US-035 must hoist the symbol to the new module."
        )


class TestReExportIdentity:
    """AC (b): re-export preserves object identity (`is`-equality).

    Same lesson as US-030 / US-031 / US-032 / US-034: the re-export at
    ``evolve/agent.py`` top must bind the SAME object the source module
    defines.  ``patch("evolve.agent.X")`` then intercepts the bound name
    in agent.py's namespace, and any call site that resolves ``X``
    through ``agent_mod`` (including the lazy ``from evolve.agent
    import _detect_current_attempt`` pattern inside the extracted
    ``build_prompt_blocks``) sees the patched object.
    """

    @pytest.mark.parametrize("name", SYMBOLS)
    def test_agent_reexports_same_object(self, name: str):
        agent_obj = getattr(agent_mod, name)
        pb_obj = getattr(pb_mod, name)
        assert agent_obj is pb_obj, (
            f"evolve.agent.{name} is NOT the same object as "
            f"evolve.prompt_builder.{name} — re-export chain broken. "
            f"This breaks `patch('evolve.agent.{name}', ...)` test "
            f"interception (memory.md round-1-of-20260424_120253 "
            f"package-move + round-7 lazy-import lessons)."
        )


class TestLeafModuleInvariant:
    """AC (c): prompt_builder.py imports no sibling-package modules at top-level.

    Function-local (indented) lazy imports inside function bodies are
    permitted and do NOT trip this check — memory.md round-7 lesson:
    ``indented imports do NOT trip the leaf-invariant regex
    ``^from evolve\\.``.

    The leaf invariant prevents the round-6 ``20260427_114957``
    lazy-import trap pattern where a sibling-module top-level import
    creates an import cycle when the orchestrator's startup path
    walks the package.
    """

    def test_no_top_level_sibling_imports(self):
        src = (REPO_ROOT / "evolve" / "prompt_builder.py").read_text()
        # Match top-of-line `from evolve.X import` for X in {agent,
        # orchestrator, cli, party}.  Function-local imports start with
        # whitespace and are explicitly allowed.
        forbidden = re.findall(
            r"^from evolve\.(agent|orchestrator|cli|party)( |$|\.)",
            src,
            re.MULTILINE,
        )
        assert not forbidden, (
            f"evolve/prompt_builder.py has forbidden top-level imports: "
            f"{forbidden}.  Move them to function-local (indented) "
            f"lazy imports per memory.md round-7 leaf-invariant lesson."
        )


class TestPromptBlocksNamedTuple:
    """Sanity: the re-exported ``PromptBlocks`` is the same class with
    the documented field names — guards against any future refactor
    that silently swaps the namedtuple shape."""

    def test_namedtuple_fields(self):
        assert pb_mod.PromptBlocks._fields == ("cached", "uncached")

    def test_constructable_with_keyword_args(self):
        blocks = pb_mod.PromptBlocks(cached="a", uncached="b")
        assert blocks.cached == "a"
        assert blocks.uncached == "b"
